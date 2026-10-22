"""Frozen public-fund request policy; no change to paid-source rate limits.

The deadline covers async transport, response bodies, retry sleeps and in-flight
cancellation. Cache reads, validation and publication remain measured local work,
not a promise that the entire supplement job finishes within sixty seconds.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import math
import time

import httpx

from app.adapters.providers import ProviderError

FUND_REQUEST_BUDGET_VERSION = 'public-fund-budget-v1'


def validate_fund_budget_version(value):
    if value is not None and value != FUND_REQUEST_BUDGET_VERSION:
        raise ValueError('资金流请求预算版本未支持，请保留原任务或明确新建')
    return value


class FundSourceError(ProviderError):
    def __init__(self, message, kind, *, retryable=False, status=None):
        super().__init__(message, status=status)
        self.kind = kind
        self.retryable = retryable


@dataclass
class FundRequestBudget:
    max_logical_requests: int = 200
    max_transport_attempts: int = 600
    deadline_seconds: float = 60
    clock: object = time.monotonic
    started_at: float = field(init=False)
    logical_request_count: int = 0
    actual_transport_attempt_count: int = 0
    circuit_open: bool = False
    failure_kind: str | None = None
    consecutive_failures: int = 0
    stop_reason: str | None = None
    peak_inflight_requests: int = 0

    def __post_init__(self):
        if (type(self.max_logical_requests) is not int or type(self.max_transport_attempts) is not int
                or not 2 <= self.max_logical_requests <= 200 or not 1 <= self.max_transport_attempts <= 600):
            raise ValueError('资金流请求预算超出已冻结上限')
        if (isinstance(self.deadline_seconds, bool) or not isinstance(self.deadline_seconds, (int, float))
                or not math.isfinite(self.deadline_seconds) or not 0 < self.deadline_seconds <= 60):
            raise ValueError('资金流网络阶段截止时间应大于0且不超过60秒')
        self.started_at = self.clock()

    def remaining(self):
        return max(0, self.started_at + self.deadline_seconds - self.clock())

    def available(self):
        if self.circuit_open:
            self.stop_reason = 'source_circuit_open'
        elif self.remaining() <= 0:
            self.stop_reason = 'request_deadline'
        elif self.logical_request_count >= self.max_logical_requests:
            self.stop_reason = 'request_budget'
        elif self.actual_transport_attempt_count >= self.max_transport_attempts:
            self.stop_reason = 'transport_budget'
        return self.stop_reason is None

    def begin_transport(self, *, new_logical=False):
        # The final logical call is allowed its remaining transport attempts.
        if new_logical and not self.available():
            raise FundSourceError('资金流请求预算或截止时间已到，保留未知', self.stop_reason)
        if self.circuit_open or self.remaining() <= 0 or self.actual_transport_attempt_count >= self.max_transport_attempts:
            reason = 'source_circuit_open' if self.circuit_open else 'request_deadline' if self.remaining() <= 0 else 'transport_budget'
            self.stop_reason = reason
            raise FundSourceError('资金流传输预算或截止时间已到，保留未知', reason)
        if new_logical:
            self.logical_request_count += 1
        self.actual_transport_attempt_count += 1

    def outcome(self, kind=None):
        if kind not in ('connection', 'schema', 'http_5xx', 'rate_limited', 'http_denied'):
            self.failure_kind = None
            self.consecutive_failures = 0
            return
        self.consecutive_failures = self.consecutive_failures + 1 if kind == self.failure_kind else 1
        self.failure_kind = kind
        if self.consecutive_failures >= 5:
            self.circuit_open = True
            self.stop_reason = 'source_circuit_open'

    def snapshot(self):
        return {'request_budget_version': FUND_REQUEST_BUDGET_VERSION,
                'logical_request_count': self.logical_request_count,
                'actual_transport_attempt_count': self.actual_transport_attempt_count,
                'max_logical_requests': self.max_logical_requests,
                'max_transport_attempts': self.max_transport_attempts,
                'fault_probe_target_max_logical_requests': 10,
                'fault_probe_target_max_transport_attempts': 30,
                'request_deadline_seconds': self.deadline_seconds,
                'request_deadline_scope': 'async transport, response body, retry waits and in-flight cancellation; local cache/validation/publication measured separately',
                'whole_phase_hard_deadline_guaranteed': False,
                'deadline_limitations': 'Local synchronous cache/validation/publication and platform DNS executor shutdown can exceed the network cancellation target; elapsed time is measured, not capped or fabricated',
                'elapsed_seconds': max(0, self.clock() - self.started_at),
                'source_circuit_open': self.circuit_open,
                'source_failure_kind': self.failure_kind,
                'consecutive_source_failures': self.consecutive_failures,
                'stop_reason': self.stop_reason,
                'peak_inflight_requests': self.peak_inflight_requests}


async def _retry_wait(seconds):
    await asyncio.sleep(seconds)


async def fund_response(connection, budget, url, *, params=None, html=False, on_transport=None):
    """One logical request, counted only when its first transport is started."""
    try:
        async with asyncio.timeout(budget.remaining()):
            for attempt in range(3):
                budget.begin_transport(new_logical=attempt == 0)
                if on_transport: on_transport()
                try:
                    async with connection.stream('GET', url, params=params,
                            headers={'User-Agent': 'Mozilla/5.0 ShixuWorkbench/0.1' if html else 'Mozilla/5.0', 'Accept': 'text/html' if html else 'application/json'}) as response:
                        status = response.status_code
                        if status != 200:
                            kind = 'rate_limited' if status == 429 else 'http_5xx' if status >= 500 else 'http_denied'
                            error = FundSourceError('公开资金来源返回HTTP ' + str(status), kind, retryable=status == 429 or status >= 500, status=status)
                            if error.retryable and attempt < 2:
                                await _retry_wait(2 ** attempt)
                                continue
                            raise error
                        chunks = []; size = 0
                        async for block in response.aiter_bytes():
                            size += len(block)
                            if size > (4 if html else 8) * 1024 ** 2:
                                raise FundSourceError('公开资金来源响应超过容量上限', 'schema')
                            chunks.append(block)
                    try:
                        body = b''.join(chunks)
                        if html:
                            return body.decode('utf-8', errors='strict')
                        result = json.loads(body)
                    except (ValueError, UnicodeError):
                        raise FundSourceError('公开资金来源响应编码或结构变化', 'schema') from None
                    if not isinstance(result, dict) or result.get('rc') not in (0, None) or not isinstance(result.get('data'), dict):
                        raise FundSourceError('公开资金来源业务响应或数据结构未通过', 'schema')
                    return result
                except httpx.HTTPError:
                    if attempt == 2:
                        raise FundSourceError('公开资金来源连接失败，已有限重试；保留缺口', 'connection', retryable=True) from None
                    await _retry_wait(2 ** attempt)
    except TimeoutError:
        budget.stop_reason = 'request_deadline'
        raise FundSourceError('公开资金来源网络阶段达到截止时间，已取消在途请求；保留未知', 'request_deadline') from None

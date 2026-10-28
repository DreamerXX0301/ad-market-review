"""有界的竞价时点取证；原件嵌入业务快照而不写活动行情库。"""
from datetime import datetime, time
from hashlib import sha256
from zoneinfo import ZoneInfo
import re

from app.adapters.providers import ProviderError
from app.services.noon_collection import canonical, contains_secret

CHINA = ZoneInfo('Asia/Shanghai')
VERSION = 'auction-snapshot-v1'
CAPTURE_START = time(9, 25)
CAPTURE_END = time(9, 30)
STOCK_CODE = re.compile(r'[0-9]{6}\.(?:SH|SZ|BJ)\Z')
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_EVIDENCE_BYTES = 2 * 1024 * 1024


class WindowExpired(ValueError):
    pass


def in_window(day, now):
    local = now.astimezone(CHINA)
    return local.date().isoformat() == day and local.weekday() < 5 and CAPTURE_START <= local.time().replace(tzinfo=None) < CAPTURE_END


class Collector:
    """最多三个逻辑请求，凭据回显与窗口外响应不进入证据。"""
    def __init__(self, day, adapter, guard, now, callback=None):
        self.day, self.adapter, self.guard, self.now, self.callback = day, adapter, guard, now, callback
        self.started_at = now().isoformat()
        self.last_accepted_at = self.started_at
        self.evidence, self.sources, self.gaps = [], [], []
        self.evidence_bytes = 0

    def gap(self, domain, reason):
        item = {'domain': domain, 'reason': reason}
        if item not in self.gaps:
            self.gaps.append(item)

    def request(self, name, endpoint, params=None):
        self.guard()
        if not in_window(self.day, self.now()):
            raise WindowExpired('竞价采集窗口已结束，不请求或补造盘前数据')
        if len(self.sources) >= 3:
            raise ValueError('竞价逻辑请求超过冻结预算')
        params = params or {}
        record = {'name': name, 'source': 'fuyao', 'endpoint': endpoint, 'params': params,
                  'requested_at': self.now().isoformat()}
        self.sources.append(record)
        try:
            def before_attempt():
                self.guard()
                if not in_window(self.day, self.now()):
                    raise WindowExpired('竞价采集已过09:30，不再请求或重试来源')
            body = self.adapter.request(endpoint, params, before_attempt=before_attempt)
            self.guard()
            received = self.now()
            record['received_at'] = received.isoformat()
            if not in_window(self.day, received):
                record['state'] = 'outside_window'
                raise WindowExpired('响应到达时已过09:30，未纳入竞价快照')
            if contains_secret(body, getattr(self.adapter, 'key', None)):
                record['state'] = 'credential_echo_rejected'
                self.gap(name, '来源响应回显凭据，未保存该响应或报价')
                return None
            if not isinstance(body, dict) or not isinstance(body.get('data'), dict):
                raise ValueError('来源响应结构无效')
            raw = canonical(body)
            if len(raw) > MAX_RESPONSE_BYTES or self.evidence_bytes + len(raw) > MAX_EVIDENCE_BYTES:
                record['state'] = 'capacity_rejected'
                self.gap(name, '来源原件超过单份1MiB或总2MiB预算，未保存该响应或报价')
                return None
            record.update(state='received', bytes=len(raw), sha256=sha256(raw).hexdigest())
            self.evidence.append({**record, 'body': body})
            self.evidence_bytes += len(raw)
            self.last_accepted_at = received.isoformat()
            if self.callback:
                self.callback('正在核验盘前竞价', min(85, 10 + len(self.sources) * 25))
            return body
        except ProviderError as error:
            record.update(state='failed', http_status=error.status if isinstance(error.status, int) else None,
                          code=error.code if isinstance(error.code, int) else None)
            self.gap(name, '来源请求失败或权限不足，数据保持未知')
            return None
        except WindowExpired:
            raise
        except (ValueError, TypeError, OverflowError):
            record['state'] = 'invalid_response'
            self.gap(name, '来源结构、数值或容量未通过核验，数据保持未知')
            return None
        finally:
            record.setdefault('received_at', self.now().isoformat())

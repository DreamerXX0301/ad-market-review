#!/usr/bin/env python3
"""Manually inspect dated outlooks through explicit HTTP GET requests only.

The fixed 1/3/5/10-session observations describe later breadth and gate states.
They do not evaluate trade profits or convert a candidate into a proven bottom.
No application modules, local market files or business databases are opened.
"""
import argparse
from collections import Counter
from datetime import date, datetime, timezone
import json
import math
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


VERSION = 'market-outlook-http-observation-v1'
HORIZONS = (1, 3, 5, 10)
MAX_SEED_DATES = 30


def dated(value):
    try:
        return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def api_root(value):
    parsed = urlsplit(value)
    if (parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.username
            or parsed.password or parsed.query or parsed.fragment
            or parsed.path.rstrip('/') not in ('', '/api/v1')):
        raise ValueError('--base-url 须为明确的HTTP(S)服务地址，可带/api/v1，不含凭据、查询或其他路径')
    return value.rstrip('/') if parsed.path.rstrip('/') else value.rstrip('/') + '/api/v1'


def read_endpoint(root, day, timeout):
    request = Request(root + '/market/emotion?' + urlencode({'date': day}),
                      headers={'Accept': 'application/json'}, method='GET')
    with urlopen(request, timeout=timeout) as response:
        # Bounded response size; never accept an arbitrarily large export.
        body = response.read(8 * 1024 * 1024 + 1)
    if len(body) > 8 * 1024 * 1024:
        raise ValueError('情绪接口响应超过8MiB核验上限')
    payload = json.loads(body)
    if not isinstance(payload, dict):
        raise ValueError('情绪接口未返回对象')
    return payload


def verified_payload(payload, day):
    """A response for a nearby fallback date is never a valid observation."""
    outlook = payload.get('market_outlook') if isinstance(payload, dict) else None
    if not isinstance(outlook, dict) or payload.get('date') != day or outlook.get('date') != day:
        return None, 'endpoint_date_mismatch'
    generation = payload.get('snapshot_id')
    if not isinstance(generation, str) or not generation or outlook.get('snapshot_id') != generation:
        return None, 'endpoint_generation_unverified'
    if outlook.get('version') != 'dated-market-outlook-v1':
        return None, 'endpoint_rule_version_unverified'
    insights = outlook.get('insights', {})
    if (not isinstance(insights, dict)
            or insights.get('version') != 'market-opportunity-insights-v1'
            or insights.get('date') != day or insights.get('snapshot_id') != generation
            or not isinstance(insights.get('candidate_tracking'), dict)
            or insights['candidate_tracking'].get('as_of') != day):
        return None, 'endpoint_insights_unverified'
    if any(not isinstance(outlook.get(key), dict) for key in
           ('temperature', 'stage', 'entry_gate', 'next_session')):
        return None, 'endpoint_shape_unverified'
    if not isinstance(insights.get('routes'), list):
        return None, 'endpoint_shape_unverified'
    return outlook, None


def observable(outlook):
    temperature = outlook.get('temperature', {})
    return (finite(temperature.get('value')) and finite(temperature.get('coverage_pct'))
            and temperature['coverage_pct'] >= 80
            and temperature.get('calendar_valid') is True
            and temperature.get('evidence_consistent') is True
            and outlook.get('stage', {}).get('status') == 'observation')


def summarize(outlook):
    tracking = outlook.get('insights', {}).get('candidate_tracking', {})
    return {'date': outlook['date'], 'stage': outlook.get('stage', {}).get('code'),
            'temperature': outlook.get('temperature', {}).get('value'),
            'coverage_pct': outlook.get('temperature', {}).get('coverage_pct'),
            'entry_gate': outlook.get('entry_gate', {}).get('status'),
            'routes': {row.get('id'): row.get('status') for row in outlook.get('insights', {}).get('routes', [])
                       if isinstance(row, dict)},
            'candidate_date': tracking.get('candidate_date'), 'candidate_status': tracking.get('status'),
            'candidate_confirmed': tracking.get('confirmed')}


def research(seed_dates, fetch, *, max_requests=120):
    """Follow only the exact next-session date supplied by each verified GET."""
    cache = {}
    requests = 0

    def load(day):
        nonlocal requests
        if day not in cache:
            if requests >= max_requests:
                return None, 'request_budget_exhausted'
            requests += 1
            try:
                cache[day] = fetch(day)
            except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
                cache[day] = None
        if cache[day] is None:
            return None, 'endpoint_unavailable'
        return verified_payload(cache[day], day)

    samples = []
    for seed in seed_dates:
        initial, error = load(seed)
        sample = {'date': seed, 'snapshot_id': None, 'observation': None, 'horizons': {}}
        if initial is not None:
            sample['snapshot_id'] = initial['snapshot_id']
            sample['observation'] = summarize(initial)
        path = [initial] if initial is not None else []
        failure = error
        for step in range(1, max(HORIZONS) + 1):
            if failure is None:
                next_day = path[-1].get('next_session', {}).get('date')
                if not dated(next_day) or next_day <= path[-1]['date']:
                    failure = 'next_market_session_unknown'
                else:
                    later, failure = load(next_day)
                    if later is not None and later['snapshot_id'] != initial['snapshot_id']:
                        failure = 'market_generation_changed'
                    if failure is None:
                        path.append(later)
            if step not in HORIZONS:
                continue
            complete = failure is None and len(path) == step + 1 and all(observable(row) for row in path)
            reason = failure or (None if complete else 'breadth_evidence_incomplete')
            if not complete:
                sample['horizons'][str(step)] = {
                    'sessions': step, 'state': 'unknown', 'reason': reason,
                    'observed_dates': [row['date'] for row in path], 'endpoint': None,
                    'temperature_change': None, 'strong_breadth_sessions': None,
                    'blocked_sessions': None}
            else:
                later = path[-1]
                sample['horizons'][str(step)] = {
                    'sessions': step, 'state': 'observed', 'reason': None,
                    'observed_dates': [row['date'] for row in path], 'endpoint': summarize(later),
                    'temperature_change': later['temperature']['value'] - initial['temperature']['value'],
                    'strong_breadth_sessions': sum(row['temperature']['value'] >= 60 for row in path[1:]),
                    'blocked_sessions': sum(row.get('entry_gate', {}).get('status') == 'blocked' for row in path[1:])}
        samples.append(sample)
    counts = {str(horizon): dict(Counter(sample['horizons'][str(horizon)]['state'] for sample in samples))
              for horizon in HORIZONS}
    return {'version': VERSION, 'mode': 'explicit_read_only_http', 'horizons': list(HORIZONS),
            'seed_dates': list(seed_dates), 'request_count': requests, 'request_budget': max_requests,
            'summary': counts, 'samples': samples,
            'notice': '当前留存回看；仅观察后续广度、闸门与候选状态。未知不补零，不是收益回测、交易胜率或已确认底部。'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True, help='已运行服务的明确HTTP(S)地址；本工具不会启动服务')
    parser.add_argument('--dates', required=True, nargs='+', help='手动选定的观察日，YYYY-MM-DD，最多30日')
    parser.add_argument('--max-requests', type=int, default=120, help='GET总次数上限，1—500，默认120')
    parser.add_argument('--timeout', type=float, default=60, help='单次GET超时秒数，默认60')
    parser.add_argument('--output', type=Path, help='可选的私有JSON回执路径；不提供时只输出到stdout')
    args = parser.parse_args(argv)
    dates = list(dict.fromkeys(args.dates))
    if not dates or len(dates) > MAX_SEED_DATES or any(not dated(day) for day in dates):
        parser.error('--dates 须为最多30个明确的YYYY-MM-DD日期')
    if not 1 <= args.max_requests <= 500 or not 0 < args.timeout <= 120:
        parser.error('请求上限须为1—500，超时须大于0且不超过120秒')
    try:
        root = api_root(args.base_url)
    except ValueError as exc:
        parser.error(str(exc))
    report = research(dates, lambda day: read_endpoint(root, day, args.timeout), max_requests=args.max_requests)
    report['computed_at'] = datetime.now(timezone.utc).isoformat()
    encoded = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    if args.output:
        # Exclusive creation prevents replacing a prior research receipt.
        with args.output.open('x', encoding='utf-8') as stream:
            stream.write(encoded)
        print(f'已写入只读观察回执：{args.output}')
    else:
        sys.stdout.write(encoded)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

"""手动核验扶摇盘中能力；仅 GET，不创建任务、打开业务库或发布行情。

在仓库根目录运行：
  backend/.venv/bin/python scripts/probe_intraday_sources.py \
    --output data/validation/intraday-<本次唯一名称> --full-market

输出目录必须不存在。真实响应与回执仅用于私有验收，不应加入 Git。
退出码 0 仅表示本次探测完成，不代表所有能力通过或产品已支持午评。
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, time as day_time, timedelta
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import sys
import time
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from app.adapters.providers import ProviderClient, ProviderError, provider_connection_scope
from app.infrastructure.credentials import CredentialStore

CHINA = ZoneInfo('Asia/Shanghai')
SYMBOL = re.compile(r'\d{6}\.(?:SH|SZ|BJ|TI)\Z')
PRICE_FIELDS = ('last_price', 'prev_price', 'open_price', 'high_price', 'low_price',
                'price_change', 'price_change_ratio_pct', 'volume', 'turnover')
ITEM_TIME_FIELDS = ('timestamp', 'quote_timestamp', 'quote_time', 'updated_at',
                    'source_updated_at', 'trade_time', 'datetime', 'date_ms')
SAMPLES = {
    'stocks': ('/api/a-share/prices/snapshot', ('600519.SH', '000001.SZ', '300750.SZ')),
    'indices': ('/api/a-share-index/prices/snapshot', ('000001.SH', '399001.SZ', '399006.SZ', '000688.SH')),
    'sectors': ('/api/a-share-index/prices/snapshot', ('881101.TI', '886042.TI')),
}


def china_now():
    return datetime.now(CHINA)


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def contains_secret(value, secret):
    """序列化前检查原字符串，避免JSON转义掩盖响应中回显的凭据。"""
    if not secret:
        return False
    if isinstance(value, str):
        return secret in value
    if isinstance(value, dict):
        return any(contains_secret(key, secret) or contains_secret(item, secret) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(contains_secret(item, secret) for item in value)
    return False


def timestamp_state(value, target, received):
    """只分类响应总时间，不据此推定逐条报价时间。"""
    if not finite(value) or value <= 0:
        return {'value_ms': None, 'time': None, 'state': 'missing_or_invalid'}
    try:
        actual = datetime.fromtimestamp(value / 1000, CHINA)
    except (OverflowError, OSError, ValueError):
        return {'value_ms': value, 'time': None, 'state': 'invalid'}
    state = 'target_date' if actual.date().isoformat() == target else 'different_date'
    if actual > received + timedelta(seconds=5):
        state = 'future'
    return {'value_ms': value, 'time': actual.isoformat(), 'state': state}


def inspect_quotes(payload, target, received, expected=None):
    data = payload.get('data') if isinstance(payload, dict) else None
    if not isinstance(data, dict) or not isinstance(data.get('item'), list):
        return {'state': 'unknown', 'reason': '快照结构不符', 'quote_as_of': None}
    rows = data['item']
    objects = [row for row in rows if isinstance(row, dict)]
    codes = [row.get('thscode') for row in objects]
    valid_codes = [code for code in codes if isinstance(code, str) and SYMBOL.fullmatch(code)]
    duplicate_codes = sorted(code for code, count in Counter(valid_codes).items() if count > 1)
    invalid_identity = len(rows) - len(valid_codes)
    fields = {field: sum(finite(row.get(field)) for row in objects) for field in PRICE_FIELDS}
    positive_prices = sum(finite(row.get('last_price')) and row['last_price'] > 0 for row in objects)
    fields_present = sorted(set().union(*(row.keys() for row in objects))) if objects else []
    observed_time_fields = sorted(set(fields_present).intersection(ITEM_TIME_FIELDS))
    timed_rows = sum(any(row.get(field) is not None for field in ITEM_TIME_FIELDS) for row in objects)
    price_mismatches = 0
    amount_mismatches = 0
    ohlc_mismatches = 0
    invalid_value_rows = 0
    for row in objects:
        invalid_value_rows += any(finite(row.get(field)) and row[field] < 0 for field in ('volume', 'turnover')) or any(
            finite(row.get(field)) and row[field] <= 0 for field in ('last_price', 'prev_price', 'open_price', 'high_price', 'low_price'))
        price, previous, change = (row.get(field) for field in ('last_price', 'prev_price', 'price_change_ratio_pct'))
        if finite(price) and price > 0 and finite(previous) and previous > 0 and finite(change):
            price_mismatches += abs((price / previous - 1) * 100 - change) > .001
        amount_change = row.get('price_change')
        if all(finite(value) for value in (price, previous, amount_change)):
            amount_mismatches += abs(price - previous - amount_change) > .011
        opening, high, low = (row.get(field) for field in ('open_price', 'high_price', 'low_price'))
        if all(finite(value) for value in (price, opening, high, low)):
            ohlc_mismatches += high + .011 < low or any(value < low - .011 or value > high + .011 for value in (price, opening))
    missing = sorted(set(expected or ()) - set(valid_codes))
    unexpected = sorted(set(valid_codes) - set(expected)) if expected is not None else []
    source_stamp = timestamp_state(data.get('timestamp'), target, received)
    partial = bool(invalid_identity or duplicate_codes or missing or unexpected or price_mismatches or amount_mismatches or
                   ohlc_mismatches or invalid_value_rows or
                   positive_prices != len(rows) or any(count != len(rows) for count in fields.values()))
    if source_stamp['state'] in ('different_date', 'future', 'invalid'):
        partial = True
    return {
        'state': 'empty_unverified' if not rows else 'partial' if partial else 'sample_observed',
        'rows': len(rows), 'unique_codes': len(set(valid_codes)), 'invalid_identity_rows': invalid_identity,
        'duplicate_codes': duplicate_codes, 'missing_codes': missing, 'unexpected_codes': unexpected,
        'positive_price_rows': positive_prices, 'finite_field_rows': fields,
        'price_change_mismatch_rows': price_mismatches, 'item_fields': fields_present,
        'price_amount_mismatch_rows': amount_mismatches, 'ohlc_mismatch_rows': ohlc_mismatches,
        'invalid_value_rows': invalid_value_rows,
        'item_time_fields': observed_time_fields, 'rows_with_any_time_field': timed_rows,
        'source_timestamp': source_stamp,
        'quote_as_of': None, 'individual_quote_dates_verified': False,
        'time_note': '总时间不证明每条行情所属日或精确11:30；逐条时间需要独立验证。',
    }


def compare_quotes(first, second):
    def data(payload):
        value = payload.get('data') if isinstance(payload, dict) else None
        return value if isinstance(value, dict) else {}

    def by_code(payload):
        rows = data(payload).get('item', [])
        if not isinstance(rows, list):
            return {}
        return {row['thscode']: {field: row.get(field) for field in PRICE_FIELDS}
                for row in rows if isinstance(row, dict) and isinstance(row.get('thscode'), str)}
    before, after = by_code(first), by_code(second)
    common = set(before).intersection(after)
    return {
        'common_codes': len(common), 'changed_quote_rows': sum(before[code] != after[code] for code in common),
        'added_codes': sorted(set(after) - set(before)), 'removed_codes': sorted(set(before) - set(after)),
        'source_timestamp_changed': data(first).get('timestamp') != data(second).get('timestamp'),
        'quote_as_of': None, 'note': '价格不变而总时间前进也不能证明精确报价时刻。',
    }


class Probe:
    def __init__(self, adapter, output, target, *, secret=None, now=china_now, sleep=time.sleep):
        self.adapter, self.output, self.target = adapter, Path(output), target
        self.secret, self.now, self.sleep = secret, now, sleep
        self.output.mkdir(parents=True, mode=0o700, exist_ok=False)
        (self.output / 'responses').mkdir(mode=0o700)
        self.records = []
        self.report = {
            'version': 'intraday-source-probe-v1', 'source': 'fuyao', 'target_date': target,
            'started_at': now().isoformat(), 'quote_as_of': None,
            'purpose': '手动只读来源验收；不代表产品已支持盘中发布。',
            'requests': self.records, 'capabilities': {}, 'execution_state': 'running',
        }
        self.checkpoint()

    def checkpoint(self):
        """每次逻辑请求结束原子保存，异常退出也保留已经完成的私有证据。"""
        self.report['logical_requests'] = len(self.records)
        temporary = self.output / '.report.tmp'
        temporary.write_text(json.dumps(self.report, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
        temporary.chmod(0o600)
        temporary.replace(self.output / 'report.json')

    def request(self, name, path, params):
        if len(self.records) >= 100:
            raise ValueError('本次探测已达到100个逻辑请求的上限')
        record = {'name': name, 'path': path, 'params': params, 'started_at': self.now().isoformat()}
        self.records.append(record)
        try:
            payload = self.adapter.request(path, params)
            if contains_secret(payload, self.secret):
                record.update(state='rejected', reason='响应含凭据，未保存')
                return None
            content = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
            relative = 'responses/' + name + '.json'
            with (self.output / relative).open('xb') as writer:
                writer.write(content)
            (self.output / relative).chmod(0o600)
            record.update(state='received', response=relative, sha256=sha256(content).hexdigest(),
                          hash_basis='解码响应的规范JSON；非HTTP原始字节', request_id=payload.get('request_id'))
            return payload
        except ProviderError as error:
            record.update(state='failed', error_type=type(error).__name__,
                          status=error.status if isinstance(error.status, int) else None,
                          code=error.code if isinstance(error.code, int) else None)
            return None
        except (ValueError, TypeError):
            record.update(state='failed', error_type='InvalidPayload')
            return None
        finally:
            record.setdefault('state', 'interrupted')
            record['finished_at'] = self.now().isoformat()
            self.checkpoint()

    def paged(self, name, path, params, *, market=False):
        rows, declared, expected_pages, page_records = [], None, None, []
        size = 1000 if market else 200
        failures = []
        # 固定上限防止来源总数异常导致无界请求；股票快照offset按代码表分页。
        for page in range(1, 21):
            query = {**params, **({'limit': size, 'offset': (page - 1) * size} if market else {'page': page, 'size': size})}
            payload = self.request(name + f'-page-{page}', path, query)
            if payload is None:
                failures.append('request_failed')
                break
            data = payload.get('data')
            if not isinstance(data, dict) or not isinstance(data.get('item'), list):
                failures.append('invalid_page_structure')
                break
            pagination = data if market else data.get('pagination')
            total = pagination.get('total') if isinstance(pagination, dict) else None
            if isinstance(total, bool) or not isinstance(total, int) or total < 0:
                failures.append('invalid_total')
                break
            if declared is not None and total != declared:
                failures.append('total_changed')
                break
            declared = total
            pages = math.ceil(total / size)
            if not market:
                actual_pages = pagination.get('pages')
                if (pagination.get('page') != page or pagination.get('size') != size or
                    actual_pages not in ({0, 1} if total == 0 else {pages})):
                    failures.append('pagination_metadata_mismatch')
                    break
            expected_pages = max(1, pages)
            batch = data['item']
            if len(batch) > size:
                failures.append('page_exceeds_limit')
                break
            rows.extend(batch)
            page_records.append({'page': page, 'rows': len(batch),
                                 'source_timestamp': timestamp_state(data.get('timestamp'), self.target, self.now())})
            if page >= expected_pages:
                break
        else:
            failures.append('page_budget_exceeded')
        codes = [row.get('thscode') for row in rows if isinstance(row, dict)]
        valid = [code for code in codes if isinstance(code, str) and SYMBOL.fullmatch(code)]
        duplicates = sorted(code for code, count in Counter(valid).items() if count > 1)
        if len(valid) != len(rows):
            failures.append('invalid_identity')
        if duplicates:
            failures.append('duplicate_identity')
        if declared is not None and len(rows) != declared:
            failures.append('row_count_differs_from_code_table_total' if market else 'row_count_differs_from_pool_total')
        positive_prices = sum(isinstance(row, dict) and finite(row.get('last_price')) and row['last_price'] > 0 for row in rows)
        if not market and positive_prices != len(rows):
            failures.append('invalid_pool_price')
        enumeration_complete = declared is not None and declared > 0 and not failures
        summary = {
            'state': 'empty_unverified' if declared == 0 and not failures else 'partial' if failures else 'enumerated',
            'declared_total': declared, 'rows': len(rows), 'unique_codes': len(set(valid)),
            'enumeration_complete': enumeration_complete, 'duplicate_codes': duplicates,
            'failures': failures, 'pages': page_records, 'quote_as_of': None,
            'positive_price_rows': positive_prices,
            'item_fields': sorted(set().union(*(row.keys() for row in rows if isinstance(row, dict)))) if rows else [],
        }
        if market:
            # 枚举完整与有效报价完整是两件事；首尾页总时间也不是逐条行情时间。
            summary['quotes'] = inspect_quotes({'data': {'item': rows}}, self.target, self.now())
            summary['current_date_quote_coverage'] = 'unknown'
        else:
            summary['date_basis'] = '显式请求date_ms；源无逐条交易日时仍需价格等独立证据'
        return summary

    def run(self, *, full_market=False, repeat_seconds=0):
        capabilities = self.report['capabilities']
        samples = {}
        for name, (path, codes) in SAMPLES.items():
            payload = self.request(name, path, {'thscodes': ','.join(codes)})
            samples[name] = payload
            capabilities[name] = inspect_quotes(payload, self.target, self.now(), codes)
        midnight = int(datetime.combine(date.fromisoformat(self.target), day_time(), CHINA).timestamp() * 1000)
        for kind in ('up', 'down', 'break'):
            name = 'limit_' + kind
            capabilities[name] = self.paged(name, '/api/a-share/special-data/limit-' + kind + '-pool', {'date_ms': midnight})
        stats = self.request('limit_statistics', '/api/a-share/special-data/limit-up-statistics', {'date': self.target})
        data = stats.get('data', {}) if isinstance(stats, dict) else {}
        if not isinstance(data, dict):
            data = {}
        count_fields = ('limit_up_stock_count', 'open_stock_count', 'touched_limit_up_stock_count')
        counts_valid = all(isinstance(data.get(field), int) and not isinstance(data[field], bool) and data[field] >= 0 for field in count_fields)
        capabilities['limit_statistics'] = {
            'state': ('sample_observed' if counts_valid else 'partial') if data.get('trade_date') == self.target else 'unknown',
            'trade_date': data.get('trade_date'), 'source_timestamp': timestamp_state(data.get('timestamp'), self.target, self.now()),
            'limit_up_stock_count': data.get('limit_up_stock_count'), 'open_stock_count': data.get('open_stock_count'),
            'touched_limit_up_stock_count': data.get('touched_limit_up_stock_count'), 'quote_as_of': None,
            'fields': sorted(data),
        }
        self.report['pool_statistics_comparison'] = {
            'pool_total': capabilities['limit_up']['declared_total'],
            'statistics_limit_up_count': data.get('limit_up_stock_count'),
            'counts_equal': (capabilities['limit_up']['declared_total'] == data.get('limit_up_stock_count'))
                if capabilities['limit_up']['declared_total'] is not None and finite(data.get('limit_up_stock_count')) else None,
            'note': '来源池与汇总可能有不同范围、采样时间或定义；不合并、不强行补齐。',
        }
        hot = self.request('hot_hour', '/api/a-share/special-data/hot-stock-list', {'period': 'hour', 'limit': 30})
        hot_data = hot.get('data', {}) if isinstance(hot, dict) else {}
        if not isinstance(hot_data, dict):
            hot_data = {}
        hot_rows = hot_data.get('item')
        capabilities['hot_hour'] = {
            'state': 'sample_observed' if isinstance(hot_rows, list) and hot_rows else 'unknown',
            'rows': len(hot_rows) if isinstance(hot_rows, list) else None,
            'source_timestamp': timestamp_state(hot_data.get('timestamp'), self.target, self.now()), 'quote_as_of': None,
            'fields': sorted(hot_data),
        }
        if full_market:
            capabilities['full_market'] = self.paged('full_market', '/api/a-share/prices/snapshot', {}, market=True)
        if repeat_seconds:
            self.sleep(repeat_seconds)
            self.report['repeat'] = {'requested_wait_seconds': repeat_seconds, 'samples': {}}
            for name, (path, codes) in SAMPLES.items():
                repeated = self.request(name + '-repeat', path, {'thscodes': ','.join(codes)})
                result = {'inspection': inspect_quotes(repeated, self.target, self.now(), codes)}
                if samples[name] is not None and repeated is not None:
                    result['comparison'] = compare_quotes(samples[name], repeated)
                self.report['repeat']['samples'][name] = result
        self.report['finished_at'] = self.now().isoformat()
        self.report['logical_requests'] = len(self.records)
        self.report['execution_state'] = 'completed'
        self.report['transport_note'] = 'ProviderClient每次逻辑请求最多有限重试3次，沿用来源限速。'
        self.checkpoint()
        return self.report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, help='不存在的新私有回执目录，建议位于已忽略的data/validation下')
    parser.add_argument('--source', choices=['fuyao'], default='fuyao')
    parser.add_argument('--date', default=china_now().date().isoformat(), help='事件池目标日，YYYY-MM-DD；快照仍为来源当前值')
    parser.add_argument('--full-market', action='store_true', help='额外验证全A快照分页和有效报价覆盖')
    parser.add_argument('--repeat-seconds', type=float, default=0, help='0不复测；1至60秒后重复三组行情样本')
    args = parser.parse_args()
    try:
        target = date.fromisoformat(args.date).isoformat()
    except ValueError:
        parser.error('--date必须是YYYY-MM-DD')
    if args.repeat_seconds != 0 and not 1 <= args.repeat_seconds <= 60:
        parser.error('--repeat-seconds只能为0或1至60')
    if Path(args.output).expanduser().exists():
        parser.error('--output必须是不存在的新目录')
    try:
        key = CredentialStore().get(args.source)
    except ValueError:
        parser.error('现有凭据无法只读解密，请检查外置主密钥配置')
    if not key:
        parser.error('所选来源尚未配置Key')
    probe = Probe(ProviderClient(args.source, key), Path(args.output).expanduser(), target, secret=key)
    with provider_connection_scope():
        report = probe.run(full_market=args.full_market, repeat_seconds=args.repeat_seconds)
    print(json.dumps({'report': str(probe.output / 'report.json'), 'logical_requests': report['logical_requests'],
                      'capability_states': {name: item['state'] for name, item in report['capabilities'].items()},
                      'quote_as_of': None, 'note': '探测结束；支持范围需核对私有报告，不等于能力全部通过。'}, ensure_ascii=False))


if __name__ == '__main__':
    main()

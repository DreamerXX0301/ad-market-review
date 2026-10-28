"""Synthetic-only CAS/Parquet producer for isolated noon-close acceptance.

Nothing from this helper is imported by the application or persisted in a real
workspace. Names are SYN-prefixed; the caller owns and later removes the root.
"""
from hashlib import sha256
from datetime import date, timedelta

import pyarrow as pa
import pyarrow.parquet as pq

from app.core import dumps
from app.analytics.market_storage import write_reference


def build_generation(root, stock_count=5600, focus_count=600, day='2026-10-28', generation='market-synthetic-close'):
    if not 180 <= focus_count <= stock_count <= 20000:
        raise ValueError('Synthetic fixture requires 180 <= focus <= stocks <= 20000')
    prior = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
    prefix = focus_count - 160
    prior_ids = set(range(prefix))
    noon_up = set(range(prefix, prefix + 80)); noon_break = set(range(prefix + 80, prefix + 120))
    noon_down = set(range(prefix + 120, prefix + 130)); newly_up = set(range(prefix + 130, focus_count))
    close_up = set(range(min(20, prefix))) | set(range(prefix, prefix + 60)) | set(range(prefix + 80, prefix + 100)) | newly_up
    close_down = set(range(prefix + 75, prefix + 80)) | set(range(prefix + 120, prefix + 125))
    close_break = (noon_up | noon_break) - close_up - close_down
    stocks, closing, bars = [], [], []
    def symbol(i): return str(600000 + i) + '.SH'
    def height(i): return (i % 5 + 1) if i in prior_ids else 0
    for i in range(stock_count):
        n = 11. if i in noon_up else 9. if i in noon_down else 10.6 if i in noon_break else 10.2
        c = 11. if i in close_up else 9. if i in close_down else 10.3
        stocks.append({'instrument_id': symbol(i), 'name': 'SYN-' + str(i), 'latest_price': n, 'prev_close': 10.,
                       'change_pct': (n/10-1)*100, 'amount': 1000000.-i, 'volume': 1000.,
                       'price_basis': 'none', 'currency': 'CNY', 'volume_unit': 'share'})
        closing.append({'instrument_id': symbol(i), 'date': day, 'open': 10., 'high': max(10.,n,c), 'low': min(n,c,9.8),
                        'close': c, 'volume': 2000., 'amount': 2000000.-i, 'source': 'fuyao', 'price_basis': 'none'})
        bars.extend([{'instrument_id': symbol(i), 'date': prior, 'open': 10., 'high': 10., 'low': 10.,
                      'close': 10., 'volume': 1000., 'amount': 1000000.-i, 'source': 'fuyao', 'price_basis': 'none'}, closing[-1]])
    published = root / 'published'; stage = published / generation; stage.mkdir(parents=True)
    temporary = root / 'synthetic-daily.parquet'; pq.write_table(pa.Table.from_pylist(bars), temporary, compression='zstd')
    encoded = temporary.read_bytes(); checksum = sha256(encoded).hexdigest(); temporary.unlink()
    key = f'market-objects/daily/{checksum[:2]}/{checksum}.parquet'
    target = published / key; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(encoded)
    descriptor = {'version': 'market-partitions-v1', 'role': 'daily.parquet', 'partitions': [
        {'month': day[:7], 'object_key': key, 'sha256': checksum, 'bytes': len(encoded), 'rows': len(bars),
         'data_begin': prior, 'data_end': day}], 'data_begin': prior, 'data_end': day, 'rows': len(bars)}
    daily = stage / 'daily.manifest.json'; daily.write_text(dumps(descriptor))
    codes = ('000001.SH', '399001.SZ', '399006.SZ', '000688.SH')
    raw_pool = lambda ids: [{'thscode': symbol(i), 'name': 'SYN-'+str(i), 'date': day, 'last_price': closing[i]['close'],
                            'continue_day_cnt': height(i)+1 if i in prior_ids else 1} for i in sorted(ids)]
    reference = {'calendar': [prior, day], 'pool_trade_date': day,
                 'master': {symbol(i): {'instrument_id': symbol(i), 'code': symbol(i)[:6],
                            'name': 'SYN-'+str(i), 'kind': 'stock', 'exchange': 'SH',
                            'list_date': '2020-01-01'} for i in range(stock_count)},
                 'pool_coverage': {name: {'complete': True} for name in ('limitup','limitdown','breakpool')},
                 'limitup': raw_pool(close_up), 'limitdown': raw_pool(close_down), 'breakpool': raw_pool(close_break),
                 'indices': {code: [{'date': prior, 'open': 100., 'high': 100., 'low': 100., 'close': 100., 'volume': 100000., 'amount': 10000000.},
                                    {'date': day, 'open': 100., 'high': 101., 'low': 100., 'close': 101., 'volume': 200000., 'amount': 20000000.}] for code in codes}}
    files, _ = write_reference(reference, published, stage, generation)
    for role, path in [('reference.json', stage/'reference.json'), ('daily.parquet', daily)]:
        files.append({'role': role, 'path': f'published/{generation}/{path.name}', 'sha256': sha256(path.read_bytes()).hexdigest(), 'bytes': path.stat().st_size})
    (stage/'manifest.json').write_text(dumps({'summary': {'snapshot_id': generation, 'source': 'fuyao', 'price_basis': 'none',
        'volume_unit': '股', 'amount_unit': 'CNY', 'data_begin': prior, 'data_end': day, 'files': files}}))
    def noon_pool(ids):
        items = [{**stocks[i], 'date': day, 'continue_day_cnt': 1} for i in sorted(ids)]
        return {'date': day, 'state': 'observed', 'declared_total': len(items), 'count': len(items), 'items': items, 'excluded_rows': 0}
    previous = [{'instrument_id': symbol(i), 'name': 'SYN-'+str(i), 'date': prior, 'previous_close': 10., 'previous_board_count': height(i)} for i in sorted(prior_ids)]
    classifications = {symbol(i): {'state': 'observed_sample', 'industries': [
        {'topic_code': str(881000+i%10)+'.TI', 'name':'SYN-行业'+str(i%10), 'kind':'industry','source':'fuyao','members_observed_date':prior}],
        'topics': [{'topic_code':str(885000+i%20)+'.TI','name':'SYN-概念'+str(i%20),'kind':'concept','source':'fuyao','members_observed_date':prior}]} for i in range(focus_count)}
    noon = {'version':'noon-snapshot-v1','date':day,'synthetic_fixture':True,
        'calendar':{'verified':True,'previous_day':prior}, 'stocks':stocks,
        'indices':[{'instrument_id':code,'latest_price':100.5,'prev_close':100.,'change_pct':.5,'amount':10000000.,'price_basis':'none','currency':'CNY'} for code in codes],
        'summary':{'top_amount':stocks[:20],'top_gainers':stocks[:20],
                   'coverage':{'enumeration_complete':True},'breadth':{'total':stock_count,'valid':stock_count,'up':stock_count-len(noon_down),'down':len(noon_down),'flat':0}},
        'events':{'limit_up':noon_pool(noon_up),'limit_break':noon_pool(noon_break),'limit_down':noon_pool(noon_down)},
        'previous_review':{'date':prior,'event_cohorts':{'limit_up':{'date':prior,'state':'observed_sample','source_count':len(previous),'verified_count':len(previous),'unverified_count':0,'items':previous}}},
        'stock_classifications':classifications, 'hot_stocks':stocks[:10],
        'sources':[{'name':'stocks_page_1','source':'fuyao','state':'received'}]}
    return generation, noon

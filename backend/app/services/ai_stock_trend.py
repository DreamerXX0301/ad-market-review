"""Bounded, read-only AI projection of published stock chart facts."""
from math import isfinite

from app.services.market_history import resolve_reference
from app.services.market_supplement import load_supplement
from app.services.sector_history import sector_prices_for_date
from app.services.stock_board_presentation import down_state, retained_proof
from app.services.strategy_limits import quote_index


PRICE_COLUMNS = ('date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'change_pct')
PERIOD_COVERAGE = {
    'W': ('complete', 'week_closed', 'weekly_quote_coverage_complete', 'weekly_missing_market_sessions'),
    'M': ('complete', 'month_closed', 'monthly_quote_coverage_complete', 'monthly_missing_market_sessions'),
}
INDICATORS = ('ma5', 'ma10', 'ma20', 'ma30', 'ma60', 'ma250', 'macd_dif',
              'macd_dea', 'macd_hist', 'kdj_k', 'kdj_d', 'kdj_j')
SUPPLEMENT_FIELDS = ('public_pools', 'annual_pools', 'annual_down_pools', 'sector_index_history')
MAX_CLASSIFICATIONS = 8
LIMIT_LOOKBACK = 30


def _number(value):
    return value if type(value) in (int, float) and isfinite(value) else None


def _gap(result, capability, reason):
    result['gaps'].append({'capability': capability, 'date': result['date'], 'state': 'unknown', 'reason': reason})


def _permitted(value):
    if isinstance(value, dict):
        return (value.get('egress_allowed') is not False and value.get('ai_processing_allowed') is not False
                and all(_permitted(item) for item in value.values()))
    if isinstance(value, (list, tuple)):
        return all(_permitted(item) for item in value)
    return True


def _classification(status, day, symbol, reference, supplement):
    local, coverage = resolve_reference('sectors', day, status, reference)
    topics = local.get('topics', [])
    known = [row for row in topics if row.get('kind', 'concept') in ('industry', 'concept')
             and symbol in row.get('instrument_ids', []) and _permitted(row)]
    known.sort(key=lambda row: (row.get('kind', 'concept') != 'industry', str(row.get('topic_id'))))
    prices = sector_prices_for_date(supplement, day) if known else {'items': []}
    by_code = {row['topic_code']: row for row in prices['items'] if _permitted(row)}
    rows = []
    for item in known[:MAX_CLASSIFICATIONS]:
        price = by_code.get(item.get('topic_code'))
        row = {key: item.get(key) for key in ('topic_id', 'topic_code', 'name', 'kind', 'observed_date', 'source')}
        if (price and price['topic_id'] == item.get('topic_id')
                and price['kind'] == item.get('kind', 'concept') and item.get('source', 'fuyao') == price['source']):
            row['index_comparison'] = {key: price[key] for key in
                                       ('quote_date', 'previous_date', 'change_pct', 'source')}
        rows.append(row)
    return {'date': day, 'source_snapshot_id': coverage.get('source_snapshot_id'),
            'source_snapshot_day': coverage.get('source_snapshot_day'), 'observed_count': len(known),
            'truncated': len(known) > len(rows), 'items': rows,
            'scope': '只读选择日可核验的来源成员存档和同来源指数日行情；不使用现行成员回填，不代表完整行业/题材归属或资金流向'}


def _limit_events(market, status, day, symbol, reference, supplement, sessions):
    if not sessions:
        return {'items': [], 'sessions': 0, 'up_unknown': 0, 'down_unknown': 0}
    history = market.history_for_instruments([symbol], start=sessions[0], end=day)
    quotes = quote_index(history.get(symbol, []))
    proof = retained_proof({**reference, '_supplement': supplement}, status['snapshot_id'])
    items = []
    up_unknown = down_unknown = 0
    for target in sessions:
        quote = quotes.get(target)
        if not _permitted(quote) or (quote and quote.get('price_basis', 'none') != 'none'):
            quote = None
        up = proof._state(symbol, target, quote)
        down = down_state(proof, symbol, target, quote, up)
        up_unknown += up['state'] is None
        down_unknown += down['state'] is None
        if up['state'] is True or down['state'] is True:
            items.append({'date': target, 'event': '收盘涨停' if up['state'] is True else '收盘跌停'})
    return {'since': sessions[0], 'until': day, 'sessions': len(sessions), 'up_unknown': up_unknown,
            'down_unknown': down_unknown, 'items': items,
            'scope': '实际市场交易日内用原始收盘、正成交量与来源收盘池交叉核验；未知不当作无涨跌停'}


def project_stock_trend(result, market, status, day, instrument, period, count, *, reference=None):
    """Populate the standard AI tool envelope from a pinned chart and dated facts."""
    if period not in ('D', 'W', 'M') or type(count) is not int or not 5 <= count <= 60:
        raise ValueError('K线仅支持D/W/M，每次5至60根')
    symbol = instrument['instrument_id']
    snapshot = status['snapshot_id']
    result['date'] = day
    chart = market.chart(symbol, period, day, 'none', indicator_continuity='quotes', ai_projection=True)
    if (chart.get('as_of') != day or chart.get('period') != period or chart.get('snapshot_id') != snapshot
            or chart.get('instrument', {}).get('instrument_id') != symbol or chart.get('price_basis') != 'none'):
        raise ValueError('K线日期、证券、周期、价格口径或发布代际不一致')
    reference = market.reference() if reference is None else reference
    if not _permitted(chart) or not _permitted(reference):
        _gap(result, 'source_permission', 'K线或相关来源明确禁止外发或AI处理')
        return
    bars = [bar for bar in chart.get('bars', []) if isinstance(bar.get('date'), str) and bar['date'] <= day]
    rows = bars[-count:]
    if not rows:
        _gap(result, 'stock_trend', '指定观察日及以前没有可核验K线')
        return
    sessions = [item for item in market.calendar() if rows[0]['date'] <= item <= day][-LIMIT_LOOKBACK:]
    supplement = load_supplement(snapshot, dates=sessions, fields=SUPPLEMENT_FIELDS)
    if supplement.get('snapshot_id') not in (None, snapshot):
        raise ValueError('行情与补充证据的发布代际不一致')
    if not _permitted(supplement):
        _gap(result, 'supplement_permission', '补充材料存在明确禁止外发或AI处理的记录，仅返回原始K线')
        supplement = None
    indicators = [{key: row['date'] if key == 'date' else _number(row.get(key))
                   for key in ('date', *INDICATORS)} for row in rows[-3:]]
    missing = sum(gap['missing_market_sessions'] for gap in chart.get('quote_gaps', [])
                  if rows[0]['date'] <= gap['previous_quote_date'] < gap['resume_quote_date'] <= day)
    coverage_fields = PERIOD_COVERAGE.get(period, ())
    data = {
        'instrument_id': symbol, 'name': instrument.get('name'), 'period': period,
        'observation_date': day, 'first_bar_date': rows[0]['date'], 'last_bar_date': rows[-1]['date'],
        'requested_bars': count, 'returned_bars': len(rows), 'truncated_earlier_bars': len(bars) > len(rows),
        'missing_market_sessions_between_quotes': missing,
        'price_basis': 'none', 'indicator_continuity': chart.get('indicator_continuity'),
        'units': {'price': 'CNY', 'volume': '股', 'amount': 'CNY'},
        'bar_columns': (*PRICE_COLUMNS, *coverage_fields),
        'bars': [[row['date'], *(_number(row.get(key)) for key in PRICE_COLUMNS[1:]),
                  *(row[key] if type(row.get(key)) in (bool, int) else None for key in coverage_fields)]
                 for row in rows],
        'recent_indicators': indicators,
        'scope': '未复权原始报价；周/月最后一柱可能未完整，不能当作完整周期信号。缺失报价不补K线，成交额不是净流入。'
    }
    if period == 'D' and rows[-1]['date'] != day:
        _gap(result, 'stock_trend_observation_day', '观察日无有效日K；已返回的末根K线日期早于观察日')
    if supplement is not None:
        limits = _limit_events(market, status, day, symbol, reference, supplement, sessions)
        classification = _classification(status, day, symbol, reference, supplement)
        if period == 'D' and len(rows) >= 2 and rows[-1]['date'] == day and rows[-1].get('price_continuity') is True:
            stock_change = _number(rows[-1].get('change_pct'))
            for item in classification['items']:
                comparison = item.get('index_comparison')
                if comparison and stock_change is not None and comparison['previous_date'] == rows[-2]['date']:
                    comparison['stock_change_pct'] = stock_change
                    comparison['stock_minus_index_percentage_points'] = stock_change - comparison['change_pct']
        data.update(closing_limit_events=limits, classification=classification)
        if limits['up_unknown'] or limits['down_unknown']:
            _gap(result, 'closing_limit_events', '部分交易日的收盘涨跌停来源或报价缺失，不能确认全窗口事件')
        if not any('index_comparison' in item for item in classification['items']):
            _gap(result, 'sector_comparison', '缺少可与所选证券同时核验的成员存档或同来源同日板块指数涨跌幅')
    result['source'] = ['current_instance_published_market']
    result['summary'].update(instrument_id=symbol, period=period, price_basis='none')
    result['evidence'].append({'evidence_id': 'stock_trend', 'source': 'current_instance_published_market',
                               'kind': 'stock_trend', 'title': '个股K线、量价和已核验附加资料',
                               'dates': {'observation_date': day}, 'data': data})

"""Project a bounded direction watchlist from one retained intraday snapshot."""
from __future__ import annotations

from app.services.market_outlook import NON_DIRECTION_LABELS
from app.services.noon_analysis import number

LIMIT = 12
MATERIAL_MOVE = 0.5
SCOPE = '仅覆盖当前快照的已留存方向。区间变化使用同日可比报价，成员个股为已核验样本。'
SECTOR_LISTS = ('strong_sectors', 'limit_up_sectors', 'wide_limit_up_sectors', 'index_only_sectors', 'sectors')


def _members(snapshot, direction):
    saved = (snapshot.get('board_stocks') or {}).get(direction['instrument_id']) or {}
    by_code = {}
    for group in ('sealed_stocks', 'broken', 'candidates'):
        for stock in direction.get(group) or ():
            by_code.setdefault(stock['instrument_id'], stock)
    for stock in saved.get('items') or ():
        by_code.setdefault(stock['instrument_id'], stock)
    ranked = sorted(by_code.values(), key=lambda row: (
        number(row.get('change_pct')) is None, -(number(row.get('change_pct')) or 0),
        row['instrument_id']))
    fields = ('instrument_id', 'name', 'change_pct', 'change_pp', 'amount',
              'limit_up_state', 'continue_day_cnt', 'themes', 'limit_up_reference_price')
    return [{key: row[key] for key in fields if key in row} for row in ranked[:6]]


def build_intraday_discovery(snapshot):
    baseline = (snapshot.get('summary') or {}).get('comparison_slot')
    current = {}
    for key in SECTOR_LISTS:
        for row in snapshot.get(key) or ():
            if row.get('instrument_id') and row.get('kind') in ('industry', 'concept'):
                current.setdefault(row['instrument_id'], row)
    strong = {row['instrument_id'] for row in snapshot.get('strong_sectors') or ()}
    candidates = {key: [] for key in ('new', 'accelerating', 'fading', 'leading')}
    for code, row in current.items():
        if row['kind'] == 'concept' and row.get('name') in NON_DIRECTION_LABELS:
            continue
        change = number(row.get('change_pct'))
        if change is None:
            continue
        movement = number(row.get('change_pp')) if baseline else None
        signal = None
        if code in strong and movement is not None and movement >= MATERIAL_MOVE:
            signal = 'new' if change - movement <= 0 else 'accelerating'
        elif movement is not None and movement <= -MATERIAL_MOVE and change - movement > 0:
            signal = 'fading'
        elif code in strong:
            signal = 'leading'
        if signal is None:
            continue
        fields = ('instrument_id', 'name', 'kind', 'member_date', 'up_ratio', 'limit_up_count',
                  'limit_up_count_state', 'broken_count', 'broken_count_state', 'member_coverage_pct')
        candidates[signal].append({
            **{key: row[key] for key in fields if key in row},
            'signal': signal, 'change_pct': change, 'change_pp': movement,
            'amount_delta': number(row.get('amount_delta'), nonnegative=True) if movement is not None else None,
            'stocks': _members(snapshot, row),
        })
    for signal, rows in candidates.items():
        rows.sort(key=lambda row: (-(abs(row['change_pp']) if signal != 'leading' else row['change_pct']),
                                   row['instrument_id']))
    # Keep several of each signal visible, then fill spare places with other confirmed directions.
    groups = list(candidates.values())
    directions = [row for group in groups for row in group[:3]]
    directions.extend([row for group in groups for row in group[3:]][:LIMIT - len(directions)])
    reason = ('当前快照暂未发现符合条件的已核验方向。' if not directions else
              '当前没有可比的上一时点，展示此时点已核验的领涨方向。' if not baseline else None)
    return {'date': snapshot.get('date'), 'slot': snapshot.get('slot'), 'scope': SCOPE,
            'state': 'available' if directions else 'unknown', 'reason': reason,
            'baseline_slot': baseline, 'directions': directions}

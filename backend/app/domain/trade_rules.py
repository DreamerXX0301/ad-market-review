"""Versioned research buy/sell predicates, independent from pool selection."""
from app.domain.conditions import names, validate

UNAVAILABLE = {
    'trendline_pullback', 'trendline_breakout',
    'topic_ids', 'limit_up', 'prev_limit_up', 'limit_up_count_1y', 'limit_up_1y',
    'turnover_ratio_pct', 'trading_age', 'tracking_change_pct',
    'close_below_entry', 'close_above_entry_high', 'close_below_entry_low',
}


def normalize_trade_rules(value=None):
    if value is None:
        return {'entry': None, 'exit': None}
    if not isinstance(value, dict) or set(value)-{'entry','exit'}:
        raise ValueError('买卖规则只接受entry和exit条件树')
    result = {}
    for side in ('entry','exit'):
        tree = value.get(side)
        tree = validate(tree) if tree is not None else None
        if tree is not None and names(tree) & UNAVAILABLE:
            raise ValueError('买卖条件暂只支持有历史覆盖的价格、量能、均线和技术指标；题材、历史涨停、换手及跟踪基准字段尚不可用于回测买卖')
        result[side] = tree
    return result


def strategy_predicates(strategy):
    selection = validate(strategy['definition'])
    rules = normalize_trade_rules(strategy.get('trade_rules'))
    trees = [selection] + [tree for tree in rules.values() if tree is not None]
    return selection, rules, {'all': trees}

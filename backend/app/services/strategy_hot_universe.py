"""A bounded, frozen current-member hot universe; never a historical feature."""
from app.services.workspace_common import digest, fail

VERSION = 'current-hot-universe-v1'
HISTORICAL_REASON = '近期热点题材 / 行业使用当前成员代理，缺少逐日历史成员与当时排名，不能回填历史回测；请关闭此范围后另存策略版本'


def normalize(value):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - {'window_sessions', 'top_n', 'kind'}:
        fail('近期热点范围仅接受 window_sessions、top_n、kind')
    result = {'window_sessions': value.get('window_sessions', 7), 'top_n': value.get('top_n', 10), 'kind': value.get('kind', 'all')}
    for field, maximum in (('window_sessions', 60), ('top_n', 50)):
        if isinstance(result[field], bool) or not isinstance(result[field], int) or not 1 <= result[field] <= maximum:
            fail(f'近期热点 {field} 需要1至{maximum}的整数')
    if result['kind'] not in ('all', 'concept', 'industry'):
        fail('近期热点类型仅支持全部、题材、行业')
    return result


def freeze(config, *, expected_day):
    """One bounded batch event read per run; one shared membership receipt."""
    from app.services.hot_topics import list_hot_topics
    ranking = list_hot_topics(config['top_n'], window_sessions=config['window_sessions'],
                              kind=config['kind'], include_members=True)
    if ranking['business_date'] != expected_day:
        fail('热点排名业务日已变化，请重新运行策略', 409)
    memberships = ranking.pop('_selected_memberships')
    union = sorted(set().union(*(set(values) for values in memberships.values())) if memberships else set())
    complete = ranking['coverage'].get('complete') is True and ranking['window'].get('complete') is True
    evidence = {**ranking, 'version': VERSION, 'config': config, 'membership_sha256': digest(memberships),
                'member_ids': union, 'complete': complete,
                'scope': 'current_members_proxy_not_historical',
                'reason': None if complete else '窗口来源、原价核验或分类成员覆盖不足，热点范围保持待核验'}
    by_stock = {}
    for topic_id, symbols in memberships.items():
        for symbol in symbols:
            by_stock.setdefault(symbol, []).append(topic_id)
    return evidence, by_stock


def combine(check, ident, evidence, by_stock):
    """Strict AND: a known technical false stays false despite hot unknowns."""
    matched = sorted(by_stock.get(ident, []))
    hot_result = bool(matched) if evidence['complete'] else None
    hot_check = {'type': 'hot_universe', 'label': '近期热点题材 / 行业', 'result': hot_result,
                 'matched_topic_ids': matched, 'evidence_ref': 'run.universe_definition.hot_universe',
                 'reason': evidence['reason']}
    result = False if check['result'] is False or hot_result is False else True if check['result'] is True and hot_result is True else None
    return {'logic': 'AND', 'result': result, 'checks': [check, hot_check]}

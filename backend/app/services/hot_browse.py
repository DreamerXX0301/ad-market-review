"""Read-only current hot-range receipts for tracking and personal watchlists.

The ranking and membership policy are exactly the stock strategy hot-universe
policy. A receipt is bound to the list's current date, publication and local
topic revisions, never to an entry day or historical alert date.
"""
from app.core import connect
from app.services import strategy_hot_universe
from app.services.workspace_common import current, fail


def browse_scope(connection, market, *, expected_day=None):
    from app.services.topic_members import _head
    from app.services.strategy_batches import _topic_revision_signature
    day = current(connection)
    if expected_day is not None and day != expected_day:
        fail('业务日已变化，请刷新列表后筛选热点', 409)
    head = _head(connection)
    reader = getattr(market, 'status', None)
    snapshot = reader().get('snapshot_id') if callable(reader) else None
    if head and snapshot and head != snapshot:
        fail('行情发布已变化，请刷新列表后筛选热点', 409)
    return {'business_date': day, 'snapshot_id': snapshot or head,
            'topic_revision_signature': _topic_revision_signature(connection)}


def require_scope(scope, market):
    with connect() as connection:
        connection.execute('BEGIN')
        latest = browse_scope(connection, market)
    if latest != scope:
        fail('业务日、行情或题材成员已变化，请刷新列表后筛选热点', 409)


def read_hot_range(as_of, *, window_sessions=7, top_n=10, kind='all',
                   snapshot_id=None, topic_revision_signature=None):
    from app.core import get_market
    from datetime import date
    try:
        valid_date = isinstance(as_of, str) and date.fromisoformat(as_of).isoformat() == as_of
    except ValueError:
        valid_date = False
    if not valid_date:
        fail('热点观察日应为YYYY-MM-DD格式')
    config = strategy_hot_universe.normalize({'window_sessions': window_sessions,
                                              'top_n': top_n, 'kind': kind})
    market = get_market()
    with connect() as connection:
        connection.execute('BEGIN')
        scope = browse_scope(connection, market, expected_day=as_of)
    if ((snapshot_id is not None and snapshot_id != scope['snapshot_id']) or
            (topic_revision_signature is not None and topic_revision_signature != scope['topic_revision_signature'])):
        fail('列表行情或题材成员已变化，请刷新列表后筛选热点', 409)
    evidence, _ = strategy_hot_universe.freeze(config, expected_day=as_of)
    require_scope(scope, market)
    if scope['snapshot_id'] != evidence.get('snapshot_id'):
        fail('热点排名与列表行情发布不一致，请刷新后重试', 409)
    return {**evidence, 'browse_scope': scope, 'persisted': False,
            'browse_notice': '当前业务日热点观察，使用当前成员代理；不会重算、修改历史选股、入池归属或触发记录。'}

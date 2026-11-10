"""Private, date-scoped user assignments shared by the two ladder views."""
import re

from fastapi import HTTPException

from app.core import connect, digest, new_id, now_ms
from app.services.read_revisions import observation_day

_SYMBOL = re.compile(r'[0-9]{6}\.(?:SH|SZ|BJ)')
_REVISION = re.compile(r'[0-9a-f]{32}')
_TOPIC_KINDS = ('industry', 'concept')
_FIELDS = 'topic_id, topic_name AS name, topic_kind AS kind, revision'


def _day(value):
    day = observation_day(value)
    if day is None:
        raise HTTPException(422, '请选择明确的节点交易日')
    return day


def _text(value, maximum, label):
    if isinstance(value, str):
        value = value.strip()
        if 1 <= len(value) <= maximum and not any(ord(char) < 32 or ord(char) == 127 for char in value):
            return value
    raise HTTPException(422, f'{label}须为1至{maximum}个字符')


def manual_topic(name, kind='concept'):
    name = _text(name, 80, '自定义题材名称')
    if kind not in _TOPIC_KINDS:
        raise HTTPException(422, '题材类型须为概念或行业')
    return {'topic_id': 'manual:' + digest([kind, name]), 'name': name, 'kind': kind}


def read_overrides(day, *, read_connection=connect):
    day = _day(day)
    with read_connection() as connection:
        rows = connection.execute(
            f'SELECT instrument_id, {_FIELDS} FROM market_node_topic_override WHERE trade_date=? ORDER BY instrument_id',
            (day,))
        return {row['instrument_id']: {key: row[key] for key in ('topic_id', 'name', 'kind', 'revision')} for row in rows}


def save_override(day, symbol, topic, *, expected_revision):
    """Upsert or restore one already-validated dated stock, using a row token."""
    day = _day(day)
    if not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol):
        raise HTTPException(422, '请选择明确的股票代码')
    if expected_revision is not None and (not isinstance(expected_revision, str) or not _REVISION.fullmatch(expected_revision)):
        raise HTTPException(422, '人工归类版本无效，请重新打开调整面板')
    if topic is not None:
        if not isinstance(topic, dict) or set(topic) != {'topic_id', 'name', 'kind'} or topic['kind'] not in _TOPIC_KINDS:
            raise HTTPException(422, '请选择明确的题材或填写自定义题材名称')
        topic = {'topic_id': _text(topic['topic_id'], 200, '题材标识'),
                 'name': _text(topic['name'], 80, '题材名称'), 'kind': topic['kind']}

    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        existing = connection.execute(
            f'SELECT {_FIELDS} FROM market_node_topic_override WHERE trade_date=? AND instrument_id=?',
            (day, symbol)).fetchone()
        current = dict(existing) if existing else None
        actual_revision = current['revision'] if current else None
        if expected_revision != actual_revision:
            raise HTTPException(409, '该股票的人工归类已变化，请刷新后重新调整')
        if topic is None:
            if current is not None:
                connection.execute('DELETE FROM market_node_topic_override WHERE trade_date=? AND instrument_id=?',
                                   (day, symbol))
            override = None
        elif current is not None and all(current[key] == value for key, value in topic.items()):
            override = current
        else:
            override = {**topic, 'revision': new_id()}
            connection.execute('''INSERT INTO market_node_topic_override
                (trade_date, instrument_id, topic_id, topic_name, topic_kind, revision, updated_at_ms)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(trade_date,instrument_id) DO UPDATE SET
                topic_id=excluded.topic_id, topic_name=excluded.topic_name, topic_kind=excluded.topic_kind,
                revision=excluded.revision, updated_at_ms=excluded.updated_at_ms''',
                (day, symbol, override['topic_id'], override['name'], override['kind'], override['revision'], now_ms()))
    return {'date': day, 'instrument_id': symbol, 'override': override}

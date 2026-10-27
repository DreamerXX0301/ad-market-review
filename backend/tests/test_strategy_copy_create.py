"""The existing create endpoint persists copied settings as an independent V1."""
from copy import deepcopy

from app import core
from test_workspace import add_one, make_strategy, ws


def test_copied_configuration_creates_new_identity_without_source_history_or_tracking(ws):
    _, client = ws
    source = make_strategy(client)
    run, tracking = add_one(client, source)
    source_id = source['strategy_id']
    source = client.patch('/api/v1/strategies/' + source_id, json={
        'period': 'W', 'price_basis': 'forward',
        'definition': {'all': [
            {'field': 'board', 'op': 'in', 'value': ['main', 'chinext']},
            {'field': 'close', 'op': '>', 'right_field': 'ma30', 'period': 'D'},
        ]},
        'hot_universe': {'window_sessions': 9, 'top_n': 13, 'kind': 'concept'},
        'trade_rules': {'entry': {'field': 'kd_cross', 'op': '==', 'value': True, 'period': 'D'},
                        'exit': {'field': 'macd_dead_cross', 'op': '==', 'value': True, 'period': 'W'}},
        'policy': {'duration_sessions': 60, 'allow_entry_day_alerts': True,
                   'allow_entry_day_exits': False, 'alert_logic': 'AND', 'exit_logic': 'OR',
                   'alerts': [{'field': 'tracking_change_pct', 'op': '>=', 'value': 5}],
                   'exits': [{'field': 'close_below_entry', 'op': '==', 'value': True}]},
    }).json()
    assert source['version'] == 2 and source['tracking_count'] == 1
    source = client.get('/api/v1/strategies/' + source_id).json()
    payload = {key: deepcopy(source[key]) for key in ('period', 'price_basis', 'definition', 'hot_universe', 'trade_rules')}
    policy_fields = ('duration_sessions', 'allow_entry_day_alerts', 'allow_entry_day_exits', 'alert_logic', 'exit_logic', 'alerts', 'exits')
    payload.update(name=source['name'] + ' · 副本', policy={key: deepcopy(source['policy'][key]) for key in policy_fields})
    response = client.post('/api/v1/strategies', json=payload)
    assert response.status_code == 200, response.text
    copied = response.json(); copy_id = copied['strategy_id']
    assert copy_id != source_id
    assert copied['version'] == copied['current_version'] == copied['current_policy_version'] == 1
    assert copied['policy']['policy_version'] == 1
    assert copied['latest_run'] is None and copied['tracking_count'] == 0
    for key in ('period', 'price_basis', 'definition', 'hot_universe', 'trade_rules'):
        assert copied[key] == source[key]
    for key in policy_fields:
        assert copied['policy'][key] == source['policy'][key]
    assert client.get('/api/v1/strategies/' + source_id).json() == source
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM selection_run WHERE strategy_id=?', (copy_id,)).fetchone()[0] == 0
        assert connection.execute('SELECT count(*) FROM tracking_round WHERE strategy_id=?', (copy_id,)).fetchone()[0] == 0
        assert connection.execute('SELECT strategy_id FROM tracking_round WHERE round_id=?', (tracking['round_id'],)).fetchone()[0] == source_id
    changed = client.patch('/api/v1/strategies/' + copy_id, json={
        'name': '独立调整的副本', 'definition': {'field': 'close', 'op': '>', 'value': 20},
        'policy': {**payload['policy'], 'duration_sessions': 90},
        'apply_to_active_tracking': True,
    })
    assert changed.status_code == 200, changed.text
    assert changed.json()['version'] == 2
    assert changed.json()['policy_application']['applied_count'] == 0
    assert client.get('/api/v1/strategies/' + source_id).json() == source
    assert client.get('/api/v1/strategy-runs/' + run['run_id']).json()['strategy_id'] == source_id

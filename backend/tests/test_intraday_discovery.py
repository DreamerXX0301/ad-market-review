from app.services.intraday_discovery import build_intraday_discovery


def test_discovery_preserves_same_slot_moves_and_verified_members():
    def board(code, name, change, movement, **extra):
        return dict(instrument_id=code, name=name, kind='industry', change_pct=change,
                    change_pp=movement, **extra)

    new = board('884001.TI', '示例设备', 1.5, 2.0, amount_delta=0,
                limit_up_count=1, limit_up_count_state='observed',
                sealed_stocks=[{'instrument_id': '600001.SH', 'name': '示例个股',
                                'change_pct': 10, 'limit_up_state': 'sealed', 'continue_day_cnt': 2}])
    faster = board('884002.TI', '示例材料', 3.0, 1.2)
    faded = board('884003.TI', '示例机械', -0.2, -1.5)
    wide = {**new, 'instrument_id': '885001.TI', 'kind': 'concept', 'name': '融资融券'}
    snapshot = {'date': '2026-11-05', 'slot': '1330', 'summary': {'comparison_slot': 'noon'},
                'strong_sectors': [new, faster, wide], 'sectors': [faded, new],
                'board_stocks': {'884001.TI': {'items': [
                    {'instrument_id': '600001.SH', 'change_pct': 10},
                    {'instrument_id': '600002.SH', 'change_pct': 5},
                ]}}}
    discovery = build_intraday_discovery(snapshot)
    assert discovery['baseline_slot'] == 'noon'
    assert [row['signal'] for row in discovery['directions']] == ['new', 'accelerating', 'fading']
    assert discovery['directions'][0]['amount_delta'] == 0
    stocks = discovery['directions'][0]['stocks']
    assert [row['instrument_id'] for row in stocks] == ['600001.SH', '600002.SH']
    assert stocks[0]['continue_day_cnt'] == 2
    without_baseline = build_intraday_discovery({**snapshot, 'summary': {}})
    assert [row['signal'] for row in without_baseline['directions']] == ['leading', 'leading']
    assert all(row['change_pp'] is None for row in without_baseline['directions'])

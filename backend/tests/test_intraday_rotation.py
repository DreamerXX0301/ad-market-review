"""Rotation reopens complete retained prices while strength lists stay bounded."""
from copy import deepcopy

import pytest

from app.services import intraday_rotation as rotation

DAY = '2026-11-05'
GENERATION = 'market-' + 'a' * 24
PRIOR = '2026-11-04'


def _live():
    codes = [f'{880000 + i}.TI' for i in range(30)] + ['886021.TI', '886108.TI', '886100.TI']
    names = {'886021.TI': '国企改革', '886108.TI': 'AI应用', '886100.TI': 'DeepSeek概念'}
    quotes = [{'instrument_id': code, 'kind': 'concept', 'latest_price': 101 + i / 100,
               'prev_close': 100, 'change_pct': 1 + i / 100, 'amount': 1000,
               'price_basis': 'none', 'currency': 'CNY'} for i, code in enumerate(codes)]
    topics = [{'topic_code': code, 'name': names.get(code, code), 'kind': 'concept',
               'source': 'fuyao', 'observed_date': PRIOR, 'members_complete': True,
               'instrument_ids': ['600001.SH', '600002.SH']} for code in codes]
    return {'version': 'intraday-snapshot-v5', 'date': DAY, 'slot': '1030',
            'collected_start_at': DAY + 'T10:30:00+08:00', 'collected_end_at': DAY + 'T10:30:10+08:00',
            'source_checks': [{'domain': 'limit_up_page_1', 'state': 'accepted',
                               'response_sha256': 'a' * 64, 'source_time': DAY + 'T10:30:01+08:00'}],
            'comparison_input': {'sectors': quotes},
            'sectors': quotes[:20], 'sealed_pool_input': {'state': 'observed', 'items': []}}, {'topics': topics}


def test_live_full_quotes_include_three_nonranked_concepts_and_verified_zero_without_mutation():
    snapshot, reference = _live()
    before = deepcopy(snapshot)
    rows = rotation.live_rotation_sectors(snapshot, reference=reference, identity=(GENERATION, PRIOR))
    assert len(rows) == 33 and snapshot == before
    assert {row['name'] for row in rows[-3:]} == {'国企改革', 'AI应用', 'DeepSeek概念'}
    assert all(row['limit_up_count'] == 0 and row['limit_up_count_state'] == 'observed' for row in rows)
    assert all(len(row['member_reference_sha256']) == 64 for row in rows)
    assert all('stocks' not in row and 'instrument_ids' not in row for row in rows)


def test_live_unknown_members_or_pool_never_manufactures_zero_or_erases_prices():
    snapshot, reference = _live()
    snapshot['sealed_pool_input']['state'] = 'unknown'
    rows = rotation.live_rotation_sectors(snapshot, reference=reference, identity=(GENERATION, PRIOR))
    assert len(rows) == 33 and all(row['limit_up_count'] is None for row in rows)
    reference['topics'][0]['observed_date'] = '2026-11-06'
    rows = rotation.live_rotation_sectors(snapshot, reference=reference, identity=(GENERATION, PRIOR))
    assert rows[0]['change_pct'] == 1 and 'member_reference_sha256' not in rows[0]


def test_duplicate_or_invalid_index_quote_is_not_a_rotation_fact():
    snapshot, reference = _live()
    snapshot['comparison_input']['sectors'].append(snapshot['comparison_input']['sectors'][0])
    snapshot['comparison_input']['sectors'][1]['change_pct'] = 90
    rows = rotation.live_rotation_sectors(snapshot, reference=reference, identity=(GENERATION, PRIOR))
    assert len(rows) == 32 and '880000.TI' not in {row['instrument_id'] for row in rows}
    assert rows[0]['latest_price'] == 101.01 and rows[0]['change_pct'] is None
    snapshot['comparison_input']['sectors'] *= 50
    assert rotation.live_rotation_sectors(snapshot, reference=reference, identity=(GENERATION, PRIOR)) == []


def test_noon_full_projection_does_not_truncate_at_twenty_four(monkeypatch):
    snapshot, reference = _live()
    names = {row['topic_code']: row['name'] for row in reference['topics']}
    payload = {'sectors': [{**row, 'topic_code': row['instrument_id'], 'name': names[row['instrument_id']]}
                           for row in snapshot['comparison_input']['sectors']], 'stocks': []}
    monkeypatch.setattr('app.services.intraday_brief._noon_member_metadata', lambda *_: {})
    assert len(rotation.noon_rotation_sectors(payload)) == 33
    assert rotation.noon_rotation_sectors(payload)[-1]['name'] == 'DeepSeek概念'
    payload['sectors'][-1].pop('prev_close')
    row = rotation.noon_rotation_sectors(payload)[-1]
    assert row['latest_price'] > 0 and row['change_pct'] is not None and 'prev_close' not in row


def test_close_source_rejects_changed_generation_before_loading_any_other_source(monkeypatch):
    from types import SimpleNamespace
    market = SimpleNamespace(status=lambda: {'snapshot_id': 'changed', 'data_end': DAY})
    monkeypatch.setattr('app.services.intraday_members._bound_market', lambda *_: market)
    monkeypatch.setattr('app.services.market_supplement.load_supplement', lambda *_args, **_kwargs:
                        pytest.fail('must not load latest or changed source'))
    with pytest.raises(ValueError):
        rotation._close_source(DAY, GENERATION)


def test_close_is_bound_to_original_generation_and_prices_not_sample_averages(monkeypatch):
    sector = {'thscode': '886108.TI', 'name': '原代际板块', 'kind': 'concept', 'close': 101,
              'previous_close': 100, 'change_pct': 1, 'source': 'fuyao',
              'price_basis': 'none', 'quote_date': DAY}
    monkeypatch.setattr(rotation, '_close_source', lambda day, generation: ([sector], {}, set(), False))
    rows = rotation.close_rotation_sectors(DAY, GENERATION)
    assert rows[0]['change_pct'] == 1 and rows[0]['limit_up_count'] is None
    sector.pop('previous_close')
    assert rotation.close_rotation_sectors(DAY, GENERATION)[0]['latest_price'] == 101
    assert 'prev_close' not in rotation.close_rotation_sectors(DAY, GENERATION)[0]
    sector['quote_date'] = PRIOR
    assert rotation.close_rotation_sectors(DAY, GENERATION) == []


def test_nonranked_live_members_need_original_quote_and_verified_original_member_proof(monkeypatch):
    from types import SimpleNamespace
    from app.services import intraday_members as members
    from app.services.intraday_brief import sector_membership_fingerprint

    snapshot, _ = _live()
    snapshot.update(member_reference={'generation': GENERATION, 'date': PRIOR},
                    comparison_input={**snapshot['comparison_input'], 'stocks': [
                        {'instrument_id': '600001.SH', 'latest_price': 11, 'change_pct': 10}]})
    base = {'date': DAY, 'slot': '1030', 'snapshot_id': 'intraday_saved', 'topic_code': '886108.TI',
            'board': 'all', 'sort': 'change_pct', 'direction': 'desc', 'limit': 3, 'page': 1}
    monkeypatch.setattr(members.intraday, 'decode_snapshot', lambda _: snapshot)
    seen = []
    def proof(generation, end, code, *, observed, reference_sha):
        seen.append((generation, end, code, observed, reference_sha))
        return ['600001.SH'], PRIOR, True
    monkeypatch.setattr(members, '_topic_members', proof)
    monkeypatch.setattr(members, '_bound_market', lambda _: SimpleNamespace(
        instruments=lambda **_: [], reference=lambda: {}, status=lambda: {'data_end': PRIOR}))
    monkeypatch.setattr(members, 'suspensions_for_date', lambda _: {})
    result = members._live(base, {})
    assert result['state'] == 'available' and result['items'][0]['instrument_id'] == '600001.SH'
    assert seen == [(GENERATION, PRIOR, '886108.TI', None, None)]
    assert result['member_reference_sha256'] == sector_membership_fingerprint(
        GENERATION, PRIOR, '886108.TI', 'concept', PRIOR, ['600001.SH'])
    monkeypatch.setattr(members, '_topic_members', lambda *_, **__: (_ for _ in ()).throw(ValueError('bad SHA')))
    assert members._live(base, {})['state'] == 'unknown'
    snapshot['comparison_input']['sectors'] = []
    assert '唯一' in members._live(base, {})['unavailable_reason']

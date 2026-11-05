"""Synthetic frozen auction evidence: no collection, model or live filesystem IO."""
from copy import deepcopy
from datetime import datetime

import pytest

from app.services.auction_carryover_narrative import auction_carryover, auction_highlights
from app.services.intraday_narrative import build_section, attach_sections

DAY, PRIOR = '2026-11-05', '2026-11-04'
GENERATION, SHA = 'market-' + 'a' * 24, 'b' * 64
CODES = ['600001.SH', '600002.SH', '600003.SH', '600004.SH']


def packet():
    start = DAY + 'T09:25:35+08:00'
    payload = {'date': DAY, 'previous_date': PRIOR, 'collected_start_at': start,
        'collected_end_at': DAY + 'T09:25:38+08:00',
        'market_context': {'source_snapshot_id': GENERATION},
        'universe': {'items': [{'instrument_id': code} for code in CODES]},
        'quotes': {'items': [
            {'instrument_id': code, 'name': f'合成候选{i}', 'state': 'observed' if pct is not None else 'unknown',
             'auction_pct': pct, 'auction_price': (10 * (1 + pct / 100)) if pct is not None else None,
             'pre_close_price': 10, 'auction_volume': 100, 'auction_amount': 110000,
             'unit_check': {'state': 'verified'}} for i, (code, pct) in enumerate(zip(CODES, [10, -3, 5, None]))]},
        'previous_review': {'date': PRIOR, 'known_at_ms': int(datetime.fromisoformat(start).timestamp() * 1000),
            'recorded_at_ms': None, 'event_cohorts': {'limit_up': {'date': PRIOR, 'state': 'observed_sample',
                'items': [{'instrument_id': code, 'name': f'合成前日{i}', 'date': PRIOR,
                           'previous_close': 10, 'previous_board_count': height}
                          for i, (code, height) in enumerate(zip(CODES, [4, 4, 2, 1]))]}}}}
    topics = {'date': DAY, 'source_snapshot_id': GENERATION, 'source_sha256': SHA,
              'groups': [{'topic_code': '884001.TI', 'name': '合成光伏', 'kind': 'industry', 'source': 'fuyao',
                          'members_observed_date': PRIOR, 'instrument_ids': CODES[:3],
                          'source_snapshot_id': GENERATION, 'source_sha256': SHA}]}
    return payload, topics


def test_original_candidates_explain_divergence_and_reference_without_claiming_promotion():
    payload, topics = packet()
    frozen = deepcopy((payload, topics))
    context = auction_carryover(payload, topics)
    top = context['highest_candidates']
    assert (top['previous_board_count'], top['sample_count'], top['high_open'], top['low_open']) == (4, 2, 1, 1)
    assert context['prior_limit_up']['unknown_count'] == 1
    group = context['directions'][0]
    assert (group['sample_count'], group['high_open'], group['low_open'], group['average_auction_pct']) == (3, 2, 1, 4)
    assert context['price_reference']['count'] == 1 and context['price_reference']['confirmed_limit_price'] is False
    highlights = auction_highlights(context)
    assert len(highlights) == 3
    assert '最高4板' in highlights[0]['text'] and '高开1只、低开1只' in highlights[0]['text']
    assert '不能据此认定今日晋级' in highlights[0]['text']
    assert '不代表全板块或昨日主线排名' in highlights[1]['text']
    assert '09:35' in highlights[-1]['text'] and '触及常规涨停参考' in highlights[-1]['text']
    assert all(len(row['stocks']) <= 2 and set(row['stock_ids']) <= set(CODES) for row in highlights)
    assert not any('board_count' in row for item in highlights for row in item['stocks']), 'prior height never becomes today height'
    assert (payload, topics) == frozen


@pytest.mark.parametrize('damage', ['late_knowledge', 'late_prior_publish', 'prior_day', 'cohort_day', 'conflicting_event'])
def test_unknown_or_later_prior_evidence_does_not_become_a_carryover(damage):
    payload, topics = packet()
    review = payload['previous_review']
    if damage == 'late_knowledge':
        review['known_at_ms'] += 1000
    elif damage == 'late_prior_publish':
        review['recorded_at_ms'] = review['known_at_ms'] + 1000
    elif damage == 'prior_day':
        review['date'] = DAY
    elif damage == 'cohort_day':
        review['event_cohorts']['limit_up']['date'] = DAY
    else:
        review['event_cohorts']['limit_break'] = deepcopy(review['event_cohorts']['limit_up'])
    context = auction_carryover(payload, topics)
    assert context['state'] == 'unknown' and not auction_highlights(context)


@pytest.mark.parametrize('damage', ['duplicate_quote', 'wrong_pct', 'changed_previous_close', 'missing_quote'])
def test_unverified_current_quote_is_not_flat_or_touched_reference(damage):
    payload, topics = packet()
    quote = payload['quotes']['items'][0]
    if damage == 'duplicate_quote':
        payload['quotes']['items'].append(deepcopy(quote))
    elif damage == 'wrong_pct':
        quote['auction_pct'] = 3
    elif damage == 'changed_previous_close':
        payload['previous_review']['event_cohorts']['limit_up']['items'][0]['previous_close'] = 9
    else:
        quote.update(state='unknown', auction_price=None)
    context = auction_carryover(payload, topics)
    assert context['highest_candidates']['unknown_count'] == 1
    assert context['highest_candidates']['flat_open'] == 0
    assert context['price_reference']['count'] == 0
    assert CODES[0] not in [row['instrument_id'] for row in context['price_reference']['stocks']]
    for row in payload['quotes']['items']:
        row['unit_check'] = {'state': 'unknown'}
    assert auction_carryover(payload, topics)['price_reference']['count'] is None


@pytest.mark.parametrize('damage', ['generation', 'sha', 'future_members', 'duplicate_members', 'broad_attribute'])
def test_topic_context_never_uses_current_or_ambiguous_membership(damage):
    payload, topics = packet()
    row = topics['groups'][0]
    if damage == 'generation':
        topics['source_snapshot_id'] = 'current-market'
    elif damage == 'sha':
        row['source_sha256'] = 'c' * 64
    elif damage == 'future_members':
        row['members_observed_date'] = DAY
    elif damage == 'duplicate_members':
        row['instrument_ids'].append(CODES[0])
    else:
        row['name'] = '融资融券'
    context = auction_carryover(payload, topics)
    assert context['state'] == 'observed_sample' and context['directions'] == []
    assert context['highest_candidates']['previous_board_count'] == 4


def test_timeline_keeps_original_body_and_exact_auction_identity_cutoff(monkeypatch):
    payload, topics = packet()
    frame = {'slot': 'auction', 'id': 'original-auction', 'date': DAY, 'quality': 'complete',
             'collected_start_at': payload['collected_start_at'], 'collected_end_at': payload['collected_end_at'],
             '_ready': int(datetime.fromisoformat(payload['collected_end_at']).timestamp() * 1000),
             'source_binding': {'snapshot_id': 'original-auction', 'content_sha256': 'a' * 64, 'source_id': 'fuyao'},
             '_auction_carryover': auction_carryover(payload, topics)}
    item = {'slot': 'auction', 'body': '原已保存简报正文', 'evidence': [
        {'slot': 'auction', 'source_id': frame['id'], 'collected_end_at': frame['collected_end_at']}]}
    monkeypatch.setattr('app.services.intraday_narrative.load_frames', lambda *_: {'auction': frame})
    attach_sections(DAY, [item], [])
    assert item['body'] == '原已保存简报正文' and len(item['sections'][0]['highlights']) == 3
    section = build_section(frame, None)
    assert section['source_bindings']['current']['content_sha256'] == 'a' * 64
    assert section['source_bindings']['auction_members']['content_sha256'] == SHA
    frame['id'] = 'later-replacement'
    attach_sections(DAY, [item], [])
    assert item['sections'] == [] and item['body'] == '原已保存简报正文'

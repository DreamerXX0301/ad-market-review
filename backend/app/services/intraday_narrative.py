"""Small, deterministic timeline highlights from original saved observations only.

This is a read projection: existing brief bodies and capture JSON never change.
The raw inputs and frozen member lists are request-scoped and never serialized.
"""
from collections import Counter
from datetime import datetime

from fastapi import HTTPException

from app.services.noon_analysis import number

ADJACENT = {'1000': '0935', '1030': '1000', '1100': '1030', 'noon': '1100', '1330': 'noon',
            '1400': '1330', '1430': '1400', 'close': '1430'}


def _count(value, state):
    return value if type(value) is int and value >= (1 if state == 'partial' else 0) and state in ('observed', 'partial') else None


def _count_text(value, state):
    value = _count(value, state)
    return None if value is None else ('至少' if state == 'partial' else '') + str(value) + '只'


def _display_change_pp(value):
    """Match the displayed 0.01pp precision without changing source facts."""
    return float(f'{value:.2f}')


def _index(rows):
    counts = Counter(row.get('instrument_id') for row in rows if isinstance(row, dict))
    return {row['instrument_id']: row for row in rows if isinstance(row, dict)
            and row.get('instrument_id') and counts[row['instrument_id']] == 1}


def _comparable(left, right):
    from app.services.intraday_brief import sector_comparison_metadata

    return (bool(sector_comparison_metadata(left, source=left.get('quote_source')))
            and bool(sector_comparison_metadata(right, source=right.get('quote_source')))
            and all(left.get(key) == right.get(key) for key in
                    ('instrument_id', 'kind', 'prev_close', 'quote_source', 'quote_basis', 'price_basis', 'currency')))


def _member_change(current, previous):
    if (current.get('limit_up_count_state') != 'observed' or previous.get('limit_up_count_state') != 'observed'
            or not current.get('member_reference_sha256')
            or any(current.get(key) != previous.get(key) for key in
                   ('member_reference_sha256', 'member_count', 'member_date'))
            or current.get('member_list_complete') is not True or previous.get('member_list_complete') is not True):
        return None
    left = _count(current.get('limit_up_count'), 'observed')
    right = _count(previous.get('limit_up_count'), 'observed')
    return left - right if left is not None and right is not None else None


class _Members:
    def __init__(self, references=None, frame_inputs=None):
        self.references = {}
        self.original_references = references or {}
        self.frame_inputs = frame_inputs or {}
        self.samples = {}
        self.quote_indices = {}
        self.pool_indices = {}
        self.pool_conflicts = {}

    def reference(self, raw):
        from app.services.intraday_members import _bound_market, _member_provenance

        identity = _member_provenance(raw)
        if identity is None:
            return {}
        generation, end, _ = identity
        if identity not in self.references:
            if (generation, end) in self.original_references:
                self.references[identity] = self.original_references[generation, end]
            else:
                market = _bound_market(generation)
                self.references[identity] = (market.reference() if
                    (market.status().get('snapshot_id'), market.status().get('data_end')) == (generation, end) else {})
        return self.references[identity]

    def stocks(self, frame, sector, *, weakest=False, sealed_only=False):
        key = (frame['slot'], frame['id'], sector['instrument_id'], weakest, sealed_only)
        if key in self.samples:
            return self.samples[key]
        from app.services.intraday_board_changes import _quotes, _pools, _conflicts
        from app.services.intraday_brief import sector_membership_fingerprint

        raw, members, master = frame.get('_raw', {}), [], {}
        try:
            if frame['slot'] == 'noon':
                from app.services.noon_sector_views import _members

                original = next((row for row in raw.get('sectors', ()) if
                                 (row.get('topic_code') or row.get('instrument_id')) == sector['instrument_id']), {})
                proof, _, error = _members(raw, original, {row['instrument_id']: row for row in raw.get('stocks', ())})
                if not error and proof:
                    members = proof['members']
                quote_frame = {'comparison_input': {'stocks': raw.get('stocks', ())}}
            elif frame['slot'] in ('auction', 'close'):
                rows = next((row.get('stocks', []) for row in frame.get('sectors', ())
                             if row['instrument_id'] == sector['instrument_id']), [])
                if sealed_only:
                    rows = [row for row in rows if row.get('limit_up_state', row.get('state')) == 'sealed']
                self.samples[key] = sorted(rows, key=lambda row: (row.get('change_pct') is None,
                    (row.get('change_pct') or 0) * (1 if weakest else -1), row['instrument_id']))[:2]
                return self.samples[key]
            else:
                reference = self.reference(raw)
                master = reference.get('master', {})
                candidates = [row for row in reference.get('topics', ()) if
                              row.get('topic_code') == sector['instrument_id'] and row.get('kind') == sector.get('kind')
                              and row.get('source') == 'fuyao']
                if len(candidates) == 1:
                    topic = candidates[0]
                    values = topic.get('instrument_ids')
                    identity = raw['member_reference']
                    if (isinstance(values, list) and 0 < len(values) <= 10000 and len(set(values)) == len(values)
                            and topic.get('members_complete') is True
                            and sector_membership_fingerprint(identity['generation'], identity['date'],
                                sector['instrument_id'], sector['kind'], topic['observed_date'], values)
                                == sector.get('member_reference_sha256')):
                        members = values
                quote_frame = raw
            frame_key = (frame['slot'], frame['id'])
            if frame_key not in self.quote_indices:
                pool_frame = {**raw, 'slot': frame['slot']}
                prepared = self.frame_inputs.get(frame['id'])
                if (prepared and prepared[:3] == (frame['date'], frame['slot'], frame.get('collected_end_at'))
                        and prepared[3] is raw.get('comparison_input')):
                    self.quote_indices[frame_key], self.pool_indices[frame_key], _ = prepared[4]
                else:
                    self.quote_indices[frame_key] = _quotes(quote_frame)
                    self.pool_indices[frame_key] = _pools(pool_frame)
                self.pool_conflicts[frame_key] = _conflicts(pool_frame)
            quotes, pools = self.quote_indices[frame_key], self.pool_indices[frame_key]
            sealed, broken = pools['limit_up'][0], pools['limit_break'][0]
            heights = {row['instrument_id']: group.get('board_count') for group in frame.get('ladder', {}).get('groups', ())
                       for row in group.get('items', ())}
            known = [*raw.get('sealed_stocks', ()), *(row for group in raw.get('board_stocks', {}).values()
                      for row in group.get('items', ()))]
            for row in known:
                height = row.get('continue_day_cnt', row.get('board_count'))
                if type(height) is int and height > 0 and row.get('instrument_id') in sealed:
                    heights[row['instrument_id']] = height
            rows = []
            for code in members:
                if code not in quotes:
                    continue
                quote = quotes[code]
                state = 'sealed' if code in sealed else 'broken' if code in broken else None
                pool_quote = sealed.get(code, broken.get(code, {}))
                pool_price = number(pool_quote.get('latest_price'), positive=True)
                if code in self.pool_conflicts[frame_key] or (state and (
                        (frame['slot'] == 'noon' and pool_price is None)
                        or (pool_price is not None and abs(pool_price - quote['latest_price']) > .011))):
                    state = 'unknown'
                if sealed_only and state != 'sealed':
                    continue
                height = sealed.get(code, {}).get('continue_day_cnt', heights.get(code))
                rows.append({'instrument_id': code, 'name': quote.get('name') or master.get(code, {}).get('name') or code,
                             'latest_price': quote['latest_price'], 'change_pct': quote['change_pct'],
                             'board_count': height if state == 'sealed' and type(height) is int and height > 0 else None,
                             'limit_up_state': state})
            rows.sort(key=lambda row: (row['change_pct'] * (1 if weakest else -1), row['instrument_id']))
            self.samples[key] = rows[:2]
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            self.samples[key] = []
        return self.samples[key]


def _stock_text(rows):
    def label(row):
        state, height = row.get('limit_up_state', row.get('state')), row.get('board_count')
        badge = (f'{height}连板' if type(height) is int and height > 1 else '首板' if type(height) is int and height == 1 else '封板') if state == 'sealed' else '炸板' if state == 'broken' else None
        return row['name'] + f' {row["change_pct"]:+.2f}%' + (f'（{badge}）' if badge else '')
    valid = [row for row in rows if row.get('name') and number(row.get('change_pct')) is not None]
    return '；代表样本：' + '、'.join(label(row) for row in valid[:2]) if valid else '；代表个股资料未核验'


def _concentrations(frame, previous):
    """Counts are over the explicitly listed, classified group rows, never the entire pool."""
    labels = {'new_sealed': '新入封板', 'new_broken': '新增炸板样本', 'resealed': '核实回封'}
    result = []
    for key, label in labels.items():
        group = frame.get('board_changes', {}).get('groups', {}).get(key, {})
        items = group.get('items', [])
        if group.get('state') not in ('observed', 'partial') or not items:
            continue
        memberships, classified = {}, set()
        for row in items:
            code = row['instrument_id']
            for theme in row.get('themes', ()):
                if theme.get('kind') not in ('industry', 'concept') or not theme.get('name') or not theme.get('member_date'):
                    continue
                memberships.setdefault((theme['kind'], theme['name']), {})[code] = row
                classified.add(code)
        ranked = sorted(memberships.items(), key=lambda pair: (pair[0][0] != 'industry', -len(pair[1]), pair[0][1]))
        industries = [pair for pair in ranked if pair[0][0] == 'industry'][:2]
        concepts = [pair for pair in ranked if pair[0][0] == 'concept'][:max(0, 3 - len(industries))]
        chosen = industries + concepts
        if not chosen:
            continue
        topics = [{'kind': kind, 'name': name, 'count': len(rows), 'stock_ids': sorted(rows)[:2]}
                  for (kind, name), rows in chosen]
        text = f'本段{label}' + (_count_text(group.get('count'), group['state']) or '数量待核验')
        if group.get('state') != 'observed' or len(items) != group.get('count'):
            text += f'（已列{len(items)}只）'
        if len(classified) < len(items):
            text += f'，其中{len(classified)}只已核实归属'
        if industries and max(len(rows) for _, rows in industries) == 1 and len(classified) > 3:
            text += '；行业较分散，已核实样本中'
        else:
            text += '；已核实样本中归属较多的是'
        text += '、'.join(('行业' if row['kind'] == 'industry' else '题材') + row['name'] + f' {row["count"]}只' for row in topics)
        samples = list(max(chosen, key=lambda pair: len(pair[1]))[1].values())[:2]
        text += _stock_text(samples) + '。题材可重叠，分布仅覆盖已列名单。'
        result.append({'id': f'{frame["slot"]}.{key}.topics', 'kind': 'broken' if key == 'new_broken' else 'sealed',
                       'text': text, 'stock_ids': [row['instrument_id'] for row in samples], 'stocks': samples,
                       'topic_counts': topics, 'listed_stock_count': len(items), 'classified_stock_count': len(classified)})
    return result


def _market_summary(frame):
    market, texts = frame.get('market', {}), []
    for row in market.get('indices', ())[:2]:
        if number(row.get('change_pct')) is not None and row.get('name'):
            texts.append(f'{row["name"]}{row["change_pct"]:+.2f}%')
    breadth = market.get('breadth', {})
    if all(type(breadth.get(key)) is int and breadth[key] >= 0 for key in ('up', 'down')):
        words = ('高开', '低开') if frame['slot'] == 'auction' else ('上涨', '下跌')
        texts.append(('候选' if frame['slot'] == 'auction' else '样本') + f'{words[0]}{breadth["up"]}只、{words[1]}{breadth["down"]}只')
    amount = number(market.get('turnover', {}).get('amount'), nonnegative=True)
    if amount is not None:
        texts.append(f'累计成交{amount / 100_000_000:,.2f}亿元')
    interval = frame.get('interval_amount', {})
    if number(interval.get('amount'), nonnegative=True) is not None:
        texts.append(f'本段同股成交{interval["amount"] / 100_000_000:,.2f}亿元')
    difference = number(interval.get('change_from_previous'))
    if difference is not None and interval.get('previous_date'):
        texts.append('可比口径较昨日同段' + ('持平' if difference == 0 else
                     ('放量' if difference > 0 else '缩量') + f'{abs(difference) / 100_000_000:,.2f}亿元'))
    return '，'.join(texts) + '。' if texts else '该时点缺少可核验的市场概况。'


def _sealed_topics(frame, sectors, members):
    """A small within-coverage ranking; broad attribute concepts do not crowd out industries."""
    eligible = [row for row in sectors if row.get('kind') in ('industry', 'concept')
                and row.get('quote_source') == 'fuyao' and row.get('name')
                and type(row.get('member_count')) is int and 0 < row['member_count'] <= 300
                and (_count(row.get('limit_up_count'), row.get('limit_up_count_state')) or 0) > 0]
    eligible.sort(key=lambda row: (-row['limit_up_count'], -(number(row.get('change_pct')) or 0), row['instrument_id']))
    industries = [row for row in eligible if row['kind'] == 'industry'][:2]
    selected = industries + [row for row in eligible if row['kind'] == 'concept'][:3 - len(industries)]
    if not selected:
        return None
    topics = [{'sector_id': row['instrument_id'], 'name': row['name'], 'kind': row['kind'],
               'count': row['limit_up_count'], 'state': row['limit_up_count_state']} for row in selected]
    samples = []
    for row in selected:
        for sample in members.stocks(frame, row, sealed_only=True):
            if sample.get('limit_up_state') == 'sealed' and sample['instrument_id'] not in {value['instrument_id'] for value in samples}:
                samples.append(sample)
            if len(samples) == 2:
                break
        if len(samples) == 2:
            break
    text = '当前已核验板块中，窄行业/具体题材封板家数靠前：' + '、'.join(
        row['name'] + ' 封板' + _count_text(row['count'], row['state']) for row in topics)
    return {'text': text + '。成员可重叠，不代表全市场完整排名。',
            'topics': topics, 'stocks': samples}


def build_section(frame, previous, *, members=None):
    """At most two leaders, two improving/weakening directions and two example stocks."""
    from app.services.intraday_brief import LABELS

    members = members or _Members()
    slot = frame['slot']
    result = {'slot': slot, 'label': frame.get('display_label', LABELS[slot]), 'status': frame.get('quality', 'partial'),
              'summary': _market_summary(frame), 'highlights': [],
              'source_bindings': {'current': frame.get('source_binding', {})}}
    if previous:
        result['source_bindings']['previous'] = previous.get('source_binding', {})
    if slot == 'auction':
        from app.services.auction_carryover_narrative import auction_highlights

        context = frame.get('_auction_carryover', {})
        result['highlights'].extend(auction_highlights(context))
        if context.get('member_source_binding'):
            result['source_bindings']['auction_members'] = context['member_source_binding']
    sectors = _index(frame.get('rotation_sectors', frame.get('sectors', ())))
    prior = _index(previous.get('rotation_sectors', previous.get('sectors', ()))) if previous else {}
    comparable = (previous is not None and previous['slot'] == ADJACENT.get(slot)
                  and frame.get('date') == previous.get('date')
                  and frame.get('source_binding', {}).get('source_id') == previous.get('source_binding', {}).get('source_id') == 'fuyao'
                  and previous.get('collected_end_at', '') < frame.get('collected_start_at', ''))
    def direction(row, kind):
        old = prior.get(row['instrument_id'], {})
        delta = row['change_pct'] - old['change_pct'] if comparable and _comparable(row, old) else None
        samples = members.stocks(frame, row, weakest=kind == 'weakening')[:2]
        text = f'{row["name"]} {row["change_pct"]:+.2f}%'
        if slot == 'auction':
            text += '（竞价候选均值）'
        if delta is not None:
            shown_delta = _display_change_pp(delta)
            previous_label = previous.get('display_label', LABELS[previous['slot']]).split()[0]
            if shown_delta == 0:
                text += f'，较{previous_label}持平'
            else:
                verb = '走强' if shown_delta >= .3 else '回落' if shown_delta <= -.3 else '小幅抬升' if shown_delta > 0 else '小幅回落'
                if shown_delta < 0 and row['change_pct'] > 0 and old['change_pct'] > 0:
                    verb = '涨幅收窄' if shown_delta <= -.3 else '涨幅小幅收窄'
                elif shown_delta > 0 and row['change_pct'] < 0 and old['change_pct'] < 0:
                    verb = '跌幅收窄' if shown_delta >= .3 else '跌幅小幅收窄'
                text += f'，较{previous_label}{verb}{abs(shown_delta):.2f}个百分点'
            text += f'（{old["change_pct"]:+.2f}% → {row["change_pct"]:+.2f}%）'
        state = row.get('limit_up_count_state', 'unknown')
        count = _count_text(row.get('limit_up_count'), state)
        if count is not None:
            text += '，核验封板' + count
        count_delta = _member_change(row, old) if delta is not None else None
        if count_delta is not None:
            text += ('，同成员封板数较前档持平' if count_delta == 0 else
                     f'，同成员封板较前档{"增加" if count_delta > 0 else "减少"}{abs(count_delta)}只')
        entry = {'id': f'{slot}.{kind}.{row["instrument_id"]}', 'kind': kind,
                 'text': text + _stock_text(samples) + '。', 'sector_id': row['instrument_id'],
                 'sector_name': row['name'],
                 'stock_ids': [row['instrument_id'] for row in samples], 'stocks': samples,
                 'change_pct': row['change_pct'], 'change_pp': delta,
                 'limit_up_count': _count(row.get('limit_up_count'), state),
                 'limit_up_count_state': state, 'limit_up_change': count_delta}
        result['highlights'].append(entry)
    ranked = [row for row in sectors.values() if number(row.get('change_pct')) is not None and row.get('name')]
    ranked.sort(key=lambda row: (-row['change_pct'], row['instrument_id']))
    for row in ranked[:2]:
        direction(row, 'auction' if slot == 'auction' else 'leading')
    changes = [(row['change_pct'] - prior[row['instrument_id']]['change_pct'], row) for row in ranked
               if comparable and row['instrument_id'] in prior and _comparable(row, prior[row['instrument_id']])]
    for kind, selected in (('strengthening', sorted((v for v in changes if _display_change_pp(v[0]) > 0), key=lambda v: (-v[0], v[1]['instrument_id']))),
                           ('weakening', sorted((v for v in changes if _display_change_pp(v[0]) < 0), key=lambda v: (v[0], v[1]['instrument_id'])))):
        for _, row in selected[:2]:
            direction(row, kind)
    if slot != 'auction' and not comparable:
        result['comparison_note'] = '缺少相邻档的同口径资料，不跨缺档推断本段强弱。'
    ladder = frame.get('ladder', {})
    old_ladder = previous.get('ladder', {}) if comparable else {}
    for kind, current_pool, old_pool, label in (
            ('sealed', {'state': ladder.get('state'), 'count': ladder.get('sealed_count')},
             {'state': old_ladder.get('state'), 'count': old_ladder.get('sealed_count')}, '封板'),
            ('broken', ladder.get('broken_board', {}), old_ladder.get('broken_board', {}), '炸板')):
        state = current_pool.get('state', 'unknown')
        count = _count_text(current_pool.get('count'), state)
        if count is None:
            continue
        text = f'本档核验{label}{count}'
        before = _count(old_pool.get('count'), old_pool.get('state'))
        delta = current_pool['count'] - before if comparable and state == old_pool.get('state') == 'observed' and before is not None else None
        if delta is not None:
            text += f'，较{previous.get("display_label", LABELS[previous["slot"]]).split()[0]}' + (
                '持平' if delta == 0 else f'{"增加" if delta > 0 else "减少"}{abs(delta)}只')
        elif comparable:
            text += '，池覆盖不足，不计算家数增减'
        samples = (current_pool.get('items', []) if kind == 'broken' else
                   [row for group in ladder.get('groups', []) for row in group.get('items', [])])[:2]
        result['highlights'].append({'id': f'{slot}.{kind}', 'kind': kind,
                                     'text': text + (_stock_text(samples) if current_pool['count'] else '') + '。',
                                     'count': current_pool['count'], 'state': state, 'change_count': delta,
                                     'stock_ids': [row['instrument_id'] for row in samples], 'stocks': samples})
    if comparable:
        # Enrich the existing two pool paragraphs, keeping <=8 highlights per stage.
        concentrations = _concentrations(frame, previous)
        for kind in ('sealed', 'broken'):
            entry = next((row for row in result['highlights'] if row['kind'] == kind), None)
            related = [row for row in concentrations if row['kind'] == kind]
            if entry is None or not related:
                continue
            heading = entry['text'].split('；代表样本')[0].split('；代表个股')[0].rstrip('。') + '。'
            entry['text'] = heading + related[0]['text']
            entry.update(stocks=related[0]['stocks'], stock_ids=related[0]['stock_ids'],
                         topic_counts=related[0]['topic_counts'],
                         listed_stock_count=related[0]['listed_stock_count'],
                         classified_stock_count=related[0]['classified_stock_count'])
            if len(related) > 1:
                entry['text'] += related[1]['text'].split('；代表样本')[0] + '。'
    sealed = next((row for row in result['highlights'] if row['kind'] == 'sealed'), None)
    if sealed is not None and (topics := _sealed_topics(frame, sectors.values(), members)):
        sealed['text'] += topics['text']
        sealed['current_sector_counts'] = topics['topics']
        if not sealed['stocks'] and topics['stocks']:
            sealed.update(stocks=topics['stocks'], stock_ids=[row['instrument_id'] for row in topics['stocks']])
            sealed['text'] += _stock_text(topics['stocks']) + '。'
    return result


def load_frames(day, anchors, displayed_snapshots=None, *, read_context=None):
    """Read bounded original bodies once. Never re-fetch or use current names as fallback."""
    from app.services import intraday_brief as brief, intraday_service as live
    from app.services.intraday_board_changes import attach_changes, read_noon_row, noon_frame
    from app.services.intraday_rotation import noon_rotation_sectors

    frames, snapshots, records = {}, [], []
    shared = read_context if read_context and read_context.get('date') == day else None
    with brief.connect() as connection:
        tables = {row[0] for row in connection.execute('SELECT name FROM sqlite_master WHERE type=\'table\'')}
        if shared is not None:
            records = [row for row in shared['rows'] if row['status'] in ('succeeded', 'partial')
                       and isinstance(row['payload_json'], str)
                       and len(row['payload_json'].encode()) <= live.MAX_PAYLOAD]
        elif {'market_intraday_capture', 'job'} <= tables:
            records = connection.execute('''SELECT c.*,j.status,j.finished_at_ms FROM market_intraday_capture c
                JOIN job j USING(job_id) WHERE c.trade_date=? AND j.status IN ('succeeded','partial')
                AND length(CAST(c.payload_json AS BLOB))<=?''', (day, live.MAX_PAYLOAD)).fetchall()
        noon_row = shared['noon_row'] if shared is not None else read_noon_row(connection, day)
        noon_clock = (shared.get('noon_clock') if shared is not None else
                      live._noon_schedule(connection, day, None)[0] if {'market_noon_capture', 'job_event'} <= tables else None)
        auction = connection.execute('''SELECT * FROM market_auction_snapshot WHERE trade_date=?
            AND length(CAST(payload_json AS BLOB))<=?''', (day, brief.MAX_NOON_BYTES)).fetchone() if 'market_auction_snapshot' in tables else None
    for row in records:
        try:
            value = shared['snapshots'].get(row['snapshot_id']) if shared is not None else None
            value = value if value is not None else live.decode_snapshot(row)
            window = next((item for item in live.slots_for_version(value['version']) if item['key'] == value['slot']), None)
            start, end = (datetime.fromisoformat(value[field]) for field in ('collected_start_at', 'collected_end_at'))
            if (not window or start.tzinfo is None or end.tzinfo is None or not row['finished_at_ms']
                    or not window['start'] <= start.astimezone(brief.CHINA).strftime('%H:%M') <= end.astimezone(brief.CHINA).strftime('%H:%M') < window['end']
                    or brief._stamp(value['collected_start_at'], day) > brief._stamp(value['collected_end_at'], day)
                    or not brief._stamp(value['collected_end_at'], day) <= row['finished_at_ms'] <= brief.now_ms()):
                continue
            snapshots.append(value)
        except (ValueError, TypeError, KeyError, AttributeError, HTTPException):
            continue
    existing = {row['id']: row for row in displayed_snapshots or []}
    by_id = {row['snapshot_id']: row for row in records}
    missing = []
    for value in snapshots:
        shown = existing.get(value['id'], {})
        if (shown.get('slot'), shown.get('date'), shown.get('collected_end_at')) == (
                value['slot'], day, value['collected_end_at']) and 'rotation_sectors' in shown and (
                shown.get('board_changes', {}).get('source_bindings', {}).get('current', {}).get('content_sha256')
                == by_id[value['id']]['content_sha256']):
            value['rotation_sectors'] = shown['rotation_sectors']
            value['board_changes'] = shown.get('board_changes', value.get('board_changes', {}))
        else:
            missing.append(value)
    if missing:
        attach_changes(snapshots, records, noon_row)
        # A matched projection's enrichment is already tied to this exact SHA.
        for value in snapshots:
            if value not in missing:
                value['board_changes'] = existing[value['id']].get('board_changes', {})
        live._attach_saved_stock_references(missing)
    for value in snapshots:
        row = by_id[value['id']]
        frames[value['slot']] = {**brief._compact_live(value), 'date': day,
            'collected_start_at': value['collected_start_at'], 'rotation_sectors': value.get('rotation_sectors', []),
            '_raw': value, '_ready': row['finished_at_ms'], 'source_binding': {
                'source_id': 'fuyao', 'snapshot_id': value['id'], 'content_sha256': row['content_sha256']},
            'board_changes': value.get('board_changes', {})}
    if noon_row is not None:
        try:
            payload = brief._verified_noon_payload(noon_row)
            current = noon_frame(noon_row)
            anchor = next((row for row in anchors if row['slot'] == 'noon' and row['id'] == noon_row['snapshot_id']), None)
            if current:
                frames['noon'] = {**(anchor or brief._noon_anchor(noon_row)), 'date': day,
                    'display_label': (noon_clock + ' 午评') if noon_clock else '午评',
                    'rotation_sectors': (anchor['rotation_sectors'] if anchor and 'rotation_sectors' in anchor
                                         else noon_rotation_sectors(current)), '_raw': current,
                    '_ready': max(brief._stamp(payload['collected_end_at'], day), noon_row['recorded_at_ms']),
                    'source_binding': {'source_id': 'fuyao', 'snapshot_id': noon_row['snapshot_id'], 'content_sha256': noon_row['content_sha256']}}
        except (ValueError, TypeError, KeyError, IndexError, AttributeError):
            pass
    if auction is not None:
        try:
            from app.services.auction_integrity import validate_snapshot
            from app.services.auction_topic_summary import build_topic_summary
            from app.services.auction_carryover_narrative import auction_carryover

            payload = validate_snapshot(auction)
            frames['auction'] = {**brief._auction_anchor(auction), 'date': day,
                '_auction_carryover': auction_carryover(payload, build_topic_summary(payload)),
                '_ready': max(brief._stamp(auction['collected_end_at'], day), auction['recorded_at_ms']),
                'source_binding': {'source_id': 'fuyao', 'snapshot_id': auction['snapshot_id'], 'content_sha256': auction['content_sha256']}}
        except (ValueError, TypeError, KeyError, AttributeError):
            pass
    for anchor in anchors:
        if anchor['slot'] == 'close' and anchor.get('rotation_sectors'):
            frames['close'] = {**anchor, 'date': day, '_ready': brief._stamp(anchor.get('published_at', anchor['collected_end_at']), day),
                               'source_binding': anchor.get('source_binding') or {
                                   'source_id': 'fuyao', 'snapshot_id': anchor['id'], 'basis': 'verified_closing_generation'}}
    return frames


def attach_sections(day, briefs, anchors, snapshots=None, *, read_context=None):
    from app.services.intraday_brief import ORDER, TIMELINE, _stamp

    if not briefs:
        return
    frames = (load_frames(day, anchors, snapshots, read_context=read_context) if read_context is not None
              else load_frames(day, anchors, snapshots))
    shared = read_context if read_context and read_context.get('date') == day else None
    members = _Members(shared.get('references'), shared.get('frame_inputs')) if shared is not None else _Members()
    sections = {}
    def matches(item, slot):
        frame = frames.get(slot)
        return frame is not None and any(
            fact.get('slot') == slot and fact.get('source_id') == frame['id']
            and fact.get('collected_end_at') == frame['collected_end_at']
            for fact in item.get('evidence', ()) if isinstance(fact, dict))
    for item in briefs:
        target = frames.get(item.get('slot'))
        if target is None or not matches(item, item['slot']):
            item['sections'] = []
            continue
        cutoff = _stamp(target.get('published_at', target['collected_end_at']), day)
        usable = {key for key in frames if matches(item, key) and ORDER[key] <= ORDER[item['slot']]
                  and (key == item['slot'] or frames[key].get('_ready', cutoff + 1) <= cutoff)}
        item['sections'] = []
        for key, _ in TIMELINE:
            if key not in usable:
                continue
            frame = frames[key]
            own_cutoff = _stamp(frame.get('published_at', frame['collected_end_at']), day)
            previous_key = ADJACENT.get(key)
            prior = frames.get(previous_key) if previous_key in usable else None
            if prior is not None and prior.get('_ready', own_cutoff + 1) > own_cutoff:
                prior = None
            cache_key = (key, prior['id'] if prior else None)
            if cache_key not in sections:
                sections[cache_key] = build_section(frame, prior, members=members)
            item['sections'].append(sections[cache_key])

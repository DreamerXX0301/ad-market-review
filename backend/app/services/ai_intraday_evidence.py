"""Small AI enrichments pinned to the selected read-only database and original files."""
from collections import Counter
import sqlite3

from app.analytics.market_storage import safe_object, verify_immutable_file
from app.infrastructure.config import published_dir
from app.services import noon_membership_context as membership
from app.services.auction_followup import model_input_allowed


def original_members(connection, snapshot, cache):
    from app.services.intraday_members import _member_provenance

    try:
        source = _member_provenance(snapshot)
    except (ValueError, KeyError, TypeError, AttributeError):
        source = None
    if source is None:
        return {}, ('', ''), None
    if source in cache:
        return cache[source]
    generation, end, expected_sha = source
    result = {}, ('', ''), None
    try:
        # Use this tool's selected read source, not app.core.connect/current head.
        rows = connection.execute('''SELECT s.snapshot_id,s.data_end,s.manifest_path,s.manifest_sha256,
            a.sha256,a.byte_count FROM dataset_snapshot s JOIN snapshot_artifact a USING(snapshot_id)
            WHERE s.module='topics' AND s.state='published'
              AND json_extract(s.lineage_json,'$.market_snapshot_id')=?
              AND a.object_key=? AND a.role='reference.json' LIMIT 3''',
            (generation, generation + '/reference.json')).fetchall()
        if len(rows) != 1:
            raise ValueError('原成员发布锚点缺失或不唯一')
        anchor = dict(rows[0])
        if (not membership._day(anchor['data_end']) or anchor['data_end'] > end
                or expected_sha is None and anchor['data_end'] != end
                or expected_sha not in (None, anchor['sha256'])):
            raise ValueError('原成员截止日或SHA变化')
        root = published_dir().resolve()
        manifest_path = safe_object(root, anchor['manifest_path'])
        size = manifest_path.stat().st_size
        if not 0 < size <= membership.MAX_MANIFEST_BYTES:
            raise ValueError('原发布清单超过有界读取范围')
        verify_immutable_file(manifest_path, anchor['manifest_sha256'], size)
        manifest = membership._read_json(manifest_path, membership.MAX_MANIFEST_BYTES)
        if (manifest.get('snapshot_id') != anchor['snapshot_id'] or manifest.get('module') != 'topics'
                or manifest.get('lineage', {}).get('market_snapshot_id') != generation):
            raise ValueError('原成员发布身份不符')
        entries = [row for row in manifest.get('artifacts', ()) if isinstance(row, dict)
                   and row.get('object_key') == generation + '/reference.json' and row.get('role') == 'reference.json']
        if len(entries) != 1 or any(entries[0].get(key) != anchor[key] for key in ('sha256', 'byte_count')):
            raise ValueError('原参考文件未与发布锚点对账')
        path = safe_object(root, generation + '/reference.json')
        if type(anchor['byte_count']) is not int or not 0 < anchor['byte_count'] <= membership.MAX_REFERENCE_BYTES:
            raise ValueError('原参考文件超过有界读取范围')
        verify_immutable_file(path, anchor['sha256'], anchor['byte_count'])
        document = membership._read_json(path, membership.MAX_REFERENCE_BYTES)
        if not model_input_allowed(document):
            raise ValueError('原成员资料禁止AI处理')
        if document.get('storage_version') in ('market-reference-parts-v1', 'market-reference-parts-v2'):
            part = document.get('fields', {}).get('topics') or {}
            parts = connection.execute('''SELECT sha256,byte_count FROM snapshot_artifact
                WHERE snapshot_id=? AND object_key=? LIMIT 2''', (anchor['snapshot_id'], part.get('object_key'))).fetchall()
            if (len(parts) != 1 or parts[0]['sha256'] != part.get('sha256')
                    or parts[0]['byte_count'] != part.get('bytes')):
                raise ValueError('题材分片未与原SQLite发布锚点对账')
        topics, topics_sha = membership._load_topics(generation, end)
        verify_immutable_file(path, anchor['sha256'], anchor['byte_count'])
        result = ({'topics': topics}, (generation, end), {'generation': generation, 'date': end,
            'reference_sha256': anchor['sha256'], 'topics_sha256': topics_sha,
            'publication_sha256': anchor['manifest_sha256']})
    except (OSError, ValueError, TypeError, KeyError, AttributeError, sqlite3.Error):
        pass
    cache[source] = result
    return result


def prepare_frame(connection, snapshot, query, cache, *, previous=None):
    """Compute metrics only for matched/full-quote or saved shortlisted directions."""
    from app.services.intraday_rotation import live_rotation_sectors, attach_sector_metrics
    from app.services.intraday_board_changes import frame_quotes, attach_current_states, stock_rows
    from app.services.intraday_discovery import SECTOR_LISTS
    from app.services.intraday_feedback import feedback_stock_rows, stock_themes

    reference, identity, binding = original_members(connection, snapshot, cache.setdefault('sources', {}))
    topics = reference.get('topics', ())
    names = {row.get('topic_code'): str(row.get('name') or '').casefold() for row in topics}
    visible = {row.get('instrument_id') for key in SECTOR_LISTS for row in snapshot.get(key, ())}
    original = snapshot.get('comparison_input', {})
    quotes = [row for row in original.get('sectors', ()) if isinstance(row, dict) and (
        row.get('instrument_id') in visible or query and any(query in value for value in (
            str(row.get('instrument_id') or '').casefold(), str(row.get('name') or '').casefold(),
            names.get(row.get('instrument_id'), ''))))]
    projection = {**snapshot, 'comparison_input': {**original, 'sectors': quotes}}
    snapshot['rotation_sectors'] = live_rotation_sectors(projection, reference=reference, identity=identity,
        member_cache=cache.setdefault('members', {}), quote_index=frame_quotes(snapshot, cache))
    attach_sector_metrics(snapshot)
    attach_current_states(snapshot)
    stocks = [*feedback_stock_rows(snapshot), *snapshot.get('stocks', ()), *snapshot.get('candidates', ()),
              *stock_rows(snapshot)]
    stocks.extend(row for key in SECTOR_LISTS for board in snapshot.get(key, ()) for row in board.get('candidates', ()))
    sectors, members = membership_inputs(topics, identity[1])
    themes = stock_themes(sectors, members, [row.get('instrument_id') for row in stocks])
    for row in stocks:
        row['_ai_observed_date'] = snapshot['date']
        if not row.get('themes') and row.get('instrument_id') in themes:
            row['themes'] = themes[row['instrument_id']]
    snapshot['_ai_stocks'] = {row.get('instrument_id'): row for row in stocks}
    attach_change_distributions(snapshot, previous, sectors, members, cache)
    return binding


def membership_inputs(topics, limit_day):
    sectors, members = [], {}
    counts = Counter(row.get('topic_code') for row in topics if isinstance(row, dict))
    for topic in topics:
        symbols = topic.get('instrument_ids') if isinstance(topic, dict) else None
        if (isinstance(topic, dict) and topic.get('source') == 'fuyao'
                and counts[topic.get('topic_code')] == 1 and topic.get('kind') in ('industry', 'concept')
                and membership.SECTOR.fullmatch(str(topic.get('topic_code')))
                and membership._day(topic.get('observed_date')) and topic['observed_date'] <= limit_day
                and isinstance(symbols, list) and 0 < len(symbols) <= membership.MAX_MEMBERS
                and all(isinstance(code, str) and membership.SYMBOL.fullmatch(code) for code in symbols)
                and len(symbols) == len(set(symbols))):
            code = topic.get('topic_code')
            sectors.append({'instrument_id': code, 'name': topic.get('name'), 'kind': topic.get('kind')})
            members[code] = {'date': topic['observed_date'], 'instrument_ids': topic['instrument_ids'],
                             'complete': topic.get('members_complete') is True}
    return sectors, members


def attach_change_distributions(snapshot, previous, sectors, members, cache):
    """Classify every price-checked pool difference before the UI's 32-row slice."""
    from app.services.intraday_board_changes import _pools, frame_quotes

    groups = snapshot.get('board_changes', {}).get('groups', {})
    if not previous or not groups:
        return
    current, before = _pools(snapshot), _pools(previous)
    quotes, old_quotes = frame_quotes(snapshot, cache), frame_quotes(previous, cache)
    up, _ = current['limit_up']; old_up, old_up_exact = before['limit_up']
    broken, _ = current['limit_break']; old_broken, old_broken_exact = before['limit_break']
    down, _ = current['limit_down']; old_down, old_down_exact = before['limit_down']
    candidates = {'new_sealed': set(up) - old_up.keys() if old_up_exact else set(up) & old_broken.keys(),
        'new_broken': set(broken) - old_broken.keys() if old_broken_exact else set(broken) & old_up.keys(),
        'new_limit_down': set(down) - old_down.keys() if old_down_exact else set(),
        'opened': set(old_up) & broken.keys(), 'resealed': set(old_broken) & up.keys()}
    for key, group in groups.items():
        ids = {code for code in candidates.get(key, ()) if code in quotes
               and (code not in old_quotes or quotes[code]['prev_close'] == old_quotes[code]['prev_close'])
               and (key not in ('opened', 'resealed') or code in old_quotes)}
        total = group.get('count')
        if total is None or len(ids) != total:
            continue
        classified, dimensions = set(), {'industries': [], 'concepts': []}
        for sector in sectors:
            proof = members.get(sector['instrument_id'], {})
            hits = ids.intersection(proof.get('instrument_ids', ()))
            if hits:
                classified.update(hits)
                dimension = 'industries' if sector['kind'] == 'industry' else 'concepts'
                dimensions[dimension].append({'topic_code': sector['instrument_id'], 'name': sector['name'],
                    'count': len(hits), 'member_date': proof['date']})
        state = ('observed' if not ids else 'unknown' if not sectors else
                 'partial' if group['state'] != 'observed' or any(not proof.get('complete') for proof in members.values())
                 else 'observed')
        group['classification'] = {'state': state,
            'total': total, 'classified': len(classified), 'missing': len(ids - classified),
            **{key: sorted(rows, key=lambda row: (-row['count'], row['topic_code']))[:3]
               for key, rows in dimensions.items() if rows}}


def stock_projection(row, fields):
    """Copy bounded classifications and explicit events; never return arbitrary nested JSON."""
    from app.services.noon_membership_context import _display_board
    from app.domain.universe import BOARDS

    result = {key: row[key] for key in fields if key in row}
    result['board'] = _display_board(row)
    result['board_label'] = BOARDS.get(result['board'], '未知')
    themes = []
    counts = {'industry': 0, 'concept': 0}
    source_themes = row.get('themes')
    for theme in source_themes if isinstance(source_themes, (list, tuple)) else ():
        if not isinstance(theme, dict) or theme.get('kind') not in counts:
            continue
        kind, day = theme['kind'], theme.get('member_date', theme.get('members_observed_date'))
        name = theme.get('name')
        if (counts[kind] < (1 if kind == 'industry' else 2) and membership._day(day)
                and day <= row.get('_ai_observed_date', day)
                and isinstance(name, str) and 0 < len(name) <= 100):
            themes.append({'name': name, 'kind': kind, 'member_date': day})
            counts[kind] += 1
    if themes:
        result['themes'] = themes
    return result

"""Project strict prior close heights from the frozen auction feedback."""

from app.services.auction_feedback import read_view


def build_reference(feedback):
    groups = {}
    unknown_height = 0
    classifications = feedback.get('stock_classifications', {})
    for group in feedback['groups']:
        for row in group['items']:
            if row['previous_state'] != 'limit_up':
                continue
            height = row['previous_board_count']
            if height is None:
                unknown_height += 1
                continue
            quote = {
                'instrument_id': row['instrument_id'],
                'name': row['name'],
                'auction_pct': row['auction_pct'],
            }
            classification = classifications.get(row['instrument_id'])
            if classification is not None:
                quote['themes'] = [*classification['industries'], *classification['topics']]
                if not quote['themes']:
                    quote['themes_reason'] = classification['reason']
            opening = row.get('opening_reference')
            if opening and opening.get('matched'):
                quote['opening_reference_price'] = opening['reference_price']
            groups.setdefault(height, []).append(quote)

    levels = []
    for height, rows in sorted(groups.items(), reverse=True):
        rows.sort(key=lambda row: (
            row['auction_pct'] is None, -(row['auction_pct'] or 0), row['instrument_id']))
        up = down = flat = unknown = 0
        for row in rows:
            value = row['auction_pct']
            if value is None:
                unknown += 1
            elif value > 0:
                up += 1
            elif value < 0:
                down += 1
            else:
                flat += 1
        levels.append({'board_count': height, 'items': rows,
                       'up': up, 'down': down, 'flat': flat, 'unknown': unknown})

    candidate_count = feedback['coverage']['requested']
    return {
        'date': feedback['date'],
        'previous_date': feedback['previous_date'],
        'state': 'observed_sample' if levels else 'unknown',
        'candidate_count': candidate_count,
        'verified_height_count': sum(len(rows) for rows in groups.values()),
        'unknown_height_count': unknown_height,
        'highest_board': max(groups, default=None),
        'groups': levels,
        'reason': (None if levels else feedback.get('reason') or
                   ('这份竞价候选中暂无可核验的前一交易日连板高度。' if candidate_count
                    else '当日没有可用的留存竞价快照。')),
    }


def read_auction_reference(day):
    return build_reference(read_view(day, include_topics=True))

"""The overview's stable amount ranking, shared with dated activity facts."""
import math


def ranked_amount_rows(rows, limit=10):
    # Preserve the existing overview selection and stable equal-amount order.
    # Source validation is reported separately; it must not secretly change
    # today's selected table while computing historical annotations.
    return sorted((row for row in rows if row.get('amount') is not None and
                   isinstance(row.get('amount'), (int, float)) and math.isfinite(row['amount'])),
                  key=lambda row: row['amount'], reverse=True)[:limit]


def amount_ranking_fact(rows, day):
    ranked = ranked_amount_rows(rows)
    valid = lambda value: isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0
    symbols = [row['instrument_id'] for row in rows if isinstance(row.get('instrument_id'), str)]
    duplicate = len(set(symbols)) != len(symbols)
    invalid = any(not valid(row.get('amount')) or not isinstance(row.get('instrument_id'), str) or not row['instrument_id'] for row in rows)
    items = [{'instrument_id': row['instrument_id'], 'rank': rank, 'amount': row['amount']}
             for rank, row in enumerate(ranked, 1)
             if isinstance(row.get('instrument_id'), str) and row['instrument_id'] and valid(row.get('amount'))]
    complete = len(items) == 10 and not duplicate and not invalid
    return {'date': day, 'items': items, 'complete': complete, 'observed_rows': len(rows),
            'reason': None if complete else '该日本地榜不足10项、成交额字段待核验或存在重复证券' if rows else '该日没有已留存报价榜'}

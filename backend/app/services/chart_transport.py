"""Optional lossless column transport; chart calculations and raw sources stay unchanged."""
from app.core import json_clean

VERSION = 'chart-bars-columns-v1'


def chart_payload(value, encoding='rows'):
    value = json_clean(value)
    if encoding != 'columns-v1': return value
    bars = value.get('bars')
    if not isinstance(bars, list) or any(not isinstance(row, dict) for row in bars): return value
    fields = sorted({field for row in bars for field in row})
    missing = {field: [i for i, row in enumerate(bars) if field not in row] for field in fields}
    return {**{key: item for key, item in value.items() if key != 'bars'},
            'bars_columnar': {'version': VERSION, 'count': len(bars), 'fields': fields,
                'columns': [[row.get(field) for row in bars] for field in fields],
                'missing': {field: indices for field, indices in missing.items() if indices}}}

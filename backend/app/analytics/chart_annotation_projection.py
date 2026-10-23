"""Read-only evidence bounds for projecting saved marks onto a later chart."""
from datetime import date

VERSION = 'chart-annotation-projection-v1'


def _date(value):
    try:
        return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def annotation_projection_evidence(rows, as_of, snapshot_id):
    """Bound the retained source observation; never claim a complete archive."""
    result = {'version': VERSION, 'available': False, 'start_date': None,
              'end_date': as_of if _date(as_of) else None,
              'snapshot_id': snapshot_id, 'source': 'published_corporate_actions',
              'reason': '缺少同代际公司行动覆盖依据，不能换算旧锚水平线'}
    if not _date(as_of) or not isinstance(snapshot_id, str) or not snapshot_id or not rows:
        return result
    dates = [row.get('date') for row in rows]
    if (not all(_date(day) and day <= as_of for day in dates)
            or dates != sorted(set(dates))):
        return {**result, 'reason': '日行情日期无效、重复或越过观察日，不能证明划线换算区间'}
    result['start_date'] = dates[0]
    if not all(row.get('corporate_action_coverage_known') is True for row in rows):
        return result
    return {**result, 'available': True,
            'reason': '已留存同代际公司行动文件，换算仅限本图日行情起点至观察日；不声称完整公司行动档案'}

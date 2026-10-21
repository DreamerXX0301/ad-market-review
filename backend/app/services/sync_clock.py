"""Pure Beijing-time cutoff rules; never fetch calendars or create tasks."""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

CHINA = ZoneInfo('Asia/Shanghai')
CUTOFF_HOUR = 18


def now_china():
    return datetime.now(CHINA)


def local_now(at=None):
    value = now_china() if at is None else at
    if value.tzinfo is None:
        raise ValueError('同步时钟必须带时区')
    return value.astimezone(CHINA)


def safe_completion_date(at=None):
    value = local_now(at)
    return (value.date() if value.hour >= CUTOFF_HOUR else value.date() - timedelta(days=1)).isoformat()


def sync_window(at=None):
    value = local_now(at)
    allowed = value.hour >= CUTOFF_HOUR
    next_at = None if allowed else datetime.combine(value.date(), time(CUTOFF_HOUR), CHINA).isoformat()
    return {'increment_allowed': allowed,
            'reason': None if allowed else '盘后增量需在北京时间18:00后手动运行；18:00前不抓取未完成的当日行情',
            'timezone': 'Asia/Shanghai', 'cutoff': '18:00', 'now': value.isoformat(),
            'next_available_at': next_at, 'completion_safe_date': safe_completion_date(value)}


def require_new_increment(at=None):
    state = sync_window(at)
    if not state['increment_allowed']:
        raise ValueError(state['reason'])


def validate_frozen_target(target, at=None):
    try:
        valid = isinstance(target, str) and date.fromisoformat(target).isoformat() == target
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('恢复任务缺少有效冻结交易日期，不猜测新的目标日')
    if target > safe_completion_date(at):
        raise ValueError('冻结目标日尚未到北京时间18:00安全时点，不发布当日未完成行情；稍后继续原任务')


def require_resume(frozen_target=None, at=None):
    if frozen_target is None:
        require_new_increment(at)
    else:
        validate_frozen_target(frozen_target, at)

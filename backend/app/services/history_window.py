"""Explicit, frozen initial-history windows; no I/O or clock-dependent defaults."""
from __future__ import annotations

from datetime import date, timedelta
from .three_year_calendar import years_before

DEFAULT_WINDOW = 'near-five-years'
FUYAO_INDEX_HISTORY_SOURCE = 'https://fuyao.aicubes.cn/docs/api-reference/a-share-index/'
TEN_YEAR_UNAVAILABLE_REASON = '当前扶摇指数来源仅提供近5年历史，尚不能交叉核验十年交易日历；十年仅完成个股存储与性能容量测试，暂不支持新建十年初始化'
WINDOWS = {
    'near-one-year': (1, '近一年（兼容）'),
    'near-three-years': (3, '近三年（兼容）'),
    'near-five-years': (5, '近五年'),
    'near-ten-years': (10, '近十年'),
}


def resolve_initial_window(parameters=None, *, for_new_task=False):
    """Normalize only an allow-listed window name, never arbitrary year counts."""
    if parameters is None: parameters = {}
    if not isinstance(parameters, dict): raise ValueError('初始化范围参数必须为对象')
    if any(key in parameters for key in ('years', 'history_years')):
        raise ValueError('初始化范围请使用已支持的window名称，不接受任意年份')
    value = parameters.get('window', DEFAULT_WINDOW)
    if not isinstance(value, str) or value not in WINDOWS:
        raise ValueError('初始化范围仅支持近一年、三年、五年或十年的明确窗口')
    if for_new_task and value == 'near-ten-years':
        raise ValueError(TEN_YEAR_UNAVAILABLE_REASON)
    years, label = WINDOWS[value]
    return {'window': value, 'years': years, 'label': label}


def window_start(target, window):
    years = resolve_initial_window({'window': window})['years']
    if years == 1:
        # Explicit legacy one-year jobs originally froze a 365-day request.
        return (date.fromisoformat(target)-timedelta(days=365)).isoformat()
    return years_before(target, years)


def initialization_window_status(reference=None):
    default = resolve_initial_window()
    coverage = reference.get('coverage', {}) if isinstance(reference, dict) else {}
    provenance = coverage.get('calendar_provenance', {})
    retained = coverage.get('initial_window')
    if retained not in WINDOWS: retained = None
    return {'default_window': default['window'], 'default_years': default['years'],
            'default_label': default['label'],
            'supported_windows': [{'window': key, 'years': value[0], 'label': value[1]}
                                  for key, value in WINDOWS.items() if key != 'near-ten-years'],
            'unavailable_windows': [{'window': 'near-ten-years', 'years': 10, 'reason': TEN_YEAR_UNAVAILABLE_REASON}],
            'source_history': {'source': 'fuyao', 'index_history_years': 5, 'documentation': FUYAO_INDEX_HISTORY_SOURCE},
            'retained_window': retained, 'retained_years': WINDOWS[retained][0] if retained else None,
            'calendar_basis': provenance.get('basis'),
            'notice': '当前扶摇指数仅近5年，正式默认近五年且不额外请求窗口外指数预热；十年仅完成个股容量测试，暂不开放正式初始化。实际覆盖须交叉核验，上市、缺报及公司行动可能限制周月指标；旧任务按原冻结范围恢复，增量不自动扩展初始历史。'}

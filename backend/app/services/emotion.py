"""Explainable observations, not a claim to measure a complete sentiment cycle."""
from __future__ import annotations
import math

VERSION='breadth-event-proxy-v2'
REFERENCES=[
    {'title':'深交所：2016年个人投资者状况调查报告','url':'https://www.szse.cn/aboutus/trends/news/t20170316_518921.html','use':'情绪还涉及投资者预期与行为偏差，不能由涨跌家数完整替代。'},
    {'title':'深交所投教：慎重追买连续涨停股票','url':'https://investor.szse.cn/warning/riskedu/t20120412_550505.html','use':'连续涨停成因复杂；涨停数量不是确定的后续收益信号。'},
]
RULES=[
    {'phase':'待核验','rule':'无可比报价，或可比样本/估算应有范围不足80%。'},
    {'phase':'冰点','rule':'温度≤15且上涨家数<500。'},
    {'phase':'高潮','rule':'温度≥85、上涨≥3000，且当日完整已核验来源池涨停≥80、跌停≤5。缺池不能确认高潮。'},
    {'phase':'修复','rule':'相邻前交易日温度≤35，今日温度>35且较前日上升≥15个百分点。'},
    {'phase':'退潮','rule':'相邻前交易日温度≥60，今日温度<60且较前日下降≥15个百分点。'},
    {'phase':'普涨','rule':'温度≥60且上涨≥3000，且未命中以上优先规则。'},
    {'phase':'分化','rule':'样本可判但未命中以上规则；不表示市场必将转向。'},
]


def _number(value):
    return value if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and value>=0 else None


def _reading(breadth):
    values=[_number(breadth.get(key)) for key in ('up','down','flat')]
    if any(value is None for value in values):return None,0,0
    comparable=sum(values);temperature=values[0]/comparable*100 if comparable else None
    observed=_number(breadth.get('total',breadth.get('observed')))
    expected=_number(breadth.get('expected_current_universe'))
    estimate=max(expected or 0,observed or 0,comparable+(_number(breadth.get('unknown')) or 0)+(_number(breadth.get('missing')) or 0))
    return temperature,comparable,comparable/estimate if estimate else 0


def describe_emotion(breadth,previous=None,*,day=None,previous_day=None,limit_up=None,limit_down=None,event_pools_complete=False):
    temperature,comparable,coverage=_reading(breadth)
    previous_temperature,_,previous_coverage=_reading(previous or {})
    same_scope=not previous or previous.get('scope')==breadth.get('scope')
    usable_previous=previous is not None and previous_temperature is not None and previous_coverage>=.8 and same_scope
    delta=temperature-previous_temperature if temperature is not None and usable_previous else None
    up=_number(breadth.get('up'));state='待核验' if not comparable else '普涨' if up>=3000 else '普跌' if up<500 else '分化'
    phase='待核验';reason='没有足够可比报价，不能判断盘面阶段'
    if comparable and coverage>=.8:
        phase='分化';reason='可比样本充足，但未命中其他阶段规则'
        if temperature<=15 and up<500:phase='冰点';reason='温度≤15且上涨家数<500'
        elif temperature>=85 and up>=3000 and event_pools_complete and _number(limit_up) is not None and _number(limit_down) is not None and limit_up>=80 and limit_down<=5:
            phase='高潮';reason='高广度与完整来源涨跌停池同时达到高潮观察阈值'
        elif delta is not None and previous_temperature<=35 and temperature>35 and delta>=15:
            phase='修复';reason='从前日低广度回升至少15个百分点，并越过35'
        elif delta is not None and previous_temperature>=60 and temperature<60 and delta<=-15:
            phase='退潮';reason='从前日高广度回落至少15个百分点，并跌破60'
        elif temperature>=60 and up>=3000:phase='普涨';reason='温度≥60且上涨家数≥3000'
    elif comparable:reason='可比样本覆盖不足80%，保留温度读数但不判阶段'
    return {'temperature':temperature,'cycle':phase,'phase':phase,'breadth_state':state,'phase_reason':reason,
            'model':VERSION,'formula':'上涨家数 /（上涨＋下跌＋平盘的可比样本）×100',
            'comparable_count':comparable,'comparable_coverage':coverage,'previous_temperature':previous_temperature if usable_previous else None,
            'temperature_delta':delta,'date':day,'previous_date':previous_day if usable_previous else None,
            'scope':breadth.get('scope','已留存可比样本'),'rules':RULES,'references':REFERENCES,
            'event_pools_complete':bool(event_pools_complete),
            'notice':'本项目自定义阈值，按顺序判定的盘面代理描述；不是交易所阶段标准、完整心理情绪周期或买卖信号。修复/退潮需同口径相邻交易日，高潮另需完整来源事件池。'}

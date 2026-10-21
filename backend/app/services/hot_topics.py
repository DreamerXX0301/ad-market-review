"""Hot-topic shortcuts from stored closing events and current effective members."""
from __future__ import annotations
import math


def rank_hot_topics(classifications,events,calendar,day,*,limit=8,coverage=None):
    """Pure rank: distinct confirmed stock-days, then distinct stocks and name."""
    window=sorted({value for value in calendar if value<=day})[-20:]
    allowed=set(window);unique={(event['instrument_id'],event['date']) for event in events if event.get('instrument_id') and event.get('date') in allowed}
    items=[]
    for topic in classifications:
        if topic.get('archived') or topic.get('kind')=='industry':continue
        members=set(topic.get('instrument_ids',[]));matched={(symbol,observed) for symbol,observed in unique if symbol in members}
        if not matched:continue
        items.append({'topic_id':topic['topic_id'],'name':topic['name'],'kind':topic.get('kind','concept'),
                      'event_count':len(matched),'active_stock_count':len({symbol for symbol,_ in matched}),
                      'member_count':len(members),'members_complete':topic.get('members_complete') is True,
                      'membership_basis':'current_members_proxy','membership_observed_date':topic.get('source_observed_date'),
                      'event_basis':'已确认收盘涨停股票日数；同股同日去重，归属重叠的题材分别计数'})
    items.sort(key=lambda item:(-item['event_count'],-item['active_stock_count'],item['name'],item['topic_id']))
    for index,item in enumerate(items):item['rank']=index+1
    return {'items':items[:max(1,min(int(limit),20))],'total':len(items),'business_date':day,
            'window':{'start':window[0] if window else None,'end':day,'observed_trade_days':len(window),'expected_trade_days':20,'complete':len(window)==20},
            'coverage':coverage or {},'ranking_basis':'近20个市场交易日已确认涨停股票日数，当前有效成员代理口径',
            'warnings':['使用当前题材成员及人工增删映射历史事件，不代表当时的题材归属；不是资金流入或投资建议。','缺失、空池和隔离事件不补0；排名仅比较已确认事件下界，覆盖缺口可能改变排序。']}


def list_hot_topics(limit=8):
    """Read only: no remote requests, sync, publication or business-state writes."""
    from app.core import connect
    from app.services.topic_members import _market_context,_read_state,_guard
    from app.services.market_supplement import load_supplement
    from app.services.topic_member_metrics import _sources,_daily_states
    market,day,head,snapshot,catalog,master=_market_context()
    with connect() as connection:
        connection.execute('BEGIN');_guard(connection,day,head)
        state=_read_state(connection,day,catalog)
    classifications=[{**item['public'],'instrument_ids':list(item['members'])} for item in state.values() if not item['public']['archived'] and item['public']['kind']!='industry']
    calendar=sorted({value for value in market.calendar() if value<=day})[-20:]
    supplement=load_supplement(snapshot) if snapshot else {}
    reference=market.reference() if callable(getattr(market,'reference',None)) else {}
    sources=_sources(reference,supplement,calendar)
    member_union=set().union(*(set(item['instrument_ids']) for item in classifications)) if classifications else set()
    code_map={symbol[:6]:symbol for symbol in master}
    candidates={code_map[code] for source in sources.values() for _,indexed,_ in source['up'] for code in indexed if code in code_map and code_map[code] in member_union}
    observed=[];complete=[]
    for observed_day,source in sources.items():
        records=[record for record,_,options in source['up'] if options['allow_absence']]
        if any(record.get('items') and record.get('date') in (None,observed_day) for record in records):observed.append(observed_day)
        if any(record.get('complete') is True and record.get('items') and record.get('date') in (None,observed_day) and not record.get('quarantine') for record in records):complete.append(observed_day)
    events=[];unknown_candidate_days=0;batch_available=callable(getattr(market,'history_for_instruments',None))
    if batch_available and calendar:
        candidates=sorted(candidates)
        for offset in range(0,len(candidates),256):
            batch=candidates[offset:offset+256]
            histories=market.history_for_instruments(batch,start=calendar[0],end=day)
            for symbol in batch:
                closes={};duplicates=set()
                for row in histories.get(symbol,[]):
                    observed_day=row.get('date')
                    if observed_day not in sources:continue
                    if observed_day in closes:duplicates.add(observed_day)
                    price=row.get('close')
                    closes[observed_day]=price if isinstance(price,(int,float)) and not isinstance(price,bool) and math.isfinite(price) and price>0 else None
                for duplicate in duplicates:closes[duplicate]=None
                for observed_day,value in _daily_states(symbol,closes,calendar,sources).items():
                    if value['state'] is True:events.append({'instrument_id':symbol,'date':observed_day})
                    elif value['state'] is None:unknown_candidate_days+=1
    coverage={'source_observed_days':len(observed),'source_complete_days':len(complete),'expected_trade_days':len(calendar),
              'missing_dates':[value for value in calendar if value not in complete],
              'complete':bool(calendar) and len(calendar)==20 and len(complete)==20 and batch_available and unknown_candidate_days==0,
              'raw_price_validation_available':batch_available,'confirmed_stock_days':len(events),
              'unverified_candidate_stock_days':unknown_candidate_days,
              'scope':'已留存来源收盘池；不等同交易所全A事件全集'}
    result=rank_hot_topics(classifications,events,calendar,day,limit=limit,coverage=coverage)
    result['snapshot_id']=snapshot
    if not batch_available:result['warnings'].append('当前行情对象没有限定证券批量原价，未核验事件，不生成快捷排名。')
    with connect() as connection:_guard(connection,day,head)
    return result

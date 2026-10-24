"""Dated market research assembled from one immutable real-data generation."""
from __future__ import annotations

from collections import Counter, OrderedDict
import threading
import math
from fastapi import HTTPException

from app.core import get_market, json_clean, digest
from app.services.market_supplement import load_supplement, load_supplement_with_fingerprint, annual_coverage
from app.services.market_history import resolve_reference, data_coverage
from app.services.market_amount import amount_change_metadata
from app.services.sector_history import sector_prices_for_date
from app.services.sector_views import sector_view

_gainers_cache=OrderedDict()
_gainers_lock=threading.RLock()


def context(day=None):
    market = get_market()
    status = market.status()
    if not status.get("ready"):
        raise HTTPException(409, "尚无已发布行情，请先在数据中心初始化")
    day = day or status["data_end"]
    if day not in market.calendar():
        raise HTTPException(422, "该日期没有已留存的完成交易日行情")
    return market, status, day


def number(value):
    return value is not None and isinstance(value, (int, float)) and math.isfinite(value)


def dated_events(reference,day):
    existing=({key:reference[key] for key in ('limitup','limitdown','breakpool') if key in reference} if day==reference.get('pool_trade_date') else dict(reference.get('pool_history',{}).get(day,{})))
    supplement=reference.get('_supplement',{})
    annual=supplement.get('annual_pools',{}).get(day)
    if 'limitup' not in existing and annual is not None:
        existing['limitup']=list(annual.get('items',[]))
    down=supplement.get('annual_down_pools',{}).get(day)
    if 'limitdown' not in existing and down is not None:
        existing['limitdown']=list(down.get('items',[]))
    public=supplement.get('public_pools',{}).get(day,{})
    if public.get('items'):
        pool={r['thscode']:dict(r) for r in existing.get('limitup',[]) if r.get('thscode')}
        for detail in public['items']:
            symbol=detail['instrument_id'];event=pool.setdefault(symbol,{'thscode':symbol,'ticker':symbol[:6],'name':detail.get('name'),'continue_day_cnt':detail.get('board_count'),'source':detail.get('source'),'seal_money':detail.get('seal_money'),'turnover':detail.get('amount'),'limit_up_time':detail.get('last_limit_time')})
            event.update(first_limit_time=detail.get('first_limit_time'),last_limit_time=detail.get('last_limit_time'),break_count=detail.get('break_count'),turnover_ratio_pct=detail.get('turnover_rate'),details_source=detail.get('source'),source_industry=detail.get('source_industry'))
        existing['limitup']=list(pool.values())
    return existing


def prior_ladder_feedback(market,reference,day,indexed):
    calendar=market.calendar();position=calendar.index(day)
    if not position:return []
    previous=calendar[position-1]
    ladder=next((r for r in reference.get('ladder',{}).get('item',[]) if r.get('date')==previous),None)
    if not ladder:return []
    down={r['thscode'] for r in dated_events(reference,day).get('limitdown',[])}
    found={}
    for events in ladder.get('boards',{}).values():
        for event in events:
            symbol=event.get('thscode');row=indexed.get(symbol)
            if not row:continue
            change=row.get('change_pct');height=event.get('board_num')
            found[symbol]={**row,'previous_board_count':height,'previous_board_date':previous,'feedback':'跌停反馈' if symbol in down else '明显回落' if number(change) and change<=-5 else '下跌分歧' if number(change) and change<0 else '上涨' if number(change) and change>0 else '待核验' if not number(change) else '平盘','sample_basis':'上一交易日已留存连板股'}
    if not found:return []
    status=market.status()
    if day==status.get('data_end'):
        from app.services.stock_memberships import current_memberships
        classifications=current_memberships(market,found,expected_day=day)
        memberships=classifications['memberships'];scope=classifications['scope'];state='current_partial'
    else:
        local,coverage=resolve_reference('sectors',day,status,reference)
        memberships={symbol:[] for symbol in found}
        for topic in local.get('topics',[]):
            for symbol in set(topic.get('instrument_ids',[])) & found.keys():
                memberships[symbol].append({'topic_id':topic.get('topic_id'),'name':topic.get('name'),
                    'kind':topic.get('kind','concept'),'source_observed_date':topic.get('observed_date'),
                    'members_complete':topic.get('members_complete') is True})
        scope=coverage.get('notice','观察日已留存成员，不回填当前归属')
        state='dated_partial' if coverage.get('selected_date_available') else 'unknown'
    for symbol,row in found.items():
        tags=memberships.get(symbol,[])
        row.update(topics=[tag for tag in tags if tag.get('kind')!='industry'],
            industries=[tag for tag in tags if tag.get('kind')=='industry'],topics_state=state,
            topics_observed_date=day,topics_scope=scope)
    from app.services.stock_board_presentation import decorate_board_collections
    decorated=decorate_board_collections(market,status,day,reference,{'feedback':list(found.values())})['feedback']
    def feedback_order(row):
        height=row.get('board_presentation',{}).get('previous_height',{})
        value=height.get('value')
        known=(height.get('state')=='confirmed' and number(value) and not isinstance(value,bool)
               and value>=1 and value==int(value))
        return (not known,-value if known else 0,row.get('instrument_id',''))
    return sorted(decorated,key=feedback_order)


def stock(row, reference=None):
    result = dict(row)
    result["change_30d_pct"] = row.get("return_30d")
    result["turnover_rate"] = row.get("turnover_ratio_pct")
    result["limit_up_ytd"] = row.get("year_limit_up_count")
    result["board_count"] = row.get("continue_day_cnt")
    result["risk_labels"] = []
    if reference:
        found = next((r for r in reference.get("abnormal", []) if r.get("thscode") == row.get("instrument_id")), None)
        if found:
            result["risk_labels"].append(found.get("monitor_label", "来源监控标签"))
    observations = []
    ratio = row.get("volume_ratio")
    if number(ratio):
        observations.append("放量" if ratio >= 1.5 else "缩量" if ratio <= .7 else "量能平稳")
    if number(row.get("ma20")) and number(row.get("close")):
        observations.append("MA20上方" if row["close"] >= row["ma20"] else "MA20下方")
    if row.get("macd_cross"):
        observations.append("MACD金叉")
    if row.get("kd_cross"):
        observations.append("KDJ金叉（K线上穿D线）")
    result["technical_observation"] = " · ".join(observations) or "指标待核验"
    return result


def panel(day=None):
    market, status, day = context(day)
    supplement,fingerprint=load_supplement_with_fingerprint(status["snapshot_id"])
    reference = {**market.reference(), "_supplement":supplement,
                 "_supplement_cache_source":(supplement,fingerprint) if fingerprint is not None else None}
    result=[stock(r, reference if day == status["data_end"] else None) for r in market.panel(day)]
    events=dated_events(reference,day)
    if 'limitup' in events:
        pool={r['thscode']:r for r in events['limitup']}
        for row in result:
            event=pool.get(row['instrument_id'])
            row.update(limit_up=True if event else row.get('limit_up'),board_count=event.get('continue_day_cnt') if event else row.get('board_count'))
            if event and event.get('turnover_ratio_pct') is not None:row['turnover_rate']=event['turnover_ratio_pct']
    supplement=reference['_supplement']
    if supplement:
        coverage=annual_coverage(supplement,market.calendar(),day,instruments=[r['instrument_id'] for r in result])
        for row in result:
            observed=coverage['counts'].get(row['instrument_id'],0);specific=coverage['instrument_coverage'][row['instrument_id']]
            row.update(limit_up_ytd=observed if specific['complete'] else None,limit_up_ytd_observed=observed,limit_up_ytd_coverage=specific,limit_up_ytd_reason=None if specific['complete'] else '本证券年内历史池仍有未知日期；已确认次数为下界')
    return market, status, day, reference, result


def _breadth(market, rows, day):
    counts = {"up": 0, "down": 0, "flat": 0, "unknown": 0}
    for row in rows:
        value = row.get("change_pct")
        counts["unknown" if not number(value) else "up" if value > 0 else "down" if value < 0 else "flat"] += 1
    identities = [r for r in market.instruments(limit=10000) if r.get("kind") == "stock" and r.get("list_date") and r["list_date"] <= day and (not r.get("end_date") or r["end_date"] >= day)]
    return {**counts, "suspended": None, "missing": max(0, len(identities) - len(rows)), "total": len(rows), "expected_current_universe": len(identities), "scope": "沪深京已留存A股样本", "rise_line": 3000, "fall_line": 500, "quality": "partial"}


def index_overview(day=None):
    """Index-page facts without the stock panel or unrelated source histories."""
    market, status, day = context(day)
    return json_clean({"date": day, "snapshot_id": status["snapshot_id"],
        "indices": market.index_quotes(day), "quality": status["quality"],
        "data_coverage": data_coverage("overview", status, day, market.reference(), market.calendar())})


def overview(day=None):
    market, status, day, reference, rows = panel(day)
    events=dated_events(reference,day)
    latest = day == reference.get("pool_trade_date")
    shsz = [r for r in rows if r["instrument_id"].endswith((".SH", ".SZ"))]
    three = [r for r in rows if r['instrument_id'].endswith(('.SH','.SZ','.BJ'))]
    amount = sum(r['amount'] for r in three if number(r.get('amount')))
    history = market.turnover_history(day) if hasattr(market,'turnover_history') else []
    comparison = amount_change_metadata(amount if three and all(number(r.get('amount')) and not isinstance(r.get('amount'),bool) and r['amount'] >= 0 for r in three) else None, day, market.calendar(), history)
    top = lambda key, reverse=True, count=10: sorted((r for r in rows if number(r.get(key))), key=lambda r: r[key], reverse=reverse)[:count]
    indexed = {r["instrument_id"]: r for r in rows}
    divergence = []
    if 'breakpool' in events:
        for event in sorted(events['breakpool'], key=lambda r: (r.get("open_times") or 0, r.get("turnover") or 0), reverse=True)[:5]:
            symbol = event.get("thscode")
            divergence.append({**indexed.get(symbol, {"instrument_id": symbol, "code": event.get("ticker"), "name": event.get("name")}), "open_times": event.get("open_times"), "turnover_rate": event.get("turnover_ratio_pct"), "amount": event.get("turnover"), "scope": "来源当日炸板池", "reason": "按开板次数、成交额排序"})
    largest = []
    if 'limitup' in events:
        for event in sorted(events['limitup'], key=lambda r: r.get("seal_money") or 0, reverse=True)[:5]:
            symbol = event.get("thscode")
            largest.append({**indexed.get(symbol, {"instrument_id": symbol, "code": event.get("ticker"), "name": event.get("name")}), "seal_money": event.get("seal_money"), "reason": event.get("limit_up_reason"), "limit_time": event.get("limit_up_time"), "board_count": event.get("continue_day_cnt"), "source":event.get("source","fuyao"), "date":day, "scope":"收盘池单次封单资金，不代表稳定性", "topics":indexed.get(symbol,{}).get("topics") or ([{"name":event["source_industry"],"kind":"source_industry"}] if event.get("source_industry") else [])})
    from app.services.stock_board_presentation import decorate_board_collections
    displayed=decorate_board_collections(market,status,day,reference,{'divergence':divergence,'seals':largest})
    divergence,largest=displayed['divergence'],displayed['seals']
    ranking,_=cached_gainers(market,status,reference,day,rows,5)
    from app.services.market_top_amount import decorate_top_amount
    top_amount, top_amount_activity = decorate_top_amount(market,status,day,rows,reference)
    return json_clean({"date": day, "snapshot_id": status["snapshot_id"], "data_coverage": data_coverage("overview", status, day, reference, market.calendar()), "indices": market.index_quotes(day) if hasattr(market,"index_quotes") else market.indices(day), "breadth": _breadth(market, rows, day), "total_amount": amount, "amount_scope": "沪深京已留存A股股票成交额", "shsz_total_amount": sum(r["amount"] for r in shsz if number(r.get("amount"))), "bj_amount": sum(r["amount"] for r in three if r["instrument_id"].endswith(".BJ") and number(r.get("amount"))), "sh_amount": sum(r["amount"] for r in shsz if r["instrument_id"].endswith(".SH") and number(r.get("amount"))), "sz_amount": sum(r["amount"] for r in shsz if r["instrument_id"].endswith(".SZ") and number(r.get("amount"))), **comparison, "top_amount": top_amount, "top_amount_activity": top_amount_activity, "top_gainers": top("change_pct"), "top_losers": top("change_pct", False), "top_30d": [r for r in ranking if r.get("rank") is not None][:5], "limit_up_count": len(events["limitup"]) if "limitup" in events else None, "limit_down_count": len(events["limitdown"]) if "limitdown" in events else None, "broken_count": len(events["breakpool"]) if "breakpool" in events else None, "divergence": divergence, "largest_seals": largest, "seal_funds_top5": largest, "seal_stability": {"items": [], "needed_capability":"盘中连续封单金额与委托队列快照，带时间戳、采样间隔和覆盖率；通常需要Level-2或许可的连续盘口采样", "unavailable_reason": "当前仅留存封单金额快照，尚无连续封单序列，不能据此计算封单稳定性"}, "warnings": ["行情与技术指标为真实未复权数据；公司行动附近指标重新预热", "上涨家数为已留存可比样本；停牌与缺数尚未全部解释"], "quality": status["quality"]})


def market_topic_members(day=None, *, topic_id=None, source_code=None, kind=None, source_name=None):
    from app.services.market_topic_members import list_members
    market, status, day, reference, rows = panel(day)
    return list_members(market, status, day, reference, rows, topic_id=topic_id, source_code=source_code, kind=kind, source_name=source_name)


def market_page(kind, day=None, limit=100, *, query="", page=1, page_size=None, sort_by=None, sort_order="desc", top_n=None, monitor_scope='historical'):
    if monitor_scope not in ('historical', 'latest') or (monitor_scope != 'historical' and kind != 'abnormal'):
        raise HTTPException(422, '监控名单范围仅支持监管页的historical或latest')
    if top_n is not None and (kind != 'gainers' or isinstance(top_n,bool) or top_n not in (100,200,300,500,1000)):
        raise HTTPException(422, '近30日涨幅榜仅支持前100、200、300、500或1000；省略top_n查看全市场')
    requested_date = day
    market, status, day, reference, rows = panel(None if kind == 'abnormal' and monitor_scope == 'latest' else day)
    indexed = {r["instrument_id"]: r for r in rows}
    latest = day == reference.get("pool_trade_date")
    events=dated_events(reference,day)
    base = {"date": day, "snapshot_id": status["snapshot_id"], "items": [], "total": 0, "summary": {}, "warnings": [], "quality": "partial"}
    if kind == "gainers":
        base["items"], summary = cached_gainers(market,status,reference,day,rows)
        ranked_total = sum(isinstance(row.get('rank'),int) and not isinstance(row.get('rank'),bool) and row['rank'] > 0 for row in base['items'])
        available_total = len(base['items'])
        if top_n is not None:
            base['items'] = [row for row in base['items'] if isinstance(row.get('rank'),int) and not isinstance(row.get('rank'),bool) and 0 < row['rank'] <= top_n]
        base["summary"] = {**summary,'top_n':top_n,'available_total':available_total,'available_ranked_total':ranked_total,'rank_scope_total':len(base['items']),'rank_scope':'先按近30日涨幅排名选择前N，再在该范围搜索、排序和分页' if top_n is not None else '全市场；端点未知者保留未排名'}
        base["warnings"] = ["30个市场交易日端点涨幅；中间缺报价不删股，另列缺口。区间已核验公司行动使用事件前复权价格，未核验则不计算涨幅。", "年内涨停以逐日来源池计数；覆盖不完整时只显示已确认次数下界，不补0。"]
    elif kind == "sectors":
        local, membership = resolve_reference('sectors', day, status, reference)
        prices = sector_prices_for_date(reference.get('_supplement', {}), day)
        base.update(sector_view(day, reference, indexed, local, membership, prices))
        from app.services.sector_activity import decorate_sector_activity
        base['items'], base['sector_activity_7d'] = decorate_sector_activity(market,status,day,base['items'],reference,prices)
    elif kind == "ladder":
        from app.services.market_ladder import build_ladder
        base.update(build_ladder(market, status, day, reference, rows, events))
        base["high_position_feedback"]=[r for r in prior_ladder_feedback(market,reference,day,indexed) if (r.get("previous_board_count") or 0)>=3]
        base['supplement_status']=supplement_status(reference,day,'seal_details')
    elif kind == "abnormal":
        from app.services.market_abnormal import monitor_view
        base.update(monitor_view(day, status, reference, indexed, scope=monitor_scope, requested_date=requested_date))
    elif kind == "emotion":
        from app.services.emotion import describe_emotion
        history=market.breadth_history(day)
        current=next((item for item in history if item.get('date')==day),None)
        previous=None;previous_day=None;universe_upper_bound=None
        if current:
            # One cached series supplies both dates with the same SH/SZ scope.
            # Do not compare it to the overview's SH/SZ/BJ aggregate.
            master=[item for item in market.instruments(limit=10000) if item.get('kind')=='stock' and item.get('instrument_id','').endswith(('.SH','.SZ'))]
            universe_upper_bound=len(master) if master else None
            identities=[item for item in master if item.get('list_date') and item['list_date']<=day and (not item.get('end_date') or item['end_date']>=day)]
            expected=len(identities) if all(item.get('list_date') for item in master) else None
            observed=current.get('observed',sum(current.get(key,0) for key in ('up','down','flat','unknown')))
            breadth={**current,'total':observed,'expected_current_universe':expected,'missing':max(0,expected-observed) if expected is not None else None,'suspended':None,'rise_line':3000,'fall_line':500,'scope':'沪深已留存可比A股样本','universe_expected_known':expected is not None}
            calendar=market.calendar();position=calendar.index(day)
            previous_day=calendar[position-1] if position else None
            prior=next((item for item in history if item.get('date')==previous_day),None)
            if prior:
                prior_expected=sum(item['list_date']<=previous_day and (not item.get('end_date') or item['end_date']>=previous_day) for item in master) if expected is not None else None
                prior_observed=prior.get('observed',sum(prior.get(key,0) for key in ('up','down','flat','unknown')))
                previous={**prior,'scope':breadth['scope'],'expected_current_universe':prior_expected,'missing':max(0,prior_expected-prior_observed) if prior_expected is not None else None}
        else:breadth=_breadth(market,rows,day)
        def pool_complete(pool,key):
            annual=reference.get('_supplement',{}).get(key,{}).get(day,{})
            source=reference.get('pool_coverage',{}).get(pool,{}) if day==reference.get('pool_trade_date') else reference.get('pool_history',{}).get(day,{}).get('coverage',{}).get(pool,{})
            return bool((source.get('complete') and events.get(pool)) or (annual.get('complete') and annual.get('date') in (None,day) and annual.get('items')))
        complete=pool_complete('limitup','annual_pools') and pool_complete('limitdown','annual_down_pools')
        up=sum(str(item.get('thscode') or item.get('instrument_id','')).endswith(('.SH','.SZ')) for item in events.get('limitup',[])) if 'limitup' in events else None
        down=sum(str(item.get('thscode') or item.get('instrument_id','')).endswith(('.SH','.SZ')) for item in events.get('limitdown',[])) if 'limitdown' in events else None
        emotion=describe_emotion(breadth,previous,day=day,previous_day=previous_day,limit_up=up,limit_down=down,event_pools_complete=complete)
        base["breadth"] = breadth
        base['emotion']=emotion
        from app.services.market_sentiment import describe_sentiment
        base['sentiment']=describe_sentiment(market,reference,rows,day)
        from app.services.market_outlook import build_market_outlook
        base['market_outlook']=build_market_outlook(market,status,day,reference,rows,breadth,emotion,
                                                     base['sentiment'],history,previous_breadth=previous,
                                                     universe_upper_bound=universe_upper_bound)
        base["summary"] = {"temperature":emotion['temperature'],"cycle":emotion['phase'],'breadth_state':emotion['breadth_state'],'comparable_count':emotion['comparable_count'],'comparable_coverage_pct':emotion['comparable_coverage']*100,'temperature_delta':emotion['temperature_delta'],"model":emotion['model'],"formula":emotion['formula']}
        base["items"] = [{"name": "上涨家数", "value": breadth["up"], "reference": "普涨参考3000"}, {"name": "下跌家数", "value": breadth["down"], "reference": "已留存可比样本"}, {"name": "收盘涨停", "value": len(events["limitup"]) if "limitup" in events else None}, {"name": "收盘跌停", "value": len(events["limitdown"]) if "limitdown" in events else None}, {"name": "炸板池", "value": len(events["breakpool"]) if "breakpool" in events else None}]
        base["history"] = history
        base["warnings"] = [emotion['notice'],"情绪页使用同口径沪深序列；主行情页的沪深京范围不同。未知、缺报价和除权不可比样本不计作平盘。"]
        if breadth.get('universe_expected_known') is False:base['warnings'].append('部分证券上市日期未知，无法确认全市场应有家数与缺报价家数；可比率以已观察样本为分母，不把应有范围记为0。')
    elif kind == "flow":
        verified=fund_flow_for_date(reference.get('_supplement',{}),day,market.calendar())
        sectors = market_page("sectors", day,limit=1000) if not verified.get('items') else {'items':[]}
        base["items"] = verified.get('items') or [{**r, "net_inflow": None} for r in sorted(sectors["items"], key=lambda r: r.get("amount") or 0, reverse=True)]
        if verified.get('items'):
            from app.services.market_topic_members import membership_link
            local, _ = resolve_reference('sectors', day, status, reference)
            base['items'] = [{**row,**membership_link(row,local.get('topics',[]))} for row in base['items']]
        base["feedback"]=prior_ladder_feedback(market,reference,day,indexed)
        base["summary"] = {"basis": "供应商主力净流入分类" if verified.get('items') else "板块成交活跃度", "intraday_sequence_available": False, "net_flow_available": bool(verified.get('items')), "daily_history_available":bool(verified.get("history")), "history_observed_days":len({r["date"] for r in verified.get("history",[])}), "history_kind":"行业跨交易日资金趋势", "scope":"东财资金页当前行业目录；带明确日期的历史序列，不代表全市场所有概念或历史全部行业"}
        base['supplement_status']=supplement_status(reference,day,'fund_flow')
        base['history']=verified.get('history',[])
        base['coverage']=verified.get('coverage',{})
        base["warnings"] = ["供应商资金流按成交单大小分类，不能直接等同机构账户买入；成交额与净流入分开。", "只展示已留存目标交易日数据，不用今日快照替代历史；全天盘中轮动序列尚未接入。"]
        if not verified.get('items'):base["unavailable_reason"] = base['supplement_status'].get('reason') or "该日净资金流未通过数据源验收，下表仅为成交额观察"
    else:
        raise HTTPException(404, "盘面模块不存在")
    if 'data_coverage' not in base:
        calendar_method = getattr(market, 'calendar', None)
        coverage_calendar = calendar_method() if callable(calendar_method) else []
        base['data_coverage'] = data_coverage(kind, status, day, reference, coverage_calendar, base)
        if not callable(calendar_method):
            base['data_coverage']['notice'] += ' 当前数据上下文未提供市场交易日历，日期覆盖待核验。'
    if sort_by is not None:
        if kind != 'gainers':raise HTTPException(422,'该列表尚未开放服务端表头排序')
        allowed={'name','topics','change_pct','change_30d_pct','limit_up_ytd','last_limit_up_date','board_count','volume_ratio','technical_observation','risk_labels'}
        if sort_by not in allowed or sort_order not in ('asc','desc'):raise HTTPException(422,'排序字段或方向无效')
        def sort_value(row):
            value=row.get(sort_by)
            if sort_by in ('topics','risk_labels'):
                return ' · '.join(sorted(str(t.get('name','')) if isinstance(t,dict) else str(t) for t in value)).casefold() if value else None
            if sort_by in ('name','technical_observation','last_limit_up_date'):return value.casefold() if isinstance(value,str) and value else None
            return value if number(value) and not isinstance(value,bool) else None
        known=[r for r in base['items'] if sort_value(r) is not None]
        unknown=[r for r in base['items'] if sort_value(r) is None]
        known.sort(key=lambda r:str(r.get('instrument_id','')))
        known.sort(key=sort_value,reverse=sort_order=='desc')
        base['items']=known+sorted(unknown,key=lambda r:str(r.get('instrument_id','')))
        base.update(sort_by=sort_by,sort_order=sort_order,sort_scope='全量筛选结果，排序后分页；未知值始终排末')
    search=query.strip().casefold()
    if search:base['items']=[r for r in base['items'] if search in (' '.join(str(r.get(k,'')) for k in ('code','name','instrument_id'))+' '+' '.join(str(t.get('name',''))+' '+str(t.get('topic_id','')) for t in r.get('topics',[]) if isinstance(t,dict))).casefold()]
    base["total"] = len(base["items"])
    if kind == 'ladder':
        # Every confirmed closing-limit row is returned, including first boards.
        # Neither legacy limit nor page_size is allowed to hide ladder members.
        base.update(page=1, page_size=base['total'], pages=1, pagination_mode='all')
    elif page_size is not None:
        page_size=max(1,min(int(page_size),200));page=max(1,int(page));pages=max(1,math.ceil(base['total']/page_size));page=min(page,pages)
        base.update(page=page,page_size=page_size,pages=pages)
        base['items']=base['items'][(page-1)*page_size:page*page_size]
    else:base["items"] = base["items"][:max(1, min(int(limit), 1000))]
    return json_clean(base)


def supplement_status(reference,day,capability):
    doc=reference.get('_supplement',{})
    errors=[r['reason'] for r in doc.get('failures',[]) if r.get('date')==day and r.get('capability')==capability]
    key='public_pools' if capability=='seal_details' else 'fund_flows'
    record=fund_flow_for_date(doc,day,reference.get('calendar')) if capability=='fund_flow' else doc.get(key,{}).get(day,{})
    return {'state':'available' if record.get('items') else 'failed' if errors else 'unverified','source':record.get('source','eastmoney-public'),'date':day,'reason':'; '.join(errors[:3])+('；另有'+str(len(errors)-3)+'个板块缺口' if len(errors)>3 else '') if errors else doc.get('failure_reason') or (None if record.get('items') else '请在数据中心手动运行“盘面补充”以抓取并校验该日数据'),'scope':record.get('scope'),'verified_rows':record.get('verified_rows'),'fetched_at':record.get('fetched_at')}


def gainers_rows(market,reference,day,rows,max_items=None):
    """Use exact T/T-30 market dates; intermediate suspension is not exclusion."""
    from app.analytics.adjustments import continuous_prices
    calendar=market.calendar();position=calendar.index(day);baseline=calendar[position-30] if position>=30 else None
    expected=set(calendar[position-30:position+1]) if baseline else set()
    histories=market.history_panel(day,lookback=31)
    result=[]; represented=set()
    for original in rows:
        row=dict(original);symbol=row['instrument_id'];represented.add(symbol);history=[r for r in histories.get(symbol,[]) if not baseline or r['date']>=baseline]
        index={r['date']:r for r in history}; missing=sorted(expected-set(index))
        reason=None; value=None; basis='none'
        if not baseline:reason='已留存市场交易日不足31天'
        elif baseline not in index or day not in index:reason='缺少T或T-30端点报价，不能按最近31条报价替代'
        else:
            actions=market.actions(symbol,baseline,day)
            if actions:
                history,info=continuous_prices(history,actions,day,calendar)
                if not info.get('usable'):reason='区间公司行动未通过核验，暂不计算跨事件涨幅'
                else:index={r['date']:r for r in history};basis=info['price_basis']
            if reason is None:value=(index[day]['close']/index[baseline]['close']-1)*100
        row.update(return_30d=value,change_30d_pct=value,return_30d_start=baseline,return_30d_end=day,return_30d_price_basis=basis,return_30d_missing_sessions=len(missing),return_30d_missing_dates=missing,return_30d_reason=reason,return_30d_quality='unknown' if reason else 'endpoint_verified_with_gaps' if missing else 'endpoint_verified')
        result.append(row)
    # Search includes master entries without a quote on this day. They remain
    # unranked with an explicit reason, instead of silently disappearing.
    for instrument in market.instruments(limit=10000):
        if instrument.get('kind')!='stock' or instrument['instrument_id'] in represented:continue
        if instrument.get('list_date') and instrument['list_date']>day:continue
        if instrument.get('end_date') and instrument['end_date']<day:continue
        result.append({**instrument,'change_pct':None,'return_30d':None,'change_30d_pct':None,'return_30d_start':baseline,'return_30d_end':day,'return_30d_reason':'该盘面日未留存报价，保留证券并标为未知','return_30d_quality':'unknown','topics':[],'technical_observation':'该日行情待核验','limit_up_ytd':None})
    result.sort(key=lambda r:(not number(r.get('return_30d')),-r['return_30d'] if number(r.get('return_30d')) else 0,r['instrument_id']))
    known=0
    for row in result:
        row['rank']=None
        if number(row.get('return_30d')):known+=1;row['rank']=known
    selected=result if max_items is None else [row for row in result if row['rank'] is not None][:max_items]
    from app.services.topic_member_metrics import BATCH_SIZE, limit_metrics
    evidence_symbols=sorted({row['instrument_id'] for row in result})
    if max_items is not None:
        wanted={row['instrument_id'] for row in selected}
        # Annual proof metadata describes its original 256-security batch.
        # Retain those exact cohorts: an invalid event for another member of
        # the cohort can affect market_full_days, even for a valid top row.
        evidence_symbols=[symbol for offset in range(0,len(evidence_symbols),BATCH_SIZE)
                          for batch in [evidence_symbols[offset:offset+BATCH_SIZE]]
                          if wanted.intersection(batch) for symbol in batch]
    limits=limit_metrics(market,day,evidence_symbols,reference.get('_supplement',{}),reference)
    for row in selected:row.update(limits.get(row['instrument_id'],{}))
    return selected,{'window':'近30个市场交易日','start':baseline,'end':day,'covered_stocks':known,'unknown_count':len(result)-known,'universe_count':len(result),'with_intermediate_gaps':sum(bool(r.get('return_30d_missing_sessions')) and number(r.get('return_30d')) for r in result),'observation':'完整证券搜索；未知端点保留未排名，停牌/缺日报价不会自动清空端点收益','return_basis':'T/T-30精确交易日端点，区间有行动时仅用可核验事件前复权','annual_limitup_coverage':reference.get('_supplement',{}).get('annual_coverage',{}) and {k:v for k,v in annual_coverage(reference['_supplement'],calendar,day).items() if k!='counts' and k!='missing_dates'}}


def cached_gainers(market,status,reference,day,rows,max_items=None):
    # A top-five overview must never seed the full searchable/sortable rank.
    # Supplement publication/repair can alter facts without changing its old
    # created_at timestamp; fingerprint the actual retained document instead.
    supplement=reference.get('_supplement',{})
    verified=reference.get('_supplement_cache_source')
    fingerprint=verified[1] if isinstance(verified,tuple) and len(verified)==2 and verified[0] is supplement else None
    key=(status['snapshot_id'],status.get('path'),day,fingerprint if fingerprint is not None else digest(supplement),max_items)
    with _gainers_lock:
        if key not in _gainers_cache:
            _gainers_cache[key]=gainers_rows(market,reference,day,rows,max_items)
            while len(_gainers_cache)>4:_gainers_cache.popitem(last=False)
        _gainers_cache.move_to_end(key)
        return _gainers_cache[key]


def fund_flow_for_date(document,day,calendar=None):
    """Recalculate the requested day's window, never reuse an anchor's 20d flag."""
    records=document.get('fund_flows',{})
    ordered=([(day,records[day])] if records.get(day,{}).get('items') else [])+[(anchor,record) for anchor,record in sorted(records.items(),reverse=True) if anchor!=day and anchor>=day]
    for anchor,record in ordered:
        rows=[r for r in (record.get('items',[]) if anchor==day else record.get('history',[])) if r.get('date')==day]
        if not rows:continue
        expected=sorted({d for d in calendar or [] if d<=day})[-20:]
        calendar_complete=len(expected)==20 and expected[-1]==day
        history=[r for r in record.get('history',[]) if r.get('date','9999')<=day and (not expected or r.get('date')>=expected[0])]
        by_code={}
        for row in history:
            code=row.get('topic_code') or row.get('topic_id')
            if code:by_code.setdefault(code,set()).add(row['date'])
        codes={r.get('topic_code') or r.get('topic_id') for r in record.get('items',[])+history if r.get('topic_code') or r.get('topic_id')}
        gaps=[];complete_count=0
        for code in sorted(codes):
            present=by_code.get(code,set());missing=[d for d in expected if d not in present]
            complete=calendar_complete and not missing
            if complete:complete_count+=1
            else:gaps.append({'topic_code':code,'dates':missing if calendar is not None else None,'observed_days':len(present),'expected_days':20,'calendar_window_complete':calendar_complete})
        original=record.get('coverage',{});directory_count=original.get('directory_count')
        observed_count=len({r.get('topic_code') or r.get('topic_id') for r in rows})
        directory_complete=isinstance(directory_count,int) and not isinstance(directory_count,bool) and directory_count==observed_count
        history_complete=directory_complete and calendar_complete and complete_count==directory_count
        coverage={**original,'requested_date':day,'historical_series_anchor':anchor,'accepted_count':observed_count,'failed_count':max(0,directory_count-observed_count) if isinstance(directory_count,int) else None,'scope_label':'当前资金页行业目录所留存的当日历史序列','complete':directory_complete,
                  'source_history_20d_complete':original.get('history_20d_complete'),'source_history_complete_count':original.get('history_complete_count'),'source_failed_count':original.get('failed_count'),
                  'history_20d_complete':history_complete,'history_complete_count':complete_count,'history_gaps':gaps,'history_observed_days':len({r['date'] for r in history}),'history_window_expected_days':20,'history_expected_dates':expected if calendar is not None else None,'history_calendar_window_complete':calendar_complete,
                  'history_coverage_reason':None if history_complete else '观察日交易日历不足完整20日或未提供，不能确认20日完整' if not calendar_complete else '观察日对应20个交易日内存在行业资金缺日；未沿用最新采集日的完整标识'}
        return {**record,'date':day,'items':rows,'history':history,'coverage':coverage,'complete':directory_complete,'date_evidence':'provider-f51，未用当前快照回填'}
    return {}

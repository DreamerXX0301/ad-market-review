"""Deterministic close-signal/next-open research simulator; no SQL or HTTP."""
from collections import defaultdict
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from math import isfinite
import re

from app.domain.conditions import evaluate, names, validate, metrics_from_enriched
from app.domain.universe import identity_flags
from app.domain.trade_rules import strategy_predicates
from app.domain.history_events import HistoryContext, nodes as history_nodes, current_names, warmup as event_warmup, retain_start as history_retain_start
from .indicators import enrich_bars, aggregate_weekly
from .monthly import aggregate_monthly,enrich_monthly_bars
from .period_dates import latest_closed_date,completed_indicator_bars
from app.services.strategy_periods import periods_for,dependencies_for_period

VERSION='restricted-next-open-v5-event-percent-cross-cycle'
DEFAULTS={'initial_capital':100000,'max_positions':5,'position_pct':20,'hold_days':10,'stop_loss_pct':8,'take_profit_pct':20,'commission_bps':3,'min_commission':5,'sell_tax_bps':5,'slippage_bps':5,'exit_ma':0}
WARNINGS=['受限研究样本：当前证券名单、区间内完整报价且无公司行动；存在幸存者与事后样本选择偏差，不是严格无偏的全市场历史回测。','撮合市场范围：仅主板、创业板的当前非 ST 研究样本；科创板、北交所和当前 ST 不参与撮合，不能据此评估策略在这些股票上的表现。','仅未复权原始价格，不模拟未经完整验收的分红送股；历史ST及退市身份覆盖不足，ST 筛选按当前名称识别，不能冒充历史逐日身份。','日收盘产生信号，下一市场交易日开盘尝试成交；止盈止损也按收盘判断，不能推断盘中成交。','费用、滑点为可配置研究参数，非现行法定费率声明；期末持仓估值且不强制平仓。']
UNSUPPORTED={'topic_ids':'缺少历史题材成员版本','limit_up':'缺少完整历史限价身份与涨停证据','prev_limit_up':'昨日涨停条件尚未接入回测的完整历史限价证据，不能按上一条报价或未来来源补算','limit_up_count_1y':'截至昨日近一年涨停次数尚未接入回测的完整滚动历史来源证据，不能用年内次数或未来来源补算','limit_up_1y':'缺少完整一年历史涨停证据','turnover_ratio_pct':'换手率数据未覆盖','trading_age':'跟踪字段不是选股历史特征','tracking_change_pct':'跟踪字段不是选股历史特征','close_below_entry':'选股时尚无事件日价格'}
UNSUPPORTED['close_below_entry_low']='事件日最低价属于跟踪结束基准，回测没有本轮入池基准'
UNSUPPORTED['close_above_entry_high']='本轮事件日最高价属于跟踪提醒基准，选股回测没有该跟踪轮次，不能作为回测条件'


def D(v):return Decimal(str(v))
def cents(v):return D(v).quantize(D('.01'),rounding=ROUND_HALF_UP)


def config_checked(payload):
    config={**DEFAULTS,**{k:v for k,v in payload.items() if k in DEFAULTS}}
    ranges={'initial_capital':(1000,1e10),'max_positions':(1,20),'position_pct':(.1,100),'hold_days':(1,250),'stop_loss_pct':(0,99),'take_profit_pct':(0,1000),'commission_bps':(0,1000),'min_commission':(0,1000),'sell_tax_bps':(0,1000),'slippage_bps':(0,1000),'exit_ma':(0,250)}
    for key,(lo,hi) in ranges.items():
        value=config[key]
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not isfinite(value) or not lo<=value<=hi:raise ValueError(f'{key}应为{lo}..{hi}的有限数值')
    for key in ('max_positions','hold_days','exit_ma'):
        if int(config[key])!=config[key]:raise ValueError(key+'应为整数')
        config[key]=int(config[key])
    if config['exit_ma'] not in (0,5,10,20,30,60,250):raise ValueError('退出均线仅支持0/5/10/20/30/60/250')
    return config


def _daily_breakout_lookback(node):
    if 'history_event' in node:
        event=node['history_event']
        count=event_warmup(event['where'])
        # EMA's full retained seed is read in prepare(); 36 is its minimum
        # verifiable warmup, not a claim that an EMA has finite memory.
        return max(event['window']['to']+(count if count is not None else 36)-1,_daily_breakout_lookback(event['then']))
    if node.get('field')=='close_breakout_high':return node['value']
    if 'intraday_high_change_pct' in (node.get('field'),node.get('right_field')):return 1
    return max((_daily_breakout_lookback(child) for child in node.get('all',node.get('any',[]))),default=0)


def warmup(definition,exit_ma=0,include_daily_lookback=True):
    n=max(2,exit_ma)
    if include_daily_lookback:n=max(n,_daily_breakout_lookback(definition)+1)
    def structure_window(node):
        if 'history_event' in node:return structure_window(node['history_event']['then'])
        if node.get('field')=='higher_lows':return node['value']+2
        return max((structure_window(child) for child in node.get('all',node.get('any',[]))),default=0)
    n=max(n,structure_window(definition))
    for field in current_names(definition):
        if field=='bullish_ma_alignment':n=max(n,60)
        match=re.search(r'ma(\d+)$',field)
        if match:n=max(n,int(match.group(1))+(1 if field.startswith('cross_') else 0))
        if field.startswith(('macd','kdj')) or field in ('kd_cross','kd_dead_cross'):n=max(n,36 if field.endswith('cross') else 35)
    return n


def universe_availability(ast):
    """Reject forced unsupported universes without evaluating price predicates.

    Non-identity predicates remain unknown. Therefore an OR price branch can
    still admit supported securities, while an AND forced-ST branch cannot.
    """
    scopes={(board,st):evaluate(ast,{'board':board,'is_st':st})['result'] for board in ('main','chinext','star','beijing') for st in (False,True)}
    supported=[board for board in ('main','chinext') if scopes[(board,False)] is not False]
    unsupported=any(result is not False for (board,st),result in scopes.items() if st or board in ('star','beijing'))
    reason=None
    if not supported:
        reason=('策略强制选择的股票范围超出撮合市场限制：当前仅支持主板、创业板的当前非 ST 股票，暂不支持仅 ST、科创板或北交所范围。请调整股票范围。' if unsupported else '策略的板块/ST范围条件相互矛盾，没有可执行股票范围。')
    warning='策略包含当前引擎未支持的 ST、科创板或北交所范围；本次仅评估其中主板、创业板当前非 ST 部分，不代表完整策略范围。' if supported and unsupported and names(ast)&{'board','is_st'} else None
    return {'reason':reason,'warning':warning,'supported_boards':supported,'current_st_allowed':False,'identity_scope':'current','historical_st_complete':False}


def availability(market,strategy,exit_ma=0):
    ast,rules,dependencies=strategy_predicates(strategy);fields=names(dependencies);cal=market.calendar();period=strategy.get('period','D')
    blocked=fields & UNSUPPORTED.keys()
    # Historical event-day closing pools have their own exact-date adapter.
    # Current-day limit predicates retain their existing backtest restriction.
    if 'limit_up' not in current_names(dependencies):blocked.discard('limit_up')
    reason='；'.join(UNSUPPORTED[f] for f in sorted(blocked))
    universe=universe_availability({'all':[ast,rules['entry']]} if rules['entry'] is not None else ast)
    reason='；'.join(value for value in (reason,universe['reason']) if value)
    warnings=list(WARNINGS)
    if universe['warning']:warnings.append(universe['warning'])
    listing_coverage=None
    identity_method=getattr(market,'instruments',None)
    if callable(identity_method):
        stocks=[row for row in identity_method(limit=10000) if row.get('kind',row.get('asset_type','stock')) in ('stock','equity','a_share','A')]
        def has_listing(row):
            value=row.get('list_date')
            try:return isinstance(value,str) and date.fromisoformat(value).isoformat()==value
            except ValueError:return False
        known=sum(has_listing(row) for row in stocks)
        listing_coverage={'observed_stock_count':len(stocks),'known_listing_date_count':known,
                          'missing_listing_date_count':len(stocks)-known,'complete':bool(stocks and known==len(stocks)),
                          'basis':'来源证券主数据的有效上市日期；不按已观察报价推断交易风控身份'}
        if stocks and not known:
            reason='；'.join(value for value in (reason,'当前股票主数据全部缺少有效上市日期，无法排除新股交易例外，回测不可用；不会按已有报价推断上市日期或生成权益结果') if value)
        elif stocks and known<len(stocks):
            warnings.append(f'{len(stocks)-known}只股票缺少有效上市日期，身份覆盖不完整，仍需逐股校验；不按已观察报价推断上市日期。')
    n=warmup(dependencies_for_period(dependencies,period,period) or {},exit_ma if period=='D' else 0,include_daily_lookback=period=='D')
    requested=periods_for(dependencies,period)|{period}
    earliest_by_period=[]
    dummy=[{'date':d,'open':1,'high':1,'low':1,'close':1,'volume':1,'amount':1} for d in cal]
    for selected in sorted(requested):
        count=warmup(dependencies_for_period(dependencies,selected,period) or {},exit_ma if selected=='D' else 0,include_daily_lookback=selected=='D')
        if selected in ('W','M'):
            series=aggregate_weekly(dummy,cal) if selected=='W' else aggregate_monthly(dummy,cal)
            closed=[b for b in series if b['complete']]
            earliest_by_period.append(closed[count-1]['date'] if len(closed)>=count else None)
        else:earliest_by_period.append(cal[count] if len(cal)>count else None)
    earliest=max(earliest_by_period) if earliest_by_period and all(earliest_by_period) else None
    if period in ('W','M'):
        daily_lookback=_daily_breakout_lookback(dependencies)
        daily_needed=max(daily_lookback+1 if daily_lookback else 0,exit_ma)
        if daily_needed:
            daily_earliest=cal[daily_needed-1] if len(cal)>=daily_needed else None
            earliest=max(earliest,daily_earliest) if earliest and daily_earliest else None
            if daily_earliest is None:reason=reason or f'现存{len(cal)}个日线交易日不足前{daily_lookback}个交易日新高窗口及观察日，或每日退出均线所需{exit_ma}日；合计需{daily_needed}个交易日'
    if not earliest:reason=reason or f'现存{len(cal)}个日线交易日不足{n}个{period}周期预热'
    valid=[d for d in cal if earliest and d>=earliest]
    if len(valid)<10:reason=reason or '预热后不足10个市场交易日'
    return {'available':not bool(reason),'reason':reason or None,'min_date':earliest,'max_date':cal[-1] if cal else None,'default_start':valid[-60] if len(valid)>=60 else (valid[0] if valid else None),'default_end':cal[-1] if cal else None,'warmup_bars':n,'daily_lookback_sessions':_daily_breakout_lookback(dependencies),'period':period,'trade_rules':rules,'warnings':warnings,'eligible_count':0 if listing_coverage and listing_coverage['observed_stock_count'] and not listing_coverage['known_listing_date_count'] else None,'execution_universe':universe,'listing_date_coverage':listing_coverage}


def prepare(market,strategy,start,end,progress=None,exit_ma=0):
    """Explicit ex-post restricted sample, disclosed in report; stream per symbol."""
    cal=market.calendar();dates=[d for d in cal if start<=d<=end];expected=set(dates)
    master={r['instrument_id']:r for r in market.instruments(limit=10000)}
    excluded=defaultdict(int);signals=defaultdict(list);quotes=defaultdict(dict);signal_rows=[];eligible=[];unknown=0;evaluated=0
    ast,rules,dependencies=strategy_predicates(strategy)
    needs_history=bool(history_nodes(dependencies))
    needs_history_limits=any('limit_up' in names(event['where']) for event in history_nodes(dependencies))
    if needs_history_limits:
        from app.services.strategy_limits import LimitConditionEvidence
        from app.services.history_events import attach_limit_events
        limit_evidence=LimitConditionEvidence(market)
    buy_ast={'all':[ast,rules['entry']]} if rules['entry'] is not None else ast
    exit_unknown=0
    total=market.status().get('symbols',0)
    for i,(symbol,rows) in enumerate(market.iter_histories(end)):
        identity=master.get(symbol,{})
        code=symbol[:6]
        if symbol.endswith('.BJ') or code.startswith(('688','689')):excluded['unsupported_board']+=1;continue
        if 'ST' in identity.get('name','').upper():excluded['current_st']+=1;continue
        if not identity.get('list_date'):excluded['identity_missing']+=1;continue
        if (date.fromisoformat(start)-date.fromisoformat(identity['list_date'])).days<30:excluded['new_listing']+=1;continue
        in_range=[r for r in rows if start<=r['date']<=end]
        if {r['date'] for r in in_range}!=expected:excluded['missing_quotes_in_interval']+=1;continue
        if any(r.get('corporate_action') for r in in_range):excluded['corporate_action_in_interval']+=1;continue
        if any(r['volume']<=0 or min(r['open'],r['close'],r['high'],r['low'])<=0 for r in in_range):excluded['invalid_or_no_trading']+=1;continue
        eligible.append(symbol)
        prev=None
        for r in rows:
            if r['date']>=start:quotes[r['date']][symbol]=r
            if r['date']<start:prev=r
        # Store a genuine prior close for the first execution's price-band check.
        if prev:quotes['_previous'][symbol]=prev
        daily_indicators=enrich_bars(rows,cal)
        history_context=HistoryContext(rows,cal,end,market.status().get('snapshot_id'),retain_start=history_retain_start([dependencies],cal,start)) if needs_history else None
        if needs_history_limits:attach_limit_events(history_context,symbol,[dependencies],limit_evidence)
        default_period=strategy.get('period','D')
        required_periods=periods_for(dependencies,default_period)|{default_period}
        daily_breakout={row['date']:row for row in daily_indicators} if default_period in ('W','M') and names(dependencies)&{'close_breakout_high','intraday_high_change_pct'} else {}
        if exit_ma:
            for row in daily_indicators:
                if row['date']>=start:quotes[row['date']][symbol]['exit_ma_value']=row.get(f'ma{exit_ma}')
        series={'D':daily_indicators}
        if 'W' in required_periods:
            series['W']=completed_indicator_bars(rows,cal,end,'W')
        if 'M' in required_periods:
            series['M']=completed_indicator_bars(rows,cal,end,'M')
        if 'higher_lows' in names(dependencies):
            from .higher_lows import summaries as higher_lows_summaries
            for selected,values in series.items():
                structure=higher_lows_summaries(rows,cal,end,selected)
                for row in values:row['higher_lows_state']=structure.get(row['date'])
        # Each summary is computed once per symbol. Scoped conditions select a
        # closed bar whose date is at or before the signal day, never future OHLC.
        scoped={selected:{r['date']:metrics_from_enriched(r,values[index-1] if index else None)
                          for index,r in enumerate(values)} for selected,values in series.items() if selected in required_periods}
        pointers={selected:-1 for selected in required_periods}
        enriched=series[default_period]
        previous=None
        for row in enriched:
            if row['date']<start:previous=row;continue
            m=metrics_from_enriched(row,previous);m.update(identity_flags(identity,row['date']));m['indicator_period']=strategy.get('period','D');previous=row
            if required_periods-{default_period}:
                by_period={default_period:dict(m)}
                for selected in required_periods-{default_period}:
                    values=series[selected];pointer=pointers[selected]
                    while pointer+1<len(values) and values[pointer+1]['date']<=row['date']:pointer+=1
                    pointers[selected]=pointer
                    by_period[selected]={**scoped[selected][values[pointer]['date']],'indicator_period':selected} if pointer>=0 and values[pointer]['date']==latest_closed_date(cal,row['date'],selected) else {}
                m['_period_metrics']=by_period
            if daily_breakout:
                daily_state=daily_breakout.get(row['date'],{})
                m.update({field:daily_state.get(field) for field in ('breakout_valid','breakout_previous_sessions','breakout_blocking_distance','new_high_current_close')})
                m.update({field:daily_state.get(field) for field in ('intraday_high_change_pct','intraday_high_change_pct_reason')})
            if rules['exit'] is not None:
                exit_check=evaluate(rules['exit'],m,history_context.at(row['date']) if history_context else None)
                if exit_check['result'] is None:exit_unknown+=1
                quotes[row['date']][symbol].update(custom_exit=exit_check['result'],custom_exit_checks=exit_check)
            decision=evaluate(buy_ast,m,history_context.at(row['date']) if history_context else None);evaluated+=1
            if decision['result'] is None:unknown+=1
            if decision['result'] is True:
                sig={'date':row['date'],'instrument_id':symbol,'code':code,'name':identity.get('name',symbol),'close':row['close'],'metrics':m,'checks':decision}
                signals[row['date']].append(sig);signal_rows.append(sig)
        if progress and i%300==0:progress('计算策略历史信号',min(55,5+int(50*i/max(total,1))))
    for group in signals.values():group.sort(key=lambda r:r['instrument_id'])
    quality={'scope':'restricted_research','total_symbols':total,'eligible_symbols':len(eligible),'excluded_symbols':sum(excluded.values()),'exclusions':dict(excluded),'evaluated_observations':evaluated,'unknown_observations':unknown,'exit_unknown_observations':exit_unknown,'matched_signals':len(signal_rows),'universe_selection_uses_interval_coverage':True,'historical_identity_complete':False,'strict_unbiased_backtest':False}
    return quotes,signals,signal_rows,quality


def simulate(calendar,quotes,signals,config,identities=None):
    """Pure execution: calendar includes baseline, quotes dates include _previous."""
    identities=identities or {};dates=calendar[1:];cash=cents(config['initial_capital']);initial=cash
    positions={};pending_sells={};orders=[];fills=[];trades=[];holding_rows=[];curve=[{'date':calendar[0],'equity':float(cash),'cash':float(cash),'drawdown':0.0,'baseline':True}]
    fees_total=D(0);peak=initial;max_dd=D(0);previous=quotes.get('_previous',{})
    def identity(symbol):return {'instrument_id':symbol,'code':symbol[:6],'name':identities.get(symbol,{}).get('name',symbol)}
    def fee(gross,side):return cents(max(D(config['min_commission']),gross*D(config['commission_bps'])/10000)+(gross*D(config['sell_tax_bps'])/10000 if side=='sell' else 0))
    def attempt(day,symbol,side,qty,reason,signal_date,checks=None):
        nonlocal cash,fees_total
        row=quotes.get(day,{}).get(symbol);old=previous.get(symbol)
        order={**identity(symbol),'order_id':f'order-{len(orders)+1}','date':day,'signal_date':signal_date,'side':side,'quantity':qty,'reason':reason,'status':'rejected','price':None}
        if checks is not None:order['checks']=checks
        orders.append(order)
        if not row or row['volume']<=0:order['reason']='停牌或缺少成交行情';return None
        if not old:order['reason']='缺少前收盘，无法核对限价';return None
        band=D('.20') if symbol.startswith(('300','301')) else D('.10')
        upper=cents(D(old['close'])*(1+band));lower=cents(D(old['close'])*(1-band))
        if side=='buy' and D(row['open'])>=upper:order['reason']='开盘涨停，保守不成交';return None
        if side=='sell' and D(row['open'])<=lower:order['reason']='开盘跌停，保守不成交';return None
        price=cents(D(row['open'])*(1+D(config['slippage_bps'])/10000*(1 if side=='buy' else -1)))
        if price<=0 or price>upper or price<lower or price>D(row['high']) or price<D(row['low']):order['reason']='滑点价格越过当日范围或限价';return None
        gross=cents(price*qty);charges=fee(gross,side)
        if side=='buy' and gross+charges>cash:order['reason']='资金不足（含费用）';return None
        if side=='sell' and gross<charges:order['reason']='费用超过卖出金额';return None
        cash=cents(cash-gross-charges if side=='buy' else cash+gross-charges);fees_total+=charges
        order.update(status='filled',price=float(price),fee=float(charges))
        fill={**order,'fill_id':f'fill-{len(fills)+1}','gross':float(gross),'fee':float(charges)};fills.append(fill)
        return price,gross,charges
    for idx,day in enumerate(dates):
        closed_today=set()
        for symbol,exit_info in list(pending_sells.items()):
            p=positions[symbol]
            if p['entry_date']>=day:continue
            done=attempt(day,symbol,'sell',p['quantity'],exit_info['reason'],exit_info['signal_date'],exit_info.get('checks'))
            if done:
                price,gross,charges=done;pnl=cents(gross-charges-p['cost'])
                trades.append({**identity(symbol),'entry_date':p['entry_date'],'exit_date':day,'entry_price':float(p['entry_price']),'exit_price':float(price),'quantity':p['quantity'],'net_pnl':float(pnl),'return_pct':float(pnl/p['cost']*100),'exit_reason':exit_info['reason'],'entry_fee':float(p['entry_fee']),'exit_fee':float(charges),**({'entry_checks':p['entry_checks']} if p.get('entry_checks') is not None else {}),**({'exit_checks':exit_info['checks']} if 'checks' in exit_info else {})})
                del positions[symbol];del pending_sells[symbol];closed_today.add(symbol)
        prior_day=calendar[idx]
        for signal in signals.get(prior_day,[]):
            symbol=signal['instrument_id']
            if symbol in positions or symbol in closed_today:continue
            if len(positions)>=config['max_positions']:
                orders.append({**identity(symbol),'date':day,'signal_date':prior_day,'side':'buy','status':'rejected','quantity':0,'price':None,'reason':'持仓数量上限'});continue
            row=quotes.get(day,{}).get(symbol)
            if not row:continue
            equity=cash+sum(D(quotes.get(day,{}).get(s,previous.get(s,{})).get('open',p['last_close']))*p['quantity'] for s,p in positions.items())
            budget=min(cash,equity*D(config['position_pct'])/100)
            approx=cents(D(row['open'])*(1+D(config['slippage_bps'])/10000))
            qty=int(budget/approx/100)*100 if approx>0 else 0
            while qty>0 and cents(approx*qty)+fee(cents(approx*qty),'buy')>budget:qty-=100
            if qty<=0:
                orders.append({**identity(symbol),'date':day,'signal_date':prior_day,'side':'buy','status':'rejected','quantity':0,'price':None,'reason':'不足一手及费用'});continue
            done=attempt(day,symbol,'buy',qty,'策略收盘信号',prior_day,signal.get('checks'))
            if done:
                price,gross,charges=done;positions[symbol]={'entry_date':day,'entry_price':price,'quantity':qty,'cost':gross+charges,'entry_fee':charges,'age':0,'last_close':row['close'],'entry_checks':signal.get('checks')}
        value=D(0)
        for symbol,p in positions.items():
            row=quotes.get(day,{}).get(symbol)
            if not row:raise ValueError('持仓行情缺失，不能用旧价格伪造权益')
            p['last_close']=row['close'];p['age']+=1;value+=D(row['close'])*p['quantity']
            ret=(D(row['close'])/p['entry_price']-1)*100;reason=None
            if config['stop_loss_pct'] and ret<=-D(config['stop_loss_pct']):reason='收盘止损'
            elif config['take_profit_pct'] and ret>=D(config['take_profit_pct']):reason='收盘止盈'
            elif row.get('custom_exit') is True:reason='自定义卖出条件'
            elif config['exit_ma'] and row.get('exit_ma_value') is not None and row['close']<row['exit_ma_value']:reason='收盘跌破退出均线'
            elif p['age']>=config['hold_days']:reason='持有交易日期限'
            if reason and symbol not in pending_sells:
                pending_sells[symbol]={'reason':reason,'signal_date':day}
                if reason=='自定义卖出条件':pending_sells[symbol]['checks']=row.get('custom_exit_checks')
            holding_rows.append({**identity(symbol),'date':day,'entry_date':p['entry_date'],'quantity':p['quantity'],'close':row['close'],'market_value':float(D(row['close'])*p['quantity']),'unrealized_pnl':float(D(row['close'])*p['quantity']-p['cost']),'trading_age':p['age']})
        equity=cents(cash+value);peak=max(peak,equity);dd=(peak-equity)/peak*100 if peak else D(0);max_dd=max(max_dd,dd)
        curve.append({'date':day,'equity':float(equity),'cash':float(cash),'drawdown':float(dd),'baseline':False})
        previous=quotes.get(day,{})
    for symbol,info in pending_sells.items():orders.append({**identity(symbol),'date':dates[-1],'signal_date':info['signal_date'],'side':'sell','quantity':positions[symbol]['quantity'],'price':None,'status':'not_filled','reason':'区间结束，保留持仓；'+info['reason']})
    for signal in signals.get(dates[-1],[]):orders.append({**identity(signal['instrument_id']),'date':dates[-1],'signal_date':dates[-1],'side':'buy','quantity':0,'price':None,'status':'not_filled','reason':'最后一日信号，无区间内下一开盘'})
    for i,order in enumerate(orders,1):order.setdefault('order_id',f'order-{i}')
    wins=[t['net_pnl'] for t in trades if t['net_pnl']>0];losses=[t['net_pnl'] for t in trades if t['net_pnl']<0]
    final=curve[-1]['equity'];n=len(dates)
    stats={'total_return':(final/float(initial)-1)*100,'annual_return':((final/float(initial))**(252/n)-1)*100 if final>0 else -100,'max_drawdown':float(max_dd),'win_rate':len(wins)/len(trades)*100 if trades else None,'payoff_ratio':(sum(wins)/len(wins))/abs(sum(losses)/len(losses)) if wins and losses else None,'trade_count':len(trades),'final_equity':final,'initial_capital':float(initial),'net_profit':final-float(initial),'total_fees':float(fees_total),'holding_count':len(positions),'trading_days':n,'wins':len(wins),'losses':len(losses),'breakeven':len(trades)-len(wins)-len(losses),'unrealized_pnl':sum(float(D(p['last_close'])*p['quantity']-p['cost']) for p in positions.values())}
    return {'stats':stats,'equity':curve,'orders':orders,'fills':fills,'trades':trades,'positions':holding_rows}


def run_backtest(market,strategy,payload,progress=None):
    config=config_checked(payload);bounds=availability(market,strategy,config['exit_ma'])
    if not bounds['available']:raise ValueError(bounds['reason'])
    start=payload.get('start_date');end=payload.get('end_date');cal=market.calendar()
    if start not in cal or end not in cal:raise ValueError('起止日期必须是已留存的完成交易日')
    if start>end or start<bounds['min_date'] or end>bounds['max_date']:raise ValueError('区间超出策略预热后的真实数据范围')
    dates=[d for d in cal if start<=d<=end]
    if len(dates)<10:raise ValueError('回测至少需要10个市场交易日')
    previous=cal[cal.index(start)-1]
    quotes,signals,signal_rows,quality=prepare(market,strategy,start,end,progress,config['exit_ma'])
    if not quality['eligible_symbols']:raise ValueError('该区间无满足明确数据约束的研究样本')
    identities={r['instrument_id']:r for r in market.instruments(limit=10000)}
    if progress:progress('撮合并对账权益',70)
    result=simulate([previous]+dates,quotes,signals,config,identities)
    result.update(config=config,trade_rules=bounds['trade_rules'],signals=signal_rows,data_quality=quality,start_date=start,end_date=end,warnings=bounds['warnings']+[f"样本共{quality['total_symbols']}只，可用{quality['eligible_symbols']}只，排除{quality['excluded_symbols']}只；逐项原因见data_quality。"],execution_universe=bounds['execution_universe'],engine_version=VERSION)
    if any('higher_lows' in names(tree) for tree in [strategy['definition']]+[tree for tree in bounds['trade_rules'].values() if tree is not None]):
        from .higher_lows import VERSION as structure_version
        result['condition_contracts']={'higher_lows':structure_version}
    return result

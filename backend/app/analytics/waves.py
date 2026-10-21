"""Reproducible, delayed swing candidates; no hindsight Elliott-wave claims."""
from math import isfinite


def wave_patterns(points):
    """Price-skeleton candidates, never a claim about lower-degree subwaves.

    Numeric labels belong to one validated five-leg impulse candidate. ABC
    is appended only to that same candidate and a three-leg zigzag skeleton.
    Mirroring prices implements the exact same checks for downward impulses.
    """
    patterns=[]
    for start in range(max(0,len(points)-5)):
        group=points[start:start+6]
        if len({point.get('segment_id') for point in group}) != 1:continue
        direction=1 if group[0]['kind']=='low' else -1
        prices=[direction*point['price'] for point in group]
        if [point['kind'] for point in group] != (['low','high']*3 if direction==1 else ['high','low']*3):continue
        p0,p1,p2,p3,p4,p5=prices
        if not (p1>p0 and p0<p2<p1 and p3>p1 and p1<p4<p3 and p5>p3):continue
        lengths=[p1-p0,p3-p2,p5-p4]
        if lengths[1]<min(lengths[0],lengths[2]):continue
        chosen=[{**point,'label':str(i)+'?' if i else '0','phase':'impulse'} for i,point in enumerate(group)]
        correction=points[start+6:start+9]
        if len(correction)==3 and all(point.get('segment_id')==group[0].get('segment_id') for point in correction):
            a,b,c=[direction*point['price'] for point in correction]
            if a<p5 and a<b<p5 and c<a:
                chosen.extend({**point,'label':label+'?','phase':'correction'} for point,label in zip(correction,['A','B','C']))
        pattern_id=f"{group[0]['date']}-{group[-1]['date']}-{'up' if direction==1 else 'down'}"
        patterns.append({'pattern_id':pattern_id,'direction':'up' if direction==1 else 'down',
                         'label':('上涨' if direction==1 else '下跌')+'五浪候选',
                         'start_date':chosen[0]['date'],'end_date':chosen[-1]['date'],
                         'confirmed_at':max(point['confirmed_at'] for point in chosen),
                         'has_correction':len(chosen)==9,'points':chosen,
                         'rules':['2浪未越过起点','3浪不是1/3/5中最短','4浪未进入1浪价格区间','5浪突破3浪端点'],
                         'scope':'仅检验本周期转折价格骨架；未识别浪级、内部5-3-5结构、延长浪、楔形与复杂调整，不是完整艾略特认定'})
    return patterns


def _bar_boundary(row):
    """Separate unusable aggregate bars from a valid first bar after a reset.

    A raw daily ex-date can begin a new segment. A weekly/monthly bar containing
    an unadjusted action mixes price bases inside its OHLC and is excluded.
    Indicator warmup alone does not break an otherwise comparable price run.
    """
    invalid_flags = (
        'week_closed', 'month_closed', 'complete',
        'weekly_quote_coverage_complete', 'monthly_quote_coverage_complete',
        'monthly_price_comparable',
    )
    if any(row.get(field) is False for field in invalid_flags):
        return '形成中、报价不完整或价格不可比的K柱，不作转折或确认', None
    aggregate = 'week_closed' in row or 'month_closed' in row
    if aggregate and row.get('corporate_action'):
        return '周/月聚合柱含未复权公司行动，不使用混合价格OHLC', None
    reasons = []
    if row.get('price_continuity') is False:
        reasons.append('价格连续性重置')
    if row.get('corporate_action'):
        reasons.append('未复权公司行动')
    for field in ('missing_market_sessions_before', 'weekly_missing_market_sessions', 'monthly_missing_market_sessions'):
        value = row.get(field)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            reasons.append('明确缺少市场交易日报价')
    for field in ('weekly_missing_market_dates', 'monthly_missing_market_dates'):
        if row.get(field):reasons.append('明确缺少市场交易日报价')
    for field in ('weekly_continuity_reset_reason', 'monthly_continuity_reset_reason', 'indicator_reset_reason'):
        if row.get(field):reasons.append(str(row[field]))
    return None, '；'.join(dict.fromkeys(reasons)) or None


def _continuous_segments(bars):
    segments = []; current = []; excluded = []; boundaries = []
    previous_date = None
    for row in bars:
        try:
            high = float(row['high']); low = float(row['low'])
            valid = isfinite(high) and isfinite(low) and high >= low > 0
        except (KeyError, TypeError, ValueError, OverflowError):
            valid = False
        invalid, reset = _bar_boundary(row)
        if not valid:invalid = '高低价缺失、无效或相互矛盾'
        if 'close' in row:
            try:
                close = float(row['close'])
                close_valid = not isinstance(row['close'], bool) and isfinite(close) and close > 0 and valid and low <= close <= high
            except (TypeError, ValueError, OverflowError):
                close_valid = False
            if not close_valid:invalid = '收盘价缺失、非正或超出当日高低价，不作转折或确认'
        if 'volume' in row:
            try:
                volume = float(row['volume'])
                volume_valid = not isinstance(row['volume'], bool) and isfinite(volume) and volume > 0
            except (TypeError, ValueError, OverflowError):
                volume_valid = False
            if not volume_valid:invalid = '成交量未知、非正或无效，不把停牌/无成交柱作为转折或确认'
        if previous_date is not None and row.get('date', '') <= previous_date:
            invalid = 'K柱日期重复或未按时间升序，不能作为确认序列'
        if invalid or reset:
            if current:segments.append(current); current = []
            if invalid:excluded.append({'date': row.get('date'), 'reason': invalid})
            else:boundaries.append({'date': row.get('date'), 'reason': reset})
        if not invalid:current.append(row)
        previous_date = row.get('date')
    if current:segments.append(current)
    return segments, excluded, boundaries


def _candidate_points(bars, confirmation_bars, threshold_pct, segment_id):
    points=[]
    for i in range(confirmation_bars,len(bars)-confirmation_bars):
        row=bars[i];window=bars[i-confirmation_bars:i+confirmation_bars+1]
        high=float(row['high']);low=float(row['low'])
        is_high=all(high>=float(r['high']) for r in window) and any(high>float(r['high']) for r in window)
        is_low=all(low<=float(r['low']) for r in window) and any(low<float(r['low']) for r in window)
        # An outside bar is directionally ambiguous, so no invented intra-bar order.
        if is_high==is_low:continue
        kind='high' if is_high else 'low';price=high if is_high else low
        if not isfinite(price) or price<=0:continue
        point={'date':row['date'],'price':price,'kind':kind,'confirmed_at':bars[i+confirmation_bars]['date'],'segment_id':segment_id}
        if points and points[-1]['kind']==kind:
            if (kind=='high' and price>points[-1]['price']) or (kind=='low' and price<points[-1]['price']):points[-1]=point
            continue
        if points and abs(price/points[-1]['price']-1)*100<threshold_pct:continue
        points.append(point)
    for point in points:point['label']='高点' if point['kind']=='high' else '低点'
    return points


def candidate_waves(bars,confirmation_bars=2,threshold_pct=2.0):
    runs, excluded, boundaries = _continuous_segments(bars)
    points=[];segments=[];patterns=[]
    for run in runs:
        segment_id = 'price-run-' + run[0]['date']
        local = _candidate_points(run, confirmation_bars, threshold_pct, segment_id)
        points.extend(local)
        segments.extend({'start_date':a['date'],'start_price':a['price'],'end_date':b['date'],'end_price':b['price'],'direction':'up' if b['price']>a['price'] else 'down','segment_id':segment_id} for a,b in zip(local,local[1:]))
        patterns.extend(wave_patterns(local))
    return {'method':'confirmed-fractal-price-skeleton-v3-continuous-runs','label':'波浪结构候选','points':points,'segments':segments,'patterns':patterns,'threshold_pct':threshold_pct,'confirmation_bars':confirmation_bars,'segment_count':len(runs),'excluded_bars':excluded,'continuity_breaks':boundaries,'notice':'高低点转折与五浪结构分开显示。1—5仅标记通过价格骨架规则的上涨/下跌五浪候选；ABC仅接在对应五浪后的简单折返候选。转折需后续完整可比K柱确认；形成中或报价不完整柱不参与，明确缺口与未复权公司行动处分段，不跨段确认或连线。候选会随后续行情改变，未验证内部子浪，不代表确定艾略特数浪或买卖点。'}

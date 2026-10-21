"""Read-only local acceptance: never submits tasks, rules or credentials."""
import argparse
import json
import time
import urllib.request
from pathlib import Path


def run(base):
    checks=[];timings={}
    def check(name,value):
        checks.append({'name':name,'passed':bool(value)})
    def get(path):
        began=time.monotonic()
        with urllib.request.urlopen(base+path,timeout=90) as response:
            result=json.load(response)
        timings[path]=round(time.monotonic()-began,4)
        return result
    boot=get('/bootstrap');day=boot['data_date']
    data=get('/data/status');init=data.get('initialization',{})
    check('published main market disables initialization',init.get('main_published') is True and init.get('can_initialize') is False)
    window=data.get('sync_window',{})
    check('server publishes Beijing 18:00 incremental window',window.get('timezone')=='Asia/Shanghai' and window.get('cutoff')=='18:00' and isinstance(window.get('increment_allowed'),bool))
    fields={field['field']:field for field in get('/strategy-fields')['items']}
    check('intraday daily high return is a numeric strategy field',fields.get('intraday_high_change_pct',{}).get('type')=='number')
    for field in ('macd_dead_cross','kd_dead_cross','close_below_entry_low'):
        check(field+' boolean contract',fields.get(field,{}).get('type')=='boolean')
    check('entry low is tracking-only',fields.get('close_below_entry_low',{}).get('tracking_only') is True)
    strategies=get('/strategies')['items']
    check('old and current strategies have explicit trade rule defaults',all(isinstance(row.get('trade_rules'),dict) and set(row['trade_rules'])=={'entry','exit'} for row in strategies))
    tracking=get('/tracking-rounds?status=all')['items']
    check('tracking low evidence fields exposed',all(all(key in row for key in ('entry_low','entry_low_date','entry_low_state','entry_low_reason','entry_low_basis')) for row in tracking))
    ladder=get('/market/ladder?date='+day+'&limit=1')
    whole=get('/market/ladder?date='+day+'&limit=1000')
    rows=ladder['items'];symbols={row['instrument_id'] for row in rows}
    check('ladder is complete returned list even with limit=1',len(rows)==ladder['total']==len(whole['items']) and symbols=={row['instrument_id'] for row in whole['items']})
    check('confirmed ladder rows have checked closing event',all(row.get('limit_up_state') is True for row in rows))
    for key in ('rebound_items','failed_items','twenty_cm_items','theme_counts','unverified_items'):
        check(key+' explicit whole list',isinstance(ladder.get(key),list))
    check('rebound sequence states are known',all(row.get('rebound_state') is True and row.get('previous_limit_up_state') is False and row.get('before_previous_limit_up_state') is True for row in ladder['rebound_items']))
    check('failed promotion is not inferred from a missing pool row',all(row.get('previous_limit_up_state') is True and row.get('limit_up_state') is False for row in ladder['failed_items']))
    check('20cm classification is explicitly checked',all(row.get('twenty_cm_state') is True for row in ladder['twenty_cm_items']))
    for topic in ladder['theme_counts']:
        hits={row['instrument_id'] for row in rows if any(item.get('topic_id')==topic['topic_id'] for item in row.get('topics',[]))}
        check('theme count matches returned confirmed symbols '+topic['topic_id'],len(hits)==topic['limit_up_observed_count'] and (topic['limit_up_count']==len(hits) if topic['complete'] else topic['limit_up_count'] is None))
    wave_counts={}
    for period in ('D','W','M'):
        chart=get('/instruments/000001.SH/chart?period='+period+'&as_of='+day)
        waves=chart['waves'];patterns=waves.get('patterns',[]);wave_counts[period]=len(patterns)
        check(period+' wave confirmations never exceed requested day',all(point['confirmed_at']<=day for point in waves['points']))
        bars={bar['date']:bar for bar in chart['bars']}
        check(period+' patterns retain one segment',all(len({point.get('segment_id') for point in pattern['points']})==1 for pattern in patterns))
        check(period+' pattern endpoints use retained complete bars',all(bars[point['date']].get('complete') is not False and bars[point['date']].get('week_closed') is not False and bars[point['date']].get('month_closed') is not False for pattern in patterns for point in pattern['points']))
    sentiment=get('/market/emotion?date='+day).get('sentiment',{})
    check('two sentiment dimensions and atmosphere retain requested date',sentiment.get('date')==day and all(isinstance(sentiment.get(key),dict) for key in ('market','short_term','atmosphere')))
    for key in ('market','short_term'):
        dimension=sentiment.get(key,{})
        check(key+' counts reconcile without zero-filled unknowns',dimension.get('sample_count',-1)+dimension.get('unknown_count',-1)==dimension.get('observed_count'))
        check(key+' metrics expose formula and null reason',all(item.get('formula') and (item.get('value') is not None or item.get('reason')) for item in dimension.get('metrics',[])))
    short=sentiment.get('short_term',{})
    rate=next((m.get('value') for m in short.get('metrics',[]) if m.get('key')=='promotion_rate_pct'),None)
    check('incomplete previous cohort has no fabricated promotion rate',short.get('cohort_source_complete') is True or rate is None)
    stock = next((row['instrument_id'] for row in rows if row.get('instrument_id')), '600825.SH')
    for period in ('D', 'W', 'M'):
        chart = get('/instruments/' + stock + '/chart?period=' + period + '&as_of=' + day)
        waves = chart.get('waves')
        check('stock ' + period + ' turning layer is available', isinstance(waves, dict) and isinstance(waves.get('points'), list))
        if isinstance(waves, dict):
            check('stock ' + period + ' turns never confirm after observation', all(point['confirmed_at'] <= day for point in waves.get('points', [])))
            check('stock ' + period + ' labels are turns rather than arbitrary numbering', all(point.get('label') in ('高点', '低点') for point in waves.get('points', [])))
    return {'method':'read_only_local_api_no_source_requests_or_business_commands','date':day,'passed':sum(row['passed'] for row in checks),'total':len(checks),'failed':[row for row in checks if not row['passed']],
            'ladder_counts':{key:len(ladder[key]) for key in ('items','rebound_items','failed_items','twenty_cm_items','theme_counts','unverified_items')},'main_coverage':ladder.get('main_coverage'),'wave_pattern_counts':wave_counts,'initialization':init,'tracking_count':len(tracking),'request_seconds':timings,'checks':checks}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--base',default='http://127.0.0.1:8788/api/v1');parser.add_argument('--output',required=True)
    args=parser.parse_args();proof=run(args.base)
    path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({key:proof[key] for key in ('passed','total','failed','ladder_counts','wave_pattern_counts')},ensure_ascii=False))
    raise SystemExit(bool(proof['failed']))

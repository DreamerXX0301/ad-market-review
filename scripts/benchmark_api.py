"""Read-only HTTP timings against a running local API, without source requests.

Run once after a fresh server start for a cold reading. Timings include JSON
transfer/decompression, but do not stand in for browser interaction timings.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import time

import httpx


def benchmark(base):
    rows=[]
    with httpx.Client(base_url=base.rstrip('/'),timeout=60) as client:
        def request(path, label=None):
            start=time.perf_counter();response=client.get(path);response.raise_for_status()
            data=response.json()
            rows.append({'label':label or path,'path':path,'seconds':round(time.perf_counter()-start,4),'decoded_bytes':len(response.content),'wire_bytes':int(response.headers.get('content-length',len(response.content))),'encoding':response.headers.get('content-encoding')})
            return data
        boot=request('/bootstrap','首次应用连接（冷进程时含行情加载）')
        dates=sorted(boot['market_dates']);latest=dates[-1]
        for day in dates[-4:][::-1]:
            start=time.perf_counter()
            with ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(request,[f'/overview?date={day}',f'/instruments/000001.SH/chart?as_of={day}']))
            rows.append({'label':'盘面与指数并行切日','date':day,'seconds':round(time.perf_counter()-start,4),'date_matches':results[0]['date']==day and results[1]['as_of']==day})
        request('/overview?date='+latest,'返回最近访问日期')
        for kind in ('emotion','sectors','ladder','gainers','abnormal','flow'):
            request(f'/market/{kind}?date={latest}&page_size=25')
        topics=request('/topics')
        concepts=[row for row in topics['items'] if row['kind']=='concept']
        culture=next((row for row in concepts if '文化传媒' in row['name']),concepts[0] if concepts else None)
        if culture:
            path='/topic-members?topic_ids='+culture['topic_id']
            request(path+'&sort_by=return_ytd_pct','题材历史指标首次查询')
            request(path+'&sort_by=last_limit_up_date','同题材再次排序')
        for path in ('/tracking-rounds?status=all','/strategies','/research/jobs','/data/status','/sources','/topic-hotspots?limit=8'):
            request(path)
    return {'scope':'只读真实HTTP；不运行选股、同步或研究取数；不含浏览器绘制','data_date':latest,'measurements':rows}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base',default='http://127.0.0.1:8788/api/v1');parser.add_argument('--output',required=True)
    args=parser.parse_args();report=benchmark(args.base)
    target=Path(args.output);target.parent.mkdir(parents=True,exist_ok=True);target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))

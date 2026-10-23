#!/usr/bin/env python3
"""Manual offline BOLL event study on an immutable local generation. No DB/API."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
import time

REPOSITORY=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPOSITORY/'backend'))
from app.analytics.market import MarketData
from app.analytics.boll_squeeze_research import aggregate,study_symbol,VERSION,DEFAULTS


def sha(path):
    result=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):result.update(block)
    return result.hexdigest()


def number(value):return '未知' if value is None else f'{value:.2f}%'
def fraction(value):return '未知' if value is None else f'{value*100:.1f}%'


def markdown(report):
    sample=report['sample'];counts=report['squeeze_event_direction_counts'];setup=report['setup_first_direction_counts']
    rows=['# BOLL收口后突破：规则方案与离线事件研究',
      '',f"研究版本 `{VERSION}`；数据截至 {sample['calendar_end']}，本次固定抽样 {report['sampled_instruments']} 只当前已导入个股。",'',
      '## 先看结论边界','',
      '收口描述低波动，方向要等后续价格确认。上轨触碰不自动等于上涨机会，下轨触碰也不自动等于见底。作者建议结合不同维度的信息；20周期、2倍标准差属于可调整默认值。[John Bollinger官方规则](https://www.bollingerbands.com/bollinger-band-rules)',
      '',
      '本轮验证的是下述预先固定的研究定义，不把截图中的成功案例当样本，不因结果挑股票或调阈值。所有收益都是历史价格事件统计，未模拟可成交账户、费用、滑点、涨跌停排队或A股卖空；下破组也展示股票自身后续涨跌，不展示做空利润。',
      '', '## 可调规则卡','',
      '| 项目 | 本次固定定义 | 正式条件或后续研究入口 |','| --- | --- | --- |',
      '| BOLL | 收盘SMA20 ± 2×总体标准差 | 周期、标准差倍数 |',
      '| 收口 | 当日带宽不高于之前125个有效连续市场日带宽的10%分位，近5日曾发生；突破日自身不参与前置收口 | 125日回看、10%分位、近5日 |',
      '| 向上 / 向下 | 收盘首次从轨内越过当期上轨 / 下轨 | 后续可加连续两日出轨确认 |',
      '| 量能 | 当日成交量 / 前20交易日均量 ≥ 1.5 | 倍数与均量周期 |',
      '| 中轨方向 | 中轨较5个交易日前向上 / 向下 | 斜率周期与阈值 |',
      '| MACD过滤 | 上破时柱线>0；下破时柱线<0 | 可另设金叉 / 水上状态 |',
      '| KDJ过滤 | 上破时K>D且K≤80；下破时K<D且K≥20 | K上下限，可结合现有低中高快捷 |',
      '| 事件去重 | 同股所有出轨事件之间至少20个市场交易日，先去重再分组 | 冷却交易日数 |',
      '| 5日回轨标签 | 上破后5日内任一收盘回到当日动态上轨之下；下破对称 | 不叫“必然失败”，可另设跌破事件价确认 |',
      '| 5日严格失败标签 | 上破后同一天收盘严格跌破事件日最低价且低于该日中轨；下破后同一天收盘严格超过事件日最高价且高于中轨 | 固定预定义的对称风险标签，不按收益调参 |',
      '',
      '带宽=(上轨−下轨)/中轨；%b=(收盘−下轨)/(上轨−下轨)。本研究的10%历史分位是自定义放宽条件，**不是作者原版Squeeze**；作者手册以125期最低带宽描述收口，Method IV还包含连续出轨和ADX语境，本轮没有复刻该完整交易法。[作者Method IV原始手册](https://www.bollingerbands.com/_files/ugd/58be43_d09c50b6e8ea4afd9af0523ef94de876.pdf)',
      '', '## 样本与防前视','',
      f"- 从本地当前个股名单 {sample['stock_universe_count']} 只中按 `SHA256(seed + '|' + 完整证券ID)` 排序取前 {sample['requested_sample_size']} 只，seed=`{sample['seed']}`；不按涨幅、案例或结果挑选。抽样ID指纹 `{sample['selected_ids_sha256']}`。",
      f"- 冻结日历 {sample['calendar_start']} → {sample['calendar_end']}，共 {sample['calendar_sessions']} 个已留存市场交易日；{report['instruments_with_valid_quotes']} 只有有效报价，{report['instruments_with_known_squeeze']} 只具有足够连续收口预热。",
      '- 信号只使用事件t收盘及更早报价；带宽分位只用当日之前125个值。统计入点为严格下一市场交易日开盘，出点分别为t+5/10/20的收盘。缺失/重复/零量/无效报价保持未知，不能跳到下一条有效报价。',
      '- 已知公司行动重置指标；未来统计窗口跨已知行动时，该窗口结果保持未知，事件本身仍保留。缺少未披露行动的外部完整性证明，因此“无已知行动”不等于已证明完全无行动；本轮使用原始价，不是分红再投资总收益。',
      '- 复权边界：全部信号、次日开盘和未来收盘均为同一未复权原价口径，不混用前复权/后复权。已知除权除息打断指标预热，未来跨行动的窗口不计算收益；不能把这些可比子样本结果外推成含分红再投资的长期总收益。',
      '- 当前名录存在幸存者偏差，历史ST/退市与当时可得数据版本不足；价格计算没有未来函数，不代表已完成严格point-in-time可交易回测。',
      '- 固定规则同时按年份展示：2022—2023为早期描述组，2024为时间验证组，2025—2026为时间留出组。未根据任何年份结果调参；这些留出表仍是历史离线研究，不冒充真正前瞻实盘。2021预热不足、2026尾部右删失独立保留。',
      '', '## 收口后方向分布','',
      f"按20日冷却抽取的收口观察点，随后10个市场日首次出轨：向上 {setup.get('up',0)} 次、向下 {setup.get('down',0)} 次、未出轨 {setup.get('no_breakout_within_window',0)} 次；未知原因与数量：`{json.dumps({key:value for key,value in setup.items() if key.startswith('unknown_')},ensure_ascii=False)}`。这是收口观察点分母，不能与下方已出轨事件分母混用。",
      f"去重后的全部出轨事件 {report['all_deduplicated_band_cross_events']} 次；前置收口可确认的上破 {counts.get('up',0)} 次、下破 {counts.get('down',0)} 次；收口状态不足以判断的事件 {report['squeeze_unknown_event_count']} 次。",
      '', '## 前置收口：量能和方向过滤对照','',
      '| 方向 / 条件 | 事件数 | 5日均值 | 10日均值 / 中位 | 20日均值 / 中位 | 20日有效 / 未知 | 5日回轨 / 严格失败 | 20日收盘路径回撤中位 |',
      '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    labels={'base':'仅收口+出轨','volume':'再加量能','volume_slope':'量能+中轨方向','volume_slope_macd':'再加MACD方向','volume_slope_kdj':'再加KDJ状态/范围'}
    for group in report['groups']:
        if group['cohort']!='squeeze' or group['year']!='all':continue
        a,b,c=(group['horizons'][str(h)] for h in (5,10,20))
        rows.append(f"| {'上破' if group['direction']=='up' else '下破'} · {labels[group['variant']]} | {a['events']} | {number(a['mean_return_pct'])} | {number(b['mean_return_pct'])} / {number(b['median_return_pct'])} | {number(c['mean_return_pct'])} / {number(c['median_return_pct'])} | {c['valid_forward_events']} / {c['unknown_forward_events']} | {fraction(a['false_breakout_5_fraction'])} / {fraction(a['strict_failure_5_fraction'])} | {number(c['median_close_path_drawdown_pct'])} |")
    rows+=['','MACD/KDJ两行分别是量能+中轨组的子集，样本和年份构成会变化；均值更高或更低都不能单独证明过滤存在收益增益。“回轨”包括收盘略回轨但之后继续走强的情况，不能称为假突破失败率。严格失败要求同一根后续K线同时越过事件反向极值和中轨，比单纯回轨更严格；它也不是已执行交易的胜负率。回撤按次日开盘后的每日收盘路径计算，不猜测盘中高低先后。',
      '', '## 5 / 10 / 20日样本缺口','',
      '| 基线方向 | 未来窗口 | 事件总数 | 有效 / 未知 | 未知原因与数量 |','| --- | ---: | ---: | ---: | --- |']
    for group in report['groups']:
        if group['cohort']!='squeeze' or group['variant']!='base' or group['year']!='all':continue
        for horizon in (5,10,20):
            a=group['horizons'][str(horizon)]
            reasons={'known_corporate_action_in_forward_window':'已知公司行动跨窗','missing_or_invalid_market_session_quote':'缺失或无效市场日报价','right_censored':'右端未满观察窗'}
            details='；'.join(f'{reasons.get(key,key)} {count}' for key,count in a['unknown_reasons'].items()) or '无'
            rows.append(f"| {'上破' if group['direction']=='up' else '下破'} | {horizon}日 | {a['events']} | {a['valid_forward_events']} / {a['unknown_forward_events']} | {details} |")
    rows+=['','每个窗口只统计其自身可比样本，5/10/20日分母不同；未知不补0。5日回轨和严格失败各自使用有完整5日标签的样本，不能拿20日有效数作为其分母。跨行动窗口未知会产生可比子样本选择偏差，故均值不能直接比较为可交易收益。',
      '', '## 按年份留出：仅收口+出轨基线','',
      '| 事件年份 | 方向 | 事件数 | 20日有效 / 未知 | 20日均值 | 20日中位 | 5日内回轨比例 |','| --- | --- | ---: | ---: | ---: | ---: | ---: |']
    for group in report['groups']:
        if group['cohort']!='squeeze' or group['variant']!='base' or group['year']=='all':continue
        a=group['horizons']['20']
        rows.append(f"| {group['year']} | {'上破' if group['direction']=='up' else '下破'} | {a['events']} | {a['valid_forward_events']} / {a['unknown_forward_events']} | {number(a['mean_return_pct'])} | {number(a['median_return_pct'])} | {fraction(a['false_breakout_5_fraction'])} |")
    rows+=['','## 无前置收口的出轨对照','',
      '| 方向 | 事件数 | 20日有效 / 未知 | 20日均值 | 20日中位 | 5日内回轨比例 |','| --- | ---: | ---: | ---: | ---: | ---: |']
    for group in report['groups']:
        if group['cohort']!='other_breakout' or group['variant']!='base' or group['year']!='all':continue
        a=group['horizons']['20']
        rows.append(f"| {'上破' if group['direction']=='up' else '下破'} | {a['events']} | {a['valid_forward_events']} / {a['unknown_forward_events']} | {number(a['mean_return_pct'])} | {number(a['median_return_pct'])} | {fraction(a['false_breakout_5_fraction'])} |")
    rows+=['','对照并未匹配同日市场状态、行业、市值或动量，不能把差异全部归因于BOLL收口。完整各年份、各过滤与5/10/20日聚合在同目录JSON中；私有事件回执不进入公开仓库。',
      '', '## 如何接入现有策略','',
      '正式策略已增加“BOLL收口与方向”条件：当期收口、向上/向下收盘突破、上方/下方回轨。BOLL周期、标准差倍数、历史分位窗口、收口阈值与最近收口范围可调，也可限制中轨方向及带宽扩张。它可放入已有全部/任一条件组，与量能、MACD、KDJ一起使用；配置和价格依据随策略运行冻结。使用方法与准确公式见[BOLL选股条件](../BOLL选股条件.md)。本研究表格仍是原先固定规则的结果，没有因产品参数可调而重算、挑选或调优。',
      '',
      '正式条件与本研究有独立版本及数据门槛：正式信号要求明确的公司行动覆盖证据，严格市场日或完整周/月，不以研究中“没有已知行动”替代完整可比性证明；缺口和预热不足保持未知。正式条件没有默认加入本研究的20日事件冷却、次日确认、未来5日失败标签或未来收益。量能字段“较昨日成交量倍数”比较前一交易日，亦不同于研究中的前20日均量。',
      '',
      '当前热点仍是当前成员代理，不能带入历史PIT回测；不能因为样本均值为正就默认加入自选或自动执行交易。新条件保存不自动创建、执行策略或更新用户已有定义。',
      '',f"复现入口：`scripts/research_boll_squeeze.py --data-root <本地冻结数据根目录> --snapshot-id <行情代际> --sample-size {sample['requested_sample_size']} --seed {sample['seed']} --audit-dir <私有回执目录> --report <聚合报告.md>`。脚本仅打开本地不可变Parquet和参考文件，最多32只一批，不打开业务SQLite或worker活动DuckDB，不联网。",'']
    return '\n'.join(rows)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root',type=Path,required=True)
    parser.add_argument('--snapshot-id',required=True)
    parser.add_argument('--sample-size',type=int,default=300)
    parser.add_argument('--seed',default='boll-squeeze-v1-fixed-before-results')
    parser.add_argument('--audit-dir',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    if not 1<=args.sample_size<=500:parser.error('sample-size must be 1..500')
    market=MarketData(args.data_root,snapshot_id=args.snapshot_id)
    status=market.status();calendar=market.calendar()
    if not status.get('ready') or status.get('snapshot_id')!=args.snapshot_id:raise ValueError('fixed generation unavailable')
    universe=sorted({item['instrument_id'] for item in market.instruments(limit=10000) if item.get('kind')=='stock'})
    selected=sorted(universe,key=lambda symbol:hashlib.sha256((args.seed+'|'+symbol).encode()).hexdigest())[:args.sample_size]
    generation=args.data_root/'published'/args.snapshot_id
    provenance={name:sha(generation/name) for name in ('manifest.json','daily.parquet','corporate-actions.parquet','reference.json')}
    args.audit_dir.mkdir(parents=True,exist_ok=True);args.report.parent.mkdir(parents=True,exist_ok=True)
    started=time.perf_counter();results=[]
    for offset in range(0,len(selected),32):
        batch=selected[offset:offset+32]
        histories=market.history_for_instruments(batch,start=calendar[0],end=calendar[-1])
        for symbol in batch:
            actions=market.actions(symbol,start=calendar[0],end=calendar[-1])
            results.append(study_symbol(symbol,histories.get(symbol,[]),calendar,[action.get('date') for action in actions]))
        print(json.dumps({'completed':len(results),'total':len(selected),'elapsed_seconds':round(time.perf_counter()-started,2)}),flush=True)
    report=aggregate(results)
    report['sample']={'stock_universe_count':len(universe),'requested_sample_size':args.sample_size,'seed':args.seed,
        'selected_ids_sha256':hashlib.sha256('\n'.join(selected).encode()).hexdigest(),
        'calendar_start':calendar[0],'calendar_end':calendar[-1],'calendar_sessions':len(calendar)}
    report['provenance']={'snapshot_id':args.snapshot_id,'file_sha256':provenance,'source':'existing_local_immutable_generation',
        'remote_requests':0,'business_database_opened':False,'worker_duckdb_opened':False}
    report['run']={'generated_at':datetime.now().astimezone().isoformat(),'elapsed_seconds':round(time.perf_counter()-started,3)}
    (args.audit_dir/'boll-squeeze-events.json').write_text(json.dumps({'research_version':VERSION,'selected_instruments':selected,'results':results},ensure_ascii=False,indent=2))
    (args.audit_dir/'boll-squeeze-provenance.json').write_text(json.dumps({'sample':report['sample'],'provenance':report['provenance'],'run':report['run']},ensure_ascii=False,indent=2))
    args.report.write_text(markdown(report))
    args.report.with_suffix('.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({'report':str(args.report),'events':report['all_deduplicated_band_cross_events'],'squeeze':report['squeeze_event_direction_counts'],'seconds':report['run']['elapsed_seconds']},ensure_ascii=False),flush=True)


if __name__=='__main__':main()

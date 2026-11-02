# 近30日涨幅前1000分类分布 v1

版本：`gainers-top1000-distribution-v1`。入口仍是 GET `/api/v1/market/gainers`；不增加取数任务或改变原排行、搜索、排序和分页契约。

## 固定统计样本

新增响应字段 `ranking_distribution`，在表格 `top_n`、`rank_state`、搜索、排序和分页之前，按所选盘面日原始 `rank` 取前1000个可排名证券。未排名证券不进入分母；实际不足1000时，以实际去重证券数为分母。即使表格当前查看前100名、未排名或搜索结果，统计样本仍固定不变。

价格与复权继续使用现有T/T-30精确市场交易日端点，不改收益计算。分类汇总不缓存至行情代际：当前行情日每次单次读取本地有效分类与人工归属；尚未建入题材库的、日期可核实的原来源分类可保留，已建库分类的排除/归档不得被来源回填。旧盘面日仅用该日已留存成员，不用当前成员或人工修改回填历史。Provider成员采集日必须可核实且不晚于观察日；当前人工题材以已生效业务日证明，不冒充Provider采集日。

每次最多聚合1000只股票，不对每只股票单独取数，不向前端发送全市场分类列表。AI只读链路可注入固定 `business_read_connection`，使用只读SQLite连接；默认API行为不变。API不打开唯一worker正在写入的DuckDB。

## 响应形状

```text
ranking_distribution: {
 version, date, sample_limit:1000, sample_count,
 sample_scope:'top_1000_before_filters', rank_basis:'return_30d',
 classification_state:'empty'|'current_business_partial'|'dated_partial'|'unavailable',
 classification_scope, classification_snapshot_id,
 topics:{definition,top,classified_count,attribute_only_count,unknown_count,total_categories,overlap,overlap_possible:true},
 industries:{top,classified_count,attribute_only_count:0,unknown_count,total_categories,overlap,overlap_possible:true},
 excluded_attributes:{reason:'trading_eligibility',unique_stock_count,total_assignments,mapping,items}
}
top[]: {id,topic_id,name,count,share_pct,data_kind,source,source_code,
        source_observed_date,classification_level}
```

- 题材定义为“经济/政策/产业题材分布，不含已审定交易资格标签”。目前仅对真实核验过的 `source=fuyao, kind=concept` 三个完全相同名称“融资融券”“沪股通”“深股通”在此统计维度排除，不以模糊词匹配。手工题材同名、其它未审来源同名、名称仅部分包含这些词均保留；国企改革、机器人等研究主题保留。行业只按已明确 `kind=industry` 的归属，不受此映射影响，不能根据名称猜行业。
- 统计键保留来源、kind及真实分类身份，跨来源同名分类不合并。各组最多返回前5类，先按家数降序，再以名称/身份稳定排序。
- 同一证券同一分类只计一次，不同分类可重复。`share_pct = count / sample_count × 100`；多题材、多来源或多层行业可能重叠，合计可超过100%，不强行凑成100%。
- `classified_count` 是该组至少有一个已核实研究分类的证券并集数；`attribute_only_count` 是仅有被排除交易资格标签、没有研究题材的证券数；`unknown_count = sample_count - classified_count - attribute_only_count`。三个集合不重叠。缺分类不是“没有题材”；未知单列，不补标签。
- `excluded_attributes.mapping` 留证本次审定来源/kind/完全相同名称映射；`items` 留证真实出现的来源分类身份、家数和以实际样本为分母的占比；`unique_stock_count` 是有任一被排除属性的证券并集，`total_assignments` 是去重股票-属性归属总数，可能大于并集。不能将该并集与已知/未知再相加；它可与有研究题材证券重叠。原列表标签和来源数据不因统计维度排除而删除。
- 不展示按 `100%-sum(top.share_pct)` 推出来的“其他”。若将来增加其他项，必须另外核对非Top5类别证券的去重并集，不能用比例相减。
- 最新业务日分类只描述这批证券当前可核实的观察标签，不表示历史完整归属、30日上涨原因或资金净流向。成员采集日期与股票行情日分别留证。

样本内返回行的标签同步到汇总使用的同次分类读取，复制行而不修改价格排行缓存；样本外原列表行为不变。

## UI

在原排行面板上部放一个紧凑摘要，默认题材Top5，同行两个按钮切换行业Top5；以条形、家数和占实际样本百分比展示。保留实际样本数、所选日与标签未知家数；存在仅资格标签样本时简短单列，并说明“未计入融资融券、沪深股通等交易资格标签”。分类来源/层级有区别时保留身份或提示；不堆几十个题材标签，也不把分类条形当成员筛选或资金流向。AI读取该字段时沿用题材维度定义，不能称作原始全部标签的排行。

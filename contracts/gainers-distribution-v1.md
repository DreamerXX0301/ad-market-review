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

## AI 查询固定样本内的分类股票

继续使用 `read_market_detail(date, section, query)`。新轮次的工具枚举开放两个分区，旧冻结请求的原枚举不扩展：

- `section=gainers_related`，例如 `query=电池`。在已留存的分类名称中做大小写不敏感的子串匹配，查询完整固定前1000中命中这些分类的股票并取并集。同一股票只返回一次，保留它命中的分类身份。范围不限于分布 Top5、首屏或裁切后的股票行。关键词只匹配真实分类名称，不匹配股票简称、分类代码或自行推断产业链关系。
- `section=gainers_topic`，`query` 传已核验的完整 `topic_id`。去掉首尾空白后按标识等值匹配，名称、子概念和部分 ID 均不替代。两个分区都要求非空 query。

两种分区沿用原榜单的观察日、T/T-30 收益和去重样本，不产生行情采集或题材写入。当前日使用当日有效分类及人工归属，历史日只用当日可核验的存档分类。即使分类在样本内没有成员，也读取其留存定义以区分已核验零交集和无资料。

未指定日期且无明确承接日期时，两个新分区可用 `date=latest`，从同一固定发布代际读取最新已发布完成日，回答标明实际日期。明确问“今天”使用 `date=today`，今日没有完成日资料时保持缺口，不回退旧日。明确历史日期使用原日期。

工具证据中 `classification_lookup` 保留 `mode`、`query`、`sample_count`、`state`、`observed_count`、`matched_count`、`coverage` 和有界 `categories`。其中 `observed_count` 是截取前去重匹配数。只有已解析的匹配分类全部具有可核验完整成员时，`matched_count` 才有确认总数，零交集标记 `confirmed_zero`。确认范围仅为所选日留存的匹配分类和前1000样本。未解析分类、成员不完整且无已观察命中为 `unknown`。没有可排名样本为 `empty_sample`，不可据此断言没有相关股票。有已观察命中时为 `matched`，覆盖不足时确认总数仍为空。

`categories` 记录真实分类名称、标识、来源、成员观察日期和可确认/观察到的家数。`stocks` 保留原始排名与近30日收益、行情日期与来源及实际命中分类。两张表均记录 `observed_count`、`returned_count`、`truncated`，截取不改变计数。原 `section=gainers` 的分布/模糊筛选和公开市场 GET 接口不变。新分区每次仍计一次现有实质读取预算。

## UI

在原排行面板上部放一个紧凑摘要，默认题材Top5，同行两个按钮切换行业Top5；以条形、家数和占实际样本百分比展示。保留实际样本数、所选日与标签未知家数；存在仅资格标签样本时简短单列，并说明“未计入融资融券、沪深股通等交易资格标签”。分类来源/层级有区别时保留身份或提示；不堆几十个题材标签，也不把分类条形当成员筛选或资金流向。AI读取该字段时沿用题材维度定义，不能称作原始全部标签的排行。

# 成交额TOP10近7日活跃标记

`GET /overview` 的既有 `top_amount` 集合和金额排序保持不变；金额相同保留原始面板顺序。生产历史投影只读同一行情代际的 `instrument_id/date/amount`，不执行七次全市场指标计算，不联网、不创建任务或写业务数据。

每行新增 `active_top10_7d={count,dates,ranks:[{date,rank,amount}],observed_days,expected_days:7,complete,state,label,reason,window_start,window_end,scope}`。窗口是包含观察日的最近7个市场交易日，两端包含，跳过自然休市日。`count` 是本地同口径成交额TOP10榜当前已观察的上榜次数；缺日、少于7个日历交易日、不足10项、金额待核验或重复证券时 `complete=false`，`state=observed_partial`、文案“已观察N次 · 样本不完整”。没有可确认榜事实则count为null、state=unknown。完整窗口state=complete。样本中重复2次及以上标为“持续活跃”，只有1次不推断持续活跃。

不完整榜单的排名出现次数不叫“下界”或“至少N次”：补齐一个未知成交额的竞争证券，可以把原第10名挤到第11名，历史上榜次数可能下降。分项reason与scope明确补齐可能改变名次及次数，不能将样本重复当完整目录的保守保证。

顶层 `top_amount_activity` 含窗口、7日核验日期、缺失/未完整日期、说明、算法版本及 `directions`。`complete`仅指最近7个**本地榜日期**完成核验，不是历史全市场证券覆盖率、机构资金流或未来表现保证。缓存只存最多32项榜事实，键含行情实例、快照、观察日、日期窗口与算法版本，另核对实例引用身份防止对象ID复用串缓存；常规MarketData用弱引用，不让小榜单缓存持有大行情对象。题材归属不缓存，deepcopy只复制榜事实。

`directions={state,items,scope,reason,unmapped_instrument_ids?}`，每项含 `topic_id/name/kind/observed_date/stock_count/instrument_ids/items/members_complete/source_snapshot_id/membership_snapshot_day/scope`；明细含股票、名称、已观察次数、上榜日期、7日是否完整；次数是否完整随明细complete区分。只把**本日TOP10中近7日重复上榜的个股**按观察日可核验成员快照归组，不声称这些股票在过去各上榜日也属于该题材。成员日期未知或晚于观察日、成员存档不可用或身份冲突时不强行分组。未覆盖股票和部分成员保留partial/unknown。这是当前可核验题材关联，不是涨停原因、主线确认或净流入。

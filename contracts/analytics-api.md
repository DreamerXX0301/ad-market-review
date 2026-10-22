# Analytics API v1

根路径 `/api/v1`。成功直接返回业务对象；列表通常为 `{items,total}`，错误为 HTTP 4xx/5xx 与 `{detail}`。数值未知为 null，条件为 true/false/null；百分比 `3.2` 表示 `3.2%`。所有 GET 只读本地已发布数据，不触发来源取数。工作区写接口见 [Workspace API](workspace-api.md)。

## 日期、身份与覆盖

- `date`/`as_of` 必须属于已发布范围内的完成市场交易日；未来、无效格式或越界日期拒绝。盘面日期不会修改业务时钟。
- 股票/指数使用完整证券 ID，例如 `000001.SH` 与 `000001.SZ`；板块使用已核验目录的完整来源 `topic_code`。
- `data_coverage` 含 `label/start/end/basis/notice/selected_date_available/partial/available_dates`。basis 是可读口径；日期列表仅表示明确留存日，不保证中间连续完整。
- 主行情、来源涨跌停池、资金、异常名单和成员快照分别披露范围。缺来源不填零，不以抓取时间冒充数据日。

`GET /data/status` 的每个 `datasets[]` 新增 `coverage_progress`：`label/observed_count/expected_count/percent/unit/dimension/basis/state/reason`。`row_count` 仍表示已留存物理记录，与完整度分子分开；只有同一口径分母明确、分子不超过分母且分母大于零时 percent 才是 0–100，其他为 null。

`coverage_progress.coverage_status/coverage_label` 区分 `covered`（当前范围已覆盖）、`partial`（当前范围有缺口）、`unknown`（覆盖待核验）、`on_demand`（按需计算）；`scope_complete` 对前两者为 true/false，其余为 null。判断完整使用原始整数分子与分母相等，不能由显示百分比四舍五入推定。上述状态和标签同时复制在 dataset 顶层，供覆盖卡展示。

`status` 兼容保留不可变快照原始发布质量，同时通过 `snapshot_quality/snapshot_quality_label/snapshot_quality_note` 明确其来源和范围。某一当前维度100%可以与发布时整体 `partial` 并存：例如当前目录成员组完整不证明历史每日成员归属完整。不得因此修改快照质量或任务状态，也不能把按需指标计算当作下载未完成；任务终态单独读取任务接口。

日历比例限冻结窗口已核验来源交易日（早期来源推导不冒充完整官方历史日历）；证券名录比例为本次名录中的上市日期字段完整度；题材比例为同代际目录中来源明确完整的成员组；盘面比例限当日涨停、跌停、炸板三来源池，不包含异动及历史事件全集。逐股日行情缺上市/退市日期或应有总量、公司行动缺来源总事件数时保持未知；按需计算的日/周/月技术指标不将输入行情条数冒充已可用指标数。统计只读快照元信息、目录和来源回执，不扫描全历史行情、不取数。

`GET /data/status.coverage_check` 只读当前代际的小型本地核验报告摘要。股票上市日期分母剔除指数。显式核验后，日行情可返回“已知上市范围报价出现率”：分子为有效期内已留存市场日的唯一有效报价，分母为已知上市/退市区间与已留存日历形成的证券交易日机会；未知身份不进入分母。无报价机会可能是真实停牌、来源缺报或身份变化，出现率不能等同全市场数据完整率，也不能为了100%补造价格。

`GET /data/coverage/check` 读取计划与完整核验报告（逐股无报价日列表最多20,000个日期，截断明确标注）；GET不建任务、不写表、不扫描全量日K。`POST /data/coverage/check {plan_id?,request_key?}` 创建唯一worker执行的 `source_check/operation=local_coverage_check`，校验当前文件SHA/结构、有效期报价日期和公司行动已提供字段，不访问凭据或联网，不修改任何发布快照的内容/quality。报告绑定行情代际、发布heads、文件stat和来源SHA；输入变化后不复用。 公司行动报告分别提供 `window_rows/window_ready/window_schema_verified/window_invalid_rows` 与 `file_rows/file_ready/file_invalid_rows`；`ready/invalid_rows`兼容全文件状态。特殊负送转不擅改原值，保留 `file_special_rows/window_special_rows/outside_window_special_rows`，`special_items/invalid_items`合计最多20条含股票、日期、范围与原因；窗口外特殊行动不描述为当前窗口损坏。公司行动的本地字段核验与来源全市场应有总量分别披露，缺外部总量始终不计算虚构完整率。

核验报告的 `repair_plan` 明确已有 `/sync/market-supplement` 任务的联网范围：年度不完整或缺失来源池、当前日封板明细及近期行业资金，不重复整库板块K线。手动提交须携带 `coverage_plan_id` 和计划中的year_history/sector_history/recent_days；创建与执行前检查冻结代际，过期或改变范围拒绝。来源空响应和未提供上市日期、无报价日不会被此任务填零或假称修复。

日K与分析行情共用同一 `report.daily.gap_diagnosis`，不创建两份缺口任务。它按连续市场交易日归并 `stocks[].episodes[]`，每段有固定 `gap_id`、起止日/天数、前后实际报价日、形态、`reason/impact/next_action` 和本地原始包对照；孤立、连续及至少5个市场日的较长连续形态均不证明停牌。摘要统计完整范围；明细最多20,000个日期、10,000段，`details_complete=false`明确截断。`GET /data/status`只带统计；完整核验GET才带股票及区间，读取不扫描Parquet或取数。

显式来源核验保存的回执仅在 `snapshot_id/source_signature/diagnosis_id` 全部匹配时，以派生视图附到区间 `source_verification`，携带来源、时间、结果、返回/未返回/差异日期和回执ID/SHA；原本地报告不改写。`source_no_quote`只说明该来源本次无报价，`confirmed_suspension`仍为null；旧回执标 `source_verification_stale`，不冒充当前结论。

上市名录独立披露 `catalog_count/listed_count/pending_listing_count/unknown_count`。已上市日期覆盖分母为已上市与身份未知之和；有可溯源证据且当前代际无真实成交报价的待上市证券剔除，未知仍占分母。逐股报价日聚合一次后复用上市分类；人工状态revision进入核验计划签名，改动即作废旧报告与来源对照，不在GET补查行情。

## 入口与检索

| 接口 | 返回与边界 |
| --- | --- |
| `GET /bootstrap` | 应用、当前业务日和已发布数据元信息 |
| `GET /overview?date=` 或 `/market/overview?date=` | 四指数、广度、总成交额、成交额排行、封板观察等 |
| `GET /instruments?query=&limit=` | 原股票/指数检索；研究选择器继续使用此接口 |
| `GET /search?query=&limit=` | 股票、指数、行业、概念统一检索；空白返回空，limit 为 1–100 |
| `GET /instruments/{id}?as_of=` | 标的身份、所选日行情、可用题材/风险标签及缺口 |

统一检索按名称/代码匹配，不推断拼音。去空格后 query 最多 100 字，原始最多 512 字；先精确匹配，再按类型平衡截取。返回 `items/total/matched_by_kind/scope/current_catalog/historical_membership`。股票/指数有 `instrument_id/kind/target_type`；板块另有 `topic_code/topic_id/sector_kind`，`target_type=sector`。当前目录只说明身份，不证明历史目录或成员。

总览指数的自身涨跌幅与 `amount_change` 分开：金额差按 T 与前一精确市场交易日金额（元）相减；缺前期或非法金额为 null。沪深总成交额不能将四个指数相加得到。

## 盘面页面

`GET /market/{kind}?date=` 支持 `emotion/sectors/ladder/gainers/abnormal/flow` 等页面分支；`limit/query/page/page_size/sort_by/sort_order/top_n` 仅在相应分支生效，以路由校验为准。

| 分支 | 主要口径 |
| --- | --- |
| emotion | 广度历史及 `sentiment` 的市场赚钱效应、昨日涨停样本今日价格反馈；无神秘总分 |
| sectors | 指定日来源指数涨幅/成交额；没有真实指数时可明确回退存档成员等权样本 |
| ladder | 来源连板梯队、首末封/开板等可用字段；反包、失败、20cm 分开，未核验核心不造龙头 |
| gainers | 精确 T/T−30 市场交易日端点、年内涨停证据、技术观察和风险提示 |
| abnormal | 仅名单来源观察日明确匹配的留存数据，不用当前名单回填历史 |
| flow | 已核验行业日净流入、跨日趋势；板块成交额与资金净流入分开 |

`sectors` 行包含 `quote_available/quote_source/quote_basis/members_available`。`quote_basis` 为 `provider_index` 或 `archived_member_sample`；没有成员时成员数、涨跌停家数为 null，但真实指数行情继续显示。顶层 `quote_coverage` 与 `membership_coverage` 分别返回，当前目录数量不称历史目录完整。

`GET /market/topic-members?date=&topic_id=`，或使用明确 `source_code/kind/source_name`，只读所选日可用的历史成员档案；找不到时明确不可用。不能按同名当前成员补档。

`GET /market/mainlines?date=&known_at_ms?` 与 `GET /market/mainlines/history?date=&instrument_id=&limit=&offset=` 读取独立的逐日手工主线审计；不改变 `ladder` 的行业、概念和原始来源原因。它们只读SQLite，date是合法ISO审计日期，即使当前行情目录已滚动也可读旧记录；无记录为空，不由当前概念补全。列表total是有修订的证券数，含撤销，不是涨停家数；前端仅与当前确认池按完整ID关联后去重展示，池外记录单列。写入、更正、撤销、来源转述/用户判断/待核验/冲突以及真实记录时间契约见[Workspace API：当日主线](workspace-api.md#当日主线归因手工证据-v01)。

`gainers` 的检索/排序对完整候选集合执行后再分页，null 升降序都最后；年内次数下界不参与精确次数排序。`flow` 的近若干日是跨日趋势，不是全天盘中资金时序，也不推断机构真实决策。

`gainers` 同时返回 `last_limit_up_date/last_limit_up_complete/last_limit_up_coverage/sessions_since_limit_up`，含义与成分股、个股统计一致；支持按 `last_limit_up_date` 对全部榜内候选排序后分页。它使用观察日之前的逐日来源涨停池与该证券当日唯一原始收盘批量核验，每批最多256只、仅从最早留存事件源日读取，不逐股调用详情或据涨幅推算涨停。不完整年内统计只在已确认次数大于0时展示下界；已确认0且仍有缺口展示待核验，完整核验的0仍显示0次。没有可核验封板分钟时此列只展示日期。

成员、个股摘要与涨幅榜的年内已确认次数下界，包含逐股同日原始收盘核验通过的 reference 历史池、年度池及公开补充池正例，跨源按日期去重，仅计观察日之前本年事件。补充正例不能把来源池缺席变为否定证据，也不能将不完整年度总数变成精确次数。

`sentiment.market/short_term` 含状态、范围、指标、样本与缺口。`sample_count` 为有效可比数，`observed_count` 为观察候选数，`unknown_count` 为差额；收益样本覆盖不等于来源全集完整。晋级率证据不全时为 null，已知价格反馈不冒充接力晋级结论。

### 情绪三轴上下文

`sentiment.model=dated-three-axis-context-v1`，原全体昨日涨停队列口径保留在 `market/short_term`，并返回 `legacy_cohort_model`。新增指数环境、制度分组和证据摘要，不合成统一温度或正式六阶段。

- `index_environment.indices[]` 按四个指数分别返回 `instrument_id/name/date/state/state_label/scope`、`sample_count/observed_count/unknown_count/coverage_pct`、窗口边界、缺失/重复日期、`window_fingerprint` 与同形 `metrics[]`。指标键是 `index_daily_return_pct/index_20d_return_pct/index_ma20_distance_pct/index_20d_volatility_pct`。后3项必须有T及T−20之间21个严格市场交易日唯一有效收盘，不能用最近21根替代；MA20按最后20个收盘，波动为20个日对数收益的样本标准差×100、ddof=1、未年化。当前日缺失不退回更早报价；来源冲突、无效日历或预热不足保留null。收益和相对MA可为负，波动不按0—100比例封顶。
- `short_term.metrics` 增加 `positive_ratio_pct`，分母为收益有效队列，不称完整队列正反馈率。`short_term.groups[]` 按P日有据身份冻结，含 `id/label/state/state_label/scope`、同形指标、`sample_count/observed_count/unknown_count/coverage_pct`、`candidate_count/confirmed_count/unverified_count`、`unallocated_candidate_count`、`cohort_source_complete/identity_complete/current_identity_unknown_count`、`promoted_count/failed_count/promotion_unknown_count`、`cohort_fingerprint`、`promotion_bounds_pct/positive_ratio_bounds_pct/median_bounds_pct` 和 `promotion_bounds_reason`。
- 分组ID为 `ordinary_10/twenty_20/thirty_30/st/listing/unknown`，默认 `default_group_id=ordinary_10`。普通组必须有P日明确非ST证据及上市阶段排除，不能从当前名称或涨幅猜测。T日身份不明或已变制度时，组内T日收益/晋级保留未知；全体队列的独立价格观察仍保留。ST单列但不假定统一涨跌幅；上市前五个留存市场日作保守隔离，不声称各市场新股例外完全相同。存在P日未分组候选时，不能证明普通组完整分母。
- `promotion_bounds_pct={lower,upper}` 的单位是百分数，仅昨日全集和该组分母可证明时返回 `[A/N,(A+U)/N]×100`；未知不能取中点。U=0才有精确 `promotion_rate_pct`。缺完整分母返回null并解释。正反馈区间把收益未知分别按全负/全正计，中位数区间把未知放在排序两端；中位数某端无界时为null，不能当0。各组仅分配已确认P日成员，未确认候选另列 `unallocated_candidate_count`，不能把分组相加当原始来源全集。
- `provenance` 返回 `model/rule_version/trade_date/mode/label/input_snapshot_ids/input_fingerprint/computed_at`、`source_records[]`、`market_collected_at/first_known_at/known_time_complete/notice`。每个来源记录含交易日、池、来源路径、原有 `fetched_at/source_published_at/first_known_at/source_sha256` 及规范化内容指纹。原始SHA缺失时不拿派生指纹冒充原始SHA；抓取时间不重命名为首次可知。当前mode固定 `retained_retrospective`，缺逐日首知档案不声称历史当时可用。`computed_at`是本次真实计算时间，重复请求可不同；其余指纹只依赖对应日期留存输入。

六阶段研究纯函数见 `app.services.sentiment_stage_research`，未接入HTTP情绪阶段、策略筛选或自动任务。`observation_from_group`只适配已有证据；`evaluate_stage_candidate`输出草案版本、参数指纹、逐谓词真/假/未知、候选与未知候选、历史分位上下界；`advance_stage_state/replay_stage_candidates`处理严格相邻确认、滞回和缺日中断。默认普通10%队列至少10只、收益可比率至少80%，缺证据不补值；晋级30%—50%跨过40%时高热谓词未知。阶段仅为 `offline_research_only`、`validated=false` 的待验草案，来源集中补采只能按当前留存回看。`as_known`模式要求每个交易日自己的带时区截止时间及完整首次可知证明，晚到材料不能进入早期时点。首次确认日在第2个满足日，结果不回填第1日；未知时只保留有日期的历史确认，不把旧阶段延续成当前。

### 手动留存复盘上下文

- `POST /market/sentiment-snapshots` 仅接受 `{date,expected_input_fingerprint}`；指纹必须与页面当前64位小写十六进制值一致，拒绝客户端payload、模型、时间和其他字段。服务器在SQLite写事务外从当前不可变发布源重算上下文，输入已变化时409且 `detail.current_input_fingerprint` 返回当前指纹，不能静默保存另一份证据。仅手动调用，不随GET浏览、日期切换、任务或定时自动创建。
- 成功200返回 `snapshot_id/date/recorded_at_ms/record_mode/model/rule_version/input_fingerprint/content_sha256/payload/created/reused/notice`。`payload`为服务器计算的完整sentiment，`record_mode=manual_retained_capture`；`recorded_at_ms`是本次实际留存时间，不代表交易日T已知。payload中的 `provenance.mode` 仍为 `retained_retrospective`，来源的 `first_known_at`、`fetched_at`不改写。`content_sha256`为规范化UTF-8 JSON内容的SHA256。
- 相同 `date+rule_version+input_fingerprint` 幂等返回原记录（`created=false,reused=true`），保持原payload、内容SHA与原留存时间。新输入/规则追加新记录，不覆盖旧记录。事务内校验实际时间不早于计算前检查时刻及全表已留存最大时刻，回退409且不伪造递增毫秒。018迁移建立独立append-only表、唯一键、更新/删除/替换保护和跨日期时钟保护，不填入任何种子记录。
- `GET /market/sentiment-snapshots?date=&known_at_ms=&limit=20&offset=0`：date与known_at_ms可省略，limit为1—100，offset为0—2147483647。按实际留存时间 `recorded_at_ms<=known_at_ms` 筛选，时间降序、ID降序稳定分页；返回 `date/known_at_ms/items/total/limit/offset/notice`。items是上述元数据摘要，不含payload。
- `GET /market/sentiment-snapshots/{snapshot_id}`：返回上述元数据与原始payload，内容SHA不符409；找不到404。两种GET只读SQLite，不重算当前行情、不联网、不回填旧快照。它们证明某份复盘内容在留存时点已被记录，不证明该内容在历史T可用、来源当日首次发布或预测得到验证。

## 个股/指数图表

`GET /instruments/{id}/tracking-baselines?as_of=` 只读该个股已保存的跟踪事件基准，按观察日排除更晚入池和退出信息，最多返回最近100个轮次并标明截断。默认最近仍在跟踪的轮次，否则最近历史轮次；返回原策略、轮次、冻结入池日期、原始收盘价与价格口径。全局搜索打开个股时也可选择这些基准，不创建跟踪或重算选股；从特定跟踪/自选来源打开仍优先原轮次，连续切股不沿用上一只基准。

每轮另含 `historical_event_checks`，仅投影本轮冻结 `entry_basis.entry_checks` 内的历史事件，不读取候选或行情、不重新求值。元素为 `{type:"history_event",result,reference_fields,selected_event:{date,open,high,low,close,offset,price_basis},event_checks:{result:true},history_event:{match,where?,price_path?:{reclaim:{event_field}}},evidence:{as_of,price_basis}}`。仅纳入事件条件明确满足、日期不晚于冻结入池观察日、有效有限正OHLC且原始未复权口径明确的已绑定事件；不要求其独立今日/价格过程最终result为true，保留原true/false/null，不能据此冒充新信号。where只为有界条件标签；无法按白名单读取时省略，原事件证据不推断。缺失、未知及未绑定证据返回空数组。扫描遵守既有100条件节点上限，异常超限时 `historical_event_checks_truncated=true` 并含 `historical_event_checks_reason`，不声称摘要完整。`reference_fields` 只从冻结then的all/any树抽取right_event_field，并合并price_path.reclaim.event_field，以close/high/low/open固定顺序去重；没有引用字段才默认 `["close"]`。事件日图标与每个参照字段的横线使用冻结事件原始OHLC，不能用入池收盘代替，也不能将±百分比比较阈值冒充原事件价格；百分比只在规则证据中解释。展示仍须遵守图表复权口径与锚定日的可比限制。

`GET /instruments/{id}/chart?period=D|W|M&as_of=&price_basis=none|forward&indicator_continuity=quotes|market_sessions`。

- 返回 `instrument/period/as_of/bars/snapshot_id/quality/warnings`、价格/量额单位、实际与请求价格口径、`adjustment/indicator_gaps/quote_gaps/waves` 及涨跌停事件覆盖。
- bar 含 date/OHLCV/amount、MA5/10/20/30/60/250、MACD DIF/DEA/柱、KDJ K/D/J；预热不足为 null。前端不另算一套指标。
- 周/月从日线聚合，形成中和不完整周期带状态；策略信号只读已闭合可比周期，图表可显示形成中柱。缺交易日报价不补 K 线。
- 默认 `none` 原价；显式 forward 仅对已核验事件做观察日锚定的本地仿射变换。证据不足回退原价并说明；不是完整分红/股数收益账户。
- 默认 `quotes` 可按实际日报价连续显示指标；`market_sessions` 严格按市场日中断。两者不改变策略、跟踪或回测口径。
- `waves` 仅为延迟确认的价格转折/骨架候选，点满足确认时间不晚于 as_of；不保证确定艾略特浪，不作自动交易信号。
- 上证 `advancers` 及广度范围按真实样本说明，不将有限可比样本称全市场完整。

收盘涨跌停通过 `limit_events` 与逐方向 `limit_event_coverage` 返回；日 bar 的状态和证据区分 confirmed/source_absent/unknown/conflict。正事件必须通过来源身份、日期及原始收盘核验，不按涨幅 9.9% 猜，不用炸板触价当收盘涨停。空池、隔离、错价不能证明 false。周/月只汇总确切日事件，不称整周/整月涨停；指数不适用个股标记。显式前复权只改变图上锚点，不改变原始事件日期和核验价格。

## 统一个股统计

个股图表支持独立的个人水平线：`GET/POST /instruments/{id}/annotations`，`PATCH/DELETE /instruments/{id}/annotations/{annotation_id}`。价格、名称与颜色写入业务库，不修改来源K线、指标、策略或跟踪状态。只接受已留存个股，每只最多64条；未复权水平线跨日/周/月共用，前复权水平线固定记录锚定日。切换价格口径或锚定日时隐藏不相符的线，并在管理列表保留说明，不能直接把原价水平线画到复权价格轴上。

`GET /instruments/{id}/summary?as_of=` 仅适用于已留存股票，不用于指数/板块。返回身份、`board/board_label/as_of/data_date/quote_date/snapshot_id/quote_available/close` 及：

| 字段 | 含义 |
| --- | --- |
| `change_pct` | 观察日收盘相对精确前一市场交易日收盘的涨幅，与未复权日K的严格日比较算法相同；无可比价格或公司行动证据时为 null |
| `change_pct_date` / `change_pct_previous_date` | 本次涨幅对应的观察日 / 前一市场交易日；前一交易日未留存时后者为 null |
| `return_ytd_pct` | 上年末已核验最后交易日为基准的年内收益；不能用年初首根代替 |
| `change_30d_pct` / `return_30d_pct` | 精确 T/T−30 市场交易日收益，不能改成最近 31 条报价 |
| `return_mtd_pct` | 以上月末为基准的本月收益，与 30 日收益不同 |
| `limit_up_ytd` / `limit_up_ytd_observed` | 完整年内次数 / 已确认下界；不足时主值 null |
| `last_limit_up_date` / `sessions_since_limit_up` | 最近已确认事件日期及交易日距离；当日距离为 0 |
| `last_limit_up_complete` | 事件后缀证据是否足以确认确为最近；不是历史从未涨停声明 |

当日涨幅另有 `change_pct_reason`，缺观察日报价、前一市场交易日报价、公司行动覆盖，或观察日存在未复权公司行动时保持未知；不改用更早的可见报价，不随图表周/月切换改变为周期涨幅。其余各组另有 reason/coverage；收益需端点与可比公司行动证据，缺当天报价为未知。次数只统计不晚于观察日的原始收盘核验事件。兼容别名 `annual_return_pct/annual_limit_up_count/latest_limit_up_date` 等不改变语义。成员与个股详情复用同一统计服务，无 N+1 来源请求。

## 板块 K 线与成员

- `GET /market/sector-chart?topic_code=&date=&period=D|W|M` 返回通用 chart shape 加 `available/task/unavailable_reason`；未缓存时 bars 为空，不联网。
- `POST /market/sector-chart/jobs {topic_code,date?,request_key?}` 手动加载单板块；返回标准任务或 `{cache_hit:true}`，随后重读 GET。只加载该来源代码，不扫描全库。
- 历史缓存须先复验原始全段与 SHA，再按目标日截断重算聚合和指标；不得从未来已聚合结果切片。板块是原始指数点位，不做个股复权/涨跌停标记。
- `GET /sectors/{topic_code}/members?as_of=&membership_scope=latest|historical` 无需跟踪轮次。latest 仅用当前业务日报价，显示真实成员采集日；historical 严格查历史留存。无档保持未知，不按名称猜成员。
- 成员返回 `membership_as_of/quote_as_of/members_complete`、board 和上述收益/事件字段；`stock_entry_baseline=null`。展开成员不会创建个股跟踪，也不会把板块入池点位套给股票。

精确字段与校验入口见[平台路由](../backend/app/routers/platform.py)、[工作区路由](../backend/app/routers/workspace.py)及[市场服务](../backend/app/services/market_service.py)。

## 报价缺口的显式来源核验

`GET /data/quote-gap-source` 只读当前本地核验计划与已保存的小摘要，返回 `available/plan_id/diagnosis_id/snapshot_id/stock_count/missing_sessions/is_current/stale/evidence`。`evidence` 包括计划与已核验股票数、失败数、逐缺口核验日数、来源未返回报价数、发现可补实际报价数、重叠报价差异数、时间、任务ID；不查询远端、不扫描全部日K、不修改快照质量。

`POST /data/quote-gap-source {plan_id,request_key?,instrument_ids?}` 显式创建 `source_check / quote_gap_source_check` 任务。省略股票范围则核验当前报告全部缺口股票，提供时只能选择该报告中的股票。计划冻结行情代际、身份版本、本地诊断ID和凭据版本；变更后旧任务不继续提交。请求只覆盖含缺口的短日期窗口，保留上下文报价作对照，严格验证股票、原始日K口径、OHLCV和量额。逐股原始响应与SHA验收后可幂等恢复，暂停/取消及旧租约阻止提交；失败保持待核验。

完整本地核验视图在同一 `source_signature/snapshot_id/diagnosis_id` 下将已保存证据附到每段 `source_verification`，不改写原本地核验文件。状态区分 `source_no_quote/local_missing/partial/failed`，附本次未返回与有效返回日期、响应证据与下一步。`source_no_quote` 只说明本次来源未返回该日报价，不证明停牌；`local_missing` 只表明有可进入单独发布核验的实际报价。此操作不修改当前不可变Parquet、不填造停牌K线，也不把出现率伪报为100%。

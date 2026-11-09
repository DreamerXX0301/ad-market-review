# Analytics API v1

炸板结构、题材分布和昨日炸板反馈的追加只读字段见[炸板研究契约](broken-board-analysis.md)。

根路径 `/api/v1`。成功直接返回业务对象；列表通常为 `{items,total}`，错误为 HTTP 4xx/5xx 与 `{detail}`。数值未知为 null，条件为 true/false/null；百分比 `3.2` 表示 `3.2%`。所有 GET 只读本地已发布数据，不触发来源取数。工作区写接口见 [Workspace API](workspace-api.md)。

## 日期、身份与覆盖

`GET /data/status` 的 `summary` 与 `/bootstrap` 的 `data_summary` 保留 `storage_bytes`，含义为本实例 `published` 中已计量普通文件的逻辑字节，新增 `storage_accounting` 明细。`scope=active_published_root`；`logical_bytes/allocated_bytes/file_count/complete` 与六个 `categories`（`current_market/old_market/shared_market/supplement/backtests/metadata_other`）使用同一遍只读文件遍历，分类逻辑字节与文件数严格加总，`storage_bytes=logical_bytes`。当前行情按本实例SQLite主行情head识别，其他已有行情代际单列，旧代际不表示可删除。`shared_market` 单独计量 `market-objects` 的跨版本共享对象，一个对象只按文件计量一次；代际自身的清单与独有文件仍计入 current/old。前端兼容旧API的五类构成，但不能遗漏共享对象后声称总量完整。统计排除staging、符号链接及特殊文件；扫描失败时 `complete=false`，数值只是已计量小计。`allocated_bytes` 为可取得的每文件 `st_blocks*512` 之和，未知为null，不去重硬链接或共享块，不等于独占占用或可释放空间。界面默认十进制GB，详情列GiB与字节；业务库、raw、work、其他运行版本及备份不在此统计内。页面读取不递归统计其他实例或备份，不进行内容SHA重算或清理。

- `date`/`as_of` 必须属于已发布范围内的完成市场交易日；未来、无效格式或越界日期拒绝。盘面日期不会修改业务时钟。
- 股票/指数使用完整证券 ID，例如 `000001.SH` 与 `000001.SZ`；板块使用已核验目录的完整来源 `topic_code`。
- `data_coverage` 含 `label/start/end/basis/notice/selected_date_available/partial/available_dates`。basis 是可读口径；日期列表仅表示明确留存日，不保证中间连续完整。
- 主行情、来源涨跌停池、资金、异常名单和成员快照分别披露范围。缺来源不填零，不以抓取时间冒充数据日。

`GET /data/status` 的每个 `datasets[]` 新增 `coverage_progress`：`label/observed_count/expected_count/percent/unit/dimension/basis/state/reason`。`row_count` 仍表示已留存物理记录，与完整度分子分开；只有同一口径分母明确、分子不超过分母且分母大于零时 percent 才是 0–100，其他为 null。

新建在线补充、首次及增量同步的公开资金请求冻结 `fund_request_budget_version=public-fund-budget-v1`，随已授权题材/板块后续链原样传递。缺少该字段的旧任务及其重试保持旧两轮预算；不改写旧参数、任务哈希或暂停/取消状态。新预算最多3在途、两轮共享200逻辑请求/600传输尝试/60秒异步截止目标；同类connection/schema/5xx/限流/拒绝连续5次停止本轮后续请求。第二轮只请求暂时失败的明确代码；首轮目录暂时失败时，恢复目录后允许首次请求，永久数据缺口不重拉。首个已确认目录冻结代码/名称/分类指纹，二轮目录变化保留首轮分母、记录和缺口并标partial，不能因目录缩小而完整。覆盖保留 `logical_request_count/actual_transport_attempt_count/peak_inflight_requests/source_circuit_open/source_failure_kind/stop_reason/local_cache_validation_seconds` 等计数。逻辑请求按首次实际发起HTTP计，传输尝试按应用HTTP调用计，不代表DNS/TCP包数；未发起的代码单列，未请求不作零或完整。异步等待、响应体、退避及在途取消受共享截止约束，暂停会等待请求取消收尾；同步本地IO和平台DNS收尾可能超时，因此 `whole_phase_hard_deadline_guaranteed=false`，不承诺整个补充任务60秒结束。未知版本拒绝，不降级或自动取数。

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
| `GET /overview?date=` 或 `/market/overview?date=` | 四指数、广度、总成交额、成交额排行、封板观察、连板反馈概览等 |
| `GET /market/indices?date=` | 指数页轻量读取，只含 `date/snapshot_id/indices/data_coverage/quality`；四指数报价与总览一致，不构建全市场股票面板、30日榜或年内涨停证明，不展开盘面补充历史 |
| `GET /instruments?query=&limit=&fuzzy=false` | 股票/指数专用检索。默认沿用原包含匹配。选择器传 `fuzzy=true`，在股票、指数范围复用名称、代码、全拼、首字母与轻度错字匹配，limit 为 1–100 |
| `GET /search?query=&limit=&stocks_only=false` | 股票、指数、行业、概念统一检索；空白返回空，limit 为 1–100。`stocks_only=true` 在排序截取前仅保留股票，复用相同名称、代码、拼音、首字母及容错匹配；默认行为不变 |
| `GET /instruments/{id}?as_of=` | 标的身份、所选日行情、可用题材/风险标签及缺口 |

页面壳使用 `GET /bootstrap?scope=navigation`，读取相同的业务日、行情代际、日历、指数、来源和行数等导航元信息，但不调用全库文件空间遍历；`data_summary.storage_bytes/storage_accounting` 为null，不能当作零占用。默认 `scope=full` 及 `GET /data/status` 继续执行原精确空间统计，响应兼容旧接口。`scope` 仅接受`full/navigation`。日期、初始化与增量锁门禁不因轻量读取放宽。

0.1.9的读取实现中，`GET /sources` 按当前凭据修订选取每项能力的最新本地核验记录，只解码被选中的证据；配置、修订和活动来源在同一SQLite读取快照中获取，凭据是否存在仍当次检查，不解密或返回Key，不缓存账号权限结论。`public_supplement` 保持原字段、日期与未知语义：只物化状态展示需要的当日池和资金字段及板块来源记录，但仍逐项验证完整补充索引的所有留存对象和嵌套引用。历史对象损坏仍使该状态未知，不能用裁剪读取掩盖来源缺口。`/bootstrap` 的来源列表不重复构建未使用的补充状态；容量统计继续遵循上述精确遍历口径。

统一检索支持名称/代码、全拼、拼音首字母及有界轻度错字建议。去空格后 query 最多 100 字，原始最多 512 字；按准确、前缀、包含、拼音/首字母、同音、近似名称排序，同层按类型平衡截取。名称和代码支持Unicode NFKC归一化；拼音不区分大小写，可含空格/分词连接符。拼音/首字母至少2字母；中文同音/错字至少3汉字，拼音错字至少5字母；近似仅允许一处插入、删除、替换或相邻交换，不模糊猜短首字母，不对数字代码纠错。

返回 `items/total/matched_by_kind/scope/current_catalog/historical_membership`。股票/指数有 `instrument_id/kind/target_type`；板块另有 `topic_code/topic_id/sector_kind`，`target_type=sector`。各行附`match_type/match_label/is_suggestion`，同音或错字结果必须标示“可能想找”，保留原名称、代码、类型，由用户明确选择；不自动修改查询、标的身份或输入框。空白不读目录；搜索不读行情面板、不联网。只缓存至多20000个不可变名称的拼音变换，每次重新取当前证券及已核验板块目录；更名、删除、目录冲突立即影响结果。当前目录只说明身份，不证明历史目录或成员。个股/研究专用`/instruments`入口与类型范围保持原契约。

总览指数的自身涨跌幅与 `amount_change` 分开：金额差按 T 与前一精确市场交易日金额（元）相减；缺前期或非法金额为 null。沪深总成交额不能将四个指数相加得到。

指数轻接口沿用总览的报价、指标预热、金额比较与行情覆盖口径；`date` 校验相同，缺指数数据保持原有未知，不取数或补造。完整总览接口和其他盘面日期作用域不变。

## 盘面页面

`GET /market/{kind}?date=` 支持 `emotion/sectors/ladder/gainers/abnormal/flow` 等页面分支；`limit/query/page/page_size/sort_by/sort_order/top_n` 仅在相应分支生效，以路由校验为准。

| 分支 | 主要口径 |
| --- | --- |
| emotion | 广度历史及 `sentiment` 的市场赚钱效应、昨日涨停样本今日价格反馈；无神秘总分 |
| sectors | 指定日来源指数涨幅/成交额；没有真实指数时可明确回退存档成员等权样本 |
| ladder | 来源连板梯队、首末封/开板等可用字段；反包、失败、20cm 分开，未核验核心不造龙头 |
| gainers | 精确 T/T−30 市场交易日端点、年内涨停证据、技术观察和风险提示 |
| abnormal | 指定日历史仅名单观察日精确匹配；显式latest读取最新已留存名单，两个范围不混用 |
| flow（旧接口兼容） | 历史留存资金只读；页面与趋势撤下，新手动任务不再采集 |

### 盘面浏览缓存与分层读取

- 盘面缓存仅保存有界的派生读模型。盘面总览、指数、市场情绪、行业与概念、涨停梯队、30 日涨幅榜、监管、龙虎榜及竞价、午评、盘中观察分别复用其已核验的行情、名单或快照计算。请求仍先确认观察日、发布代际和实际来源，题材归属、任务状态和其它可变业务信息按原接口读取。不同日期、名单范围或来源各自校验。缺档、冲突和未知保留原口径。
- 常规缓存按最近 7 个已发布交易日滚动，包含最新日。发布新的交易日时，最早一天滚出窗口并清理常规缓存。打开更早的观察日可为该日建立独立临时缓存，从第一块成功写入起保留 24 小时。命中、打开同日其他明细和源更新均不续期。临时缓存拥有独立文件和容量限制，核验失败或到期时从当前本地来源按需重建。
- 午评快照成功发布后可立即复用，盘中相邻档比较在该日正式收盘发布后才复用。实时请求仍读取当前快照。同一档缺少可信的上一档时继续返回未知。首次初始化及新盘后增量链完成并发布后先预热当日盘面块，再由唯一 worker 在空闲轮次按日分步补齐最近 7 个已发布交易日。启动时也检查这个窗口。复用有效的落盘块，缺块使用同日期本机归档重建，遇新行情版本停止旧轮次，避开竞价保护时段。预热不创建任务、追加取数或扩大既有任务范围。
- 市场情绪页面打开或切换日期时请求完整接口，自动展示炸板分析、连板反馈和广度历史。各派生块复用已核验的行情和事件，题材归属按实际日期重新读取。`GET /market/emotion?date=&summary_only=true` 继续支持只需核心字段的调用方。
- 个股、指数与板块图表和个股固定统计按同一交易日范围按需复用有界内存计算。图表分开核验证券、日期、周期、复权口径与实际行情来源。补充证据和用户编辑内容继续走各自的读取和验证，缓存不改变图表、策略与回测的计算口径。
- 盘中观察的收盘回看可在进程内复用原收盘代际的两日报价和公司行动筛选结果，命中前仍核验实际文件。盘中快照及简报按原归档读取，采集状态和控制配置实时读取。
- 近 30 日涨幅榜与总览前五共用固定日期的端点涨幅排名。全量榜的涨停依据另存紧凑派生缓存，两者随其他盘面文件保留至下次启动。题材归属、筛选和页面装饰每次按原路径读取。

`abnormal` 当前接入供应商的严重异动与重点监控 `state=current` 名单。首次主行情同步按每页30条读取完整分页，并分别保留名单 `abnormal_observed_date/abnormal_fetched_at`；后续题材库重发只复用已留存名单，必须保留原观察日与获取时间，缺失仍为未知，不得改成题材任务本次 `collected_at`。板块K线及盘面补充完成不代表另行取得监管历史全集或官方函件。

`GET /market/abnormal?date=&monitor_scope=historical|latest` 默认 `historical`，保持指定日精确匹配。`latest` 只从已登记且通过SHA核验的发布存档，按名单观察日、名单获取时间选择最新可读名单；不会联网或创建任务，不按较晚题材重发时间更新名单时点。页面默认显示明确标注的最新已留存名单，可切换指定日历史，不修改盘面共享日期。返回 `monitor_scope/requested_date/monitor_observed_date/monitor_fetched_at/quote_as_of`，名单日期与报价日期分开；例如周六24日首次采集、行情截至周五23日时，最新名单可为24日，股票报价与打开K线仍截至23日，23日历史名单仍未知。latest的顶层 `date` 为报价日，`requested_date` 保留原盘面选择日；其 `data_coverage` 可列非交易日观察日期，不冒称交易日历史档案。无合法观察日期的名单不可用，已核验空名单才返回已知空；损坏存档跳过并提示，降级旧档须披露旧观察日。其他分支不接受latest范围。

名单记录标识 `evidence_type=provider_monitor`、`official_document=null`。供应商记录的起止区间不等于交易所正式监管起止或解除证明；无匹配观察日保持“未留存/未知”，不表示没有发生监管。正式监管结束日没有权威材料时保持未知，不由来源标签、停复牌日期或预计期限补齐。

`sectors` 行包含 `quote_available/quote_source/quote_basis/members_available`。`quote_basis` 为 `provider_index` 或 `archived_member_sample`；没有成员时成员数、涨跌停家数为 null，但真实指数行情继续显示。顶层 `quote_coverage` 与 `membership_coverage` 分别返回，当前目录数量不称历史目录完整。

`GET /market/topic-members?date=&topic_id=`，或使用明确 `source_code/kind/source_name`，只读所选日可用的历史成员档案；找不到时明确不可用。不能按同名当前成员补档。

`GET /market/mainlines?date=&known_at_ms?` 与 `GET /market/mainlines/history?date=&instrument_id=&limit=&offset=` 读取独立的逐日手工主线审计；不改变 `ladder` 的行业、概念和原始来源原因。它们只读SQLite，date是合法ISO审计日期，即使当前行情目录已滚动也可读旧记录；无记录为空，不由当前概念补全。列表total是有修订的证券数，含撤销，不是涨停家数；这些接口仅保留既有手工数据的兼容读取，当前自动主线页面不消费该结果。写入、更正、撤销、来源转述/用户判断/待核验/冲突以及真实记录时间契约见[Workspace API：当日主线](workspace-api.md#当日主线归因手工证据-v01)。

`GET /market/ladder?date=` 返回只读 `system_mainlines`，自动页面不读取手工审计。版本 `algorithm_version=source-reason-directions-v4`、`source_kind=local_source_synthesis`，包含 `state=candidate|unavailable/date/snapshot_id/groups/other_groups/related_groups/individual_cues/missing_reason/coverage/reason/notice`。只复用本次已读同日确认收盘涨停行、唯一有效原始收盘与正成交量、绑定收盘事件证据；日期、行情代际、证券身份、原价、冲突或重复不一致时拒绝纳入，不以池价代替缺失报价。收盘梯队缓存键包含算法版本。

四个自动分区分别为重点方向`groups`、其他共同方向/事件组`other_groups`、未成组但有原文的`individual_cues`、真正空或占位原因的`missing_reason`。每只证券仅占一个分类位置，四个分区去重家数之和等于`coverage.confirmed_sample_count`。`coverage`保留`examined_row_count/eligible_reason_count/rejected_row_count/source_pool_complete/market_complete=false`，并分别给出`focus_count/other_count/individual_count/missing_reason_count`；拒绝行不伪造为缺原因证券，也不声称样本等于全市场。

来源原因按半角/全角加号拆分、trim并按原文顺序去重。明确含机器人、具身智能的正向标签可归“机器人相关”；只有物理AI或否认、不涉及等反向描述不强归。剩余股票的主归类用首个明确来源标签。有限的冰雪、煤炭、短剧漫剧、航空航天、AI 应用、PCB、光伏、零售和业绩标签归入对应家族，明确写出的“字辈”归入各自字辈，其余保留原标签。例如印制电路板归 PCB，光伏 BIPV 归光伏，覆铜板原文独立保留。

`related_groups` 是独立的来源关联，读取原文中各位置的明确标签。至少两只确认样本共享明确家族或同一原标签才展示，最多 24 组，优先保留明确家族、字辈及原文明确写出的“商业航天”。航材、卫星等已列标签可归“航空航天”，只有原文写明“商业航天”才能计入单独的商业航天关联。业绩、股权及通用所有制等背景词不生成重复标签关联。每组按证券去重，同一证券可跨组，同键关联须覆盖原组全部成员且增加至少一只。关联不计入四个主分区的统计。匹配标签、原始原因和日期照原值返回，归类不从公司名、当前目录或后续行情推断历史。

方向至少3只才可进重点，按家数降序、已核验最高板高降序、有效首封时间及名称稳定排序，最多3组，不硬凑。其余至少2只的共同方向及事件组进入`other_groups`，事件不进入重点。单例、长叙述及标签保护上限进入`individual_cues`，不能把它们误称来源未提供原因；真正空或占位原因才进入`missing_reason`。原文保护界限不影响原价/收盘事件核验。

组结构为`key/name/kind=direction|catalyst/grouping_rule/labels/count/max_known_height/high_stock_ids/members`。`labels`是组内实际命中的原文标签去重汇总，高度未知为null，高标不认证龙头。所有成员统一包含`instrument_id/source_reason/matched_tag/matched_tags/source/source_ref/closing_pool_refs`；`matched_tags`为此次规则实际命中的原文标签、按原文顺序去重，`matched_tag`为第一项或无命中时空值，不将族名冒充原文。`source_ref`绑定`date/snapshot_id/endpoint=/api/v1/market/ladder/field_path=items[instrument_id="…"].reason`；原因提供方缺据则`source=null`，不拿报价/封板详情来源代填。`closing_pool_refs`只保留收盘事件来源、路径及有据原始SHA，固定`purpose=closing_event_only`，不独立证明原因。

前端验证同日同代、完整ID、原文/标签绑定、四个主分区去重及家数，再单独验证来源关联。方向表同键使用较完整的名单并明确标为来源关联，按实际展示家数降序，家数相同再按已核验最高板高排序。下方仅展示未出现在任何可见方向名单的独立来源线索。全局高标独立从全部同日确认样本的`board_presentation.height`核验。正文展示方向、样本和原始原因，技术定位和SHA保留在API。既有SQLite手工记录及兼容API独立保留，旧修订不参与自动页面分类。系统仅使用本地已留存材料，不创建任务或迁移。

`gainers` 的检索/排序对完整候选集合执行后再分页，null 升降序都最后；年内次数下界不参与精确次数排序。`flow` 的近若干日是跨日趋势，不是全天盘中资金时序，也不推断机构真实决策。

完整总览的 `top_30d` 仍先按全量证券的已核验端点收益排名，再补齐前五行的涨停证据；为保持原始覆盖字段一致，只核验这些行所属的原始256证券批次。此小样结果与完整涨幅榜分开缓存，不能供完整榜检索、排序或分页复用；缓存按行情根/代际、观察日、实际补充文档指纹、停牌证据代际和完整/前五范围隔离，最多保留4个范围。来源事件的收盘核验每批最多256证券，只读取所需日期与收盘，保留重复日期、缺值和原有未知语义，不将全量OHLC或当前归属加入历史事件证明。

`gainers` 同时返回 `last_limit_up_date/last_limit_up_complete/last_limit_up_coverage/sessions_since_limit_up`，含义与成分股、个股统计一致；支持按 `last_limit_up_date` 对全部榜内候选排序后分页。它使用观察日之前的逐日来源涨停池与该证券当日唯一原始收盘批量核验，每批最多256只、仅从最早留存事件源日读取，不逐股调用详情或据涨幅推算涨停。不完整年内统计只在已确认次数大于0时展示下界；已确认0且仍有缺口展示待核验，完整核验的0仍显示0次。没有可核验封板分钟时此列只展示日期。

成员、个股摘要与涨幅榜的年内已确认次数下界，包含逐股同日原始收盘核验通过的 reference 历史池、年度池及公开补充池正例，跨源按日期去重，仅计观察日之前本年事件。补充正例不能把来源池缺席变为否定证据，也不能将不完整年度总数变成精确次数。

`sentiment.market/short_term` 含状态、范围、指标、样本与缺口。`sample_count` 为有效可比数，`observed_count` 为观察候选数，`unknown_count` 为差额；收益样本覆盖不等于来源全集完整。晋级率证据不全时为 null，已知价格反馈不冒充接力晋级结论。

### 情绪三轴上下文

`sentiment.model=dated-three-axis-context-v1`，原全体昨日涨停队列口径保留在 `market/short_term`，并返回 `legacy_cohort_model`。新增指数环境、制度分组和证据摘要；三轴对象不合成统一温度，也不确认离线六阶段。独立的顶层 `market_outlook` 温度及单日阶段见下节。

- `index_environment.indices[]` 按四个指数分别返回 `instrument_id/name/date/state/state_label/scope`、`sample_count/observed_count/unknown_count/coverage_pct`、窗口边界、缺失/重复日期、`window_fingerprint` 与同形 `metrics[]`。指标键是 `index_daily_return_pct/index_20d_return_pct/index_ma20_distance_pct/index_20d_volatility_pct`。后3项必须有T及T−20之间21个严格市场交易日唯一有效收盘，不能用最近21根替代；MA20按最后20个收盘，波动为20个日对数收益的样本标准差×100、ddof=1、未年化。当前日缺失不退回更早报价；来源冲突、无效日历或预热不足保留null。收益和相对MA可为负，波动不按0—100比例封顶。
- `short_term.metrics` 增加 `positive_ratio_pct`，分母为收益有效队列，不称完整队列正反馈率。`short_term.groups[]` 按P日有据身份冻结，含 `id/label/state/state_label/scope`、同形指标、`sample_count/observed_count/unknown_count/coverage_pct`、`candidate_count/confirmed_count/unverified_count`、`unallocated_candidate_count`、`cohort_source_complete/identity_complete/current_identity_unknown_count`、`promoted_count/failed_count/promotion_unknown_count`、`cohort_fingerprint`、`promotion_bounds_pct/positive_ratio_bounds_pct/median_bounds_pct` 和 `promotion_bounds_reason`。
- 分组ID为 `ordinary_10/twenty_20/thirty_30/st/listing/unknown`，返回 `default_group_id=ordinary_10` 供严格同制度分析使用，界面价格观察默认展示全部队列。普通组必须有P日明确非ST证据及上市阶段排除，不能从当前名称或涨幅猜测。T日身份不明或已变制度时，原组 `metrics` 中严格同制度子样本的T日收益/晋级保留未知；全体队列及下述 `cohort_price_feedback` 的独立价格观察仍保留有效价格。ST单列但不假定统一涨跌幅；上市前五个留存市场日作保守隔离，不声称各市场新股例外完全相同。存在P日未分组候选时，不能证明普通组完整分母。
- `short_term.groups[].cohort_price_feedback` 将昨日冻结分组的今日价格反馈与严格同制度接力分开，版本 `previous-group-price-feedback-v1`。含 `group_id/date/cohort_date/scope/price_basis/sample_count/observed_count/unknown_count/coverage_pct/state/state_label/cohort_source_complete/identity_complete/current_identity_unknown_count/metrics/notice`。内部核验成员保留独立 `price_return_pct`，不公开展开成员；今日制度身份未知或改变不单独剔除有效价格，缺价、停牌或公司行动不可比仍为null。均幅、中位数和价格正反馈按此独立样本计算，不能把今日继续涨停者的均幅称作昨日全队列均幅。
- 原组 `metrics` 保留严格同制度子样本语义；新价格对象不能替代晋级区间或被离线草案用作晋级证据。价格覆盖100%仅指P日已确认分组成员，不代表来源全集或分组完整。规则版本为 `descriptive-context-v2`，相应输入及队列指纹更新；历史手动留存记录原样保留，新保存按新指纹核验。界面以新对象展示价格反馈，严格接力与缺口按需展开；旧快照缺新对象时明确标为可比子样本观察。
- `promotion_bounds_pct={lower,upper}` 的单位是百分数，仅昨日全集和该组分母可证明时返回 `[A/N,(A+U)/N]×100`；未知不能取中点。U=0才有精确 `promotion_rate_pct`。缺完整分母返回null并解释。正反馈区间把收益未知分别按全负/全正计，中位数区间把未知放在排序两端；中位数某端无界时为null，不能当0。各组仅分配已确认P日成员，未确认候选另列 `unallocated_candidate_count`，不能把分组相加当原始来源全集。
- `provenance` 返回 `model/rule_version/trade_date/mode/label/input_snapshot_ids/input_fingerprint/computed_at`、`source_records[]`、`market_collected_at/first_known_at/known_time_complete/notice`。每个来源记录含交易日、池、来源路径、原有 `fetched_at/source_published_at/first_known_at/source_sha256` 及规范化内容指纹。原始SHA缺失时不拿派生指纹冒充原始SHA；抓取时间不重命名为首次可知。当前mode固定 `retained_retrospective`，缺逐日首知档案不声称历史当时可用。`computed_at`是本次真实计算时间，重复请求可不同；其余指纹只依赖对应日期留存输入。

六阶段研究纯函数见 `app.services.sentiment_stage_research`，未接入HTTP情绪阶段、策略筛选或自动任务。`observation_from_group`只适配已有证据；`evaluate_stage_candidate`输出草案版本、参数指纹、逐谓词真/假/未知、候选与未知候选、历史分位上下界；`advance_stage_state/replay_stage_candidates`处理严格相邻确认、滞回和缺日中断。默认普通10%队列至少10只、收益可比率至少80%，缺证据不补值；晋级30%—50%跨过40%时高热谓词未知。阶段仅为 `offline_research_only`、`validated=false` 的待验草案，来源集中补采只能按当前留存回看。`as_known`模式要求每个交易日自己的带时区截止时间及完整首次可知证明，晚到材料不能进入早期时点。首次确认日在第2个满足日，结果不回填第1日；未知时只保留有日期的历史确认，不把旧阶段延续成当前。

### 市场温度与下一交易日条件预案

`GET /market/emotion?date=` 另增顶层 `market_outlook`，版本 `dated-market-outlook-v1`。顶层预案使用自己的规则及输入指纹，不覆盖 `sentiment` 指纹；本轮 `sentiment` 新计算使用上节的 `descriptive-context-v2`，相应价格对象和指纹按该版本更新，旧手动留存内容原样保留。新字段是独立公开阈值的单日盘面观察，不是将上述离线六阶段草案转正。首屏显示读数、今日变化、机会/风险/缺口和条件预案，完整三轴指标、来源和留存功能按需展开。新增提示不调整既有温度、阶段与20%显著放量阈值，不新增情绪总分。

- `date/snapshot_id`绑定本次观察日和不可变行情代际。`temperature`含 `value/previous_value/delta/previous_date/scope/coverage_pct/calendar_valid/comparable_count/universe_expected_known/evidence_consistent`；温度固定同口径沪深上涨/(上涨+下跌+平盘)×100，不混入沪深京个股比例或队列反馈。当前可比率不足80%不判阶段，前日须为严格相邻同范围、有唯一广度记录且可比率达标的市场日；跨输入日期、范围或涨跌平数字冲突时待核验，缺档不跨日确认修复或退潮。
- `temperature`另含 `universe_upper_bound/coverage_basis/observed_coverage_pct/universe_bound_known`。真实应有范围已知时使用该范围；否则只允许使用已取得的沪深主表数量作保守上界，分母取既有分母与上界的较大值，basis为 `retained_master_upper_bound`，不声称历史应有范围完整。应有范围未知且无有据上界时，basis为 `unbounded_observed_sample`、`coverage_pct=null`，保留观察温度和 `observed_coverage_pct`，阶段待核验且禁止开仓；前日与5日冰点历史同样约束，防止极少样本被误算为100%覆盖。
- `stage={code,label,status,summary,reasons,conflicts,limitations}`。code为 `unknown/ice/repair/ebb/climax/broad_rise/mixed/divergence`，分别为待核验、冰点、修复、退潮、高潮、普涨、混沌、分化。阶段仅 `observation` 或 `unknown`，不声称完整周期已验证。混沌只用于已知方向矛盾，缺数据不默认混沌。冰点、修复、退潮、普涨沿用公开广度阈值；高潮额外要求沪深当日来源候选逐股原收盘及正成交量核验，完整来源涨停≥80且跌停≤5；来源缺档、冲突和空池不强算。`rules[]`返回版本化阈值。
- `axes[]`并列指数环境、个股广度和短线价格反馈的 `id/label/state/state_label/evidence/scope`。四指数分别使用严格21日窗口，环境方向不合成价格收益；短线另含 `ordinary_group_ready`。普通10%同制度证据不足时，不能把全部昨日涨停队列价格反馈或混合制度晋级率当作该组精确接力结论。
- `turnover={amount,previous_date,previous_amount,change_pct,expansion_threshold_pct,expanded,state,scope,reason,comparison_complete}` 与盘面总览使用同一沪深京股票成交额元值及严格相邻比较。显著放量阈值为20%，恰好达到也满足；缺失、重复或范围不可比时 `expanded=null`，不能当作不放量或允许开仓。
- `bottom_watch` 含 `status:candidate|unconfirmed|unknown/confirmed:false/label/candidate_date/index_references/confirmation_conditions/invalidations/lookback_sessions/observations/history_complete/prior_ice_streak/recent_ice_count`。近5日观察只读T之前严格交易日，缺档中断连续计数。当日为 `repair` 或 `climax`、严格前日真实冰点、成交额显著放大且四指数当日低点及收盘参照齐备时仅列候选；这不调整原阶段优先级或阈值，避免高潮标签遮蔽相同冰点修复证据。后续确认条件固定待观察，不能用未来价格确认T日底部。
- `entry_gate={status:blocked|conditional|wait,label,reasons,release_conditions,blocked_conditions,routes}`。冰点、退潮、必要广度证据不足或成交额比较未知为优先禁止条件。`routes[]`与下述 `insights.routes` 使用同一分路径对象；趋势条件不依赖普通10%接力证据，短线接力须额外满足本组证据，冰点修复保留原候选门槛。修复未形成候选且趋势路径未满足时仍禁止开仓；高潮不因标签本身一律等待，而按实际路径证据判断。普通趋势路径不要求20%放量，但成交额比较必须真实可比。任何放行仍为下一交易日条件清单；此对象不接管策略、跟踪或回测的执行。
- `next_session={date,label,calendar_reason}`，date仅从已留存市场日历取严格相邻后一交易日；没有未来日历时为空并显示下一交易日，不用自然日T+1或工作日猜测。历史回看可显示已留存下一交易日的日期，但不得读取其价格来制定T日预案。
- `plan={stance,label,summary,actions,scenarios,trigger_state,verification_mode,notice}`。actions固定 `open/add/bottom`，对应开仓、加仓、抄底；每项有 `status:blocked|avoid|wait|conditional/status_label/reason/triggers/invalidations/trigger_state`。scenarios为转强、分歧、转弱，各列观察、应对与失效。所有触发状态固定 `unobserved`，模式为 `manual_next_session`；没有盘中数据时不能标已满足。加仓须另核验已有持仓的原定失效条件，不读取或假造账户成本和仓位，不输出自动交易或承诺可成交。一字封板不代表下一日可买。
- `watchlist={topics,stocks,coverage,empty_reason,topics_empty_reason}`：最多3个方向、每方向3只股、独立个股列表5只，角色为观察候选。股票须有T日唯一正原始收盘、正成交量和精确匹配的同日来源涨停证据；含 `instrument_id/name/code/close/change_pct/board_count/evidence_date/role/reason/trigger/invalidations/source/price_basis/event_basis/source_fingerprint`。排序依来源连板数、当日成交额、代码；题材按指定日成员下已确认涨停下界排序，重叠分别计，不自动声称真实上涨原因或龙头。缺历史成员仅保留有据个股，不借当前成员回填历史。
- `insights` 为独立的提示对象，含 `version:"market-opportunity-insights-v1"/date/snapshot_id/headline/changes/opportunities/risks/gaps/routes/candidate_tracking`，与本次观察日及行情代际绑定。`headline={code,label,summary,observation_state:"observed"|"unknown",execution_state:"unobserved"}` 区分已观察到的盘面与尚未触发的执行条件。`opportunities/risks/gaps` 分别为字符串数组，机会、风险和资料缺口独立保留；不以缺资料证明风险已经发生，也不以机会抵消风险或缺口。
- `insights.changes[]` 含 `id/label/state/date/previous_date/value/previous_value/delta/unit/delta_unit/evidence/interpretation/scope/reason`，逐项展示今日读数、严格前日比较、证据及解释；前日缺失、重复或口径不可比时保留未知及原因，不用较早日期补位或将缺失补0。`unit`为当前值单位（广度`/100`、成交额`CNY`），`delta_unit`独立区分温度变化的百分点与成交额变化的百分比；昨板今日反馈仅展示当日观察，不冒充与前日队列相同的变化率。
- `insights.routes[]` 及 `entry_gate.routes[]` 各项含 `id:"trend"|"relay"|"repair"/label/status:"blocked"|"wait"|"conditional"/status_label/observation/reason/missing_conditions/triggers/invalidations/trigger_state:"unobserved"`，后三组条件为字符串数组。`trend` 在指数环境强、广度强及严格相邻量能比较已知时可为条件复核，不要求普通10%制度组齐备；`relay` 另要求短线反馈强和 `ordinary_group_ready`；`repair` 以当日短底候选为条件。全局必要证据和冰点/退潮限制优先于任何路径。分路径为趋势加仓与短线接力分别提供证据，已有持仓仍须人工核对原定失效条件、所属方向和真实可成交条件，修复候选不直接形成加仓建议。
- `insights.candidate_tracking` 含 `status:"candidate"|"continuing"|"diverging"|"failed"|"unknown"|"none"/label/candidate_date/last_observed_date/as_of/lookback_sessions:20/window_begin/window_end/history_complete/confirmed:false/reason/conditions/index_references/observations/notice`。`observations[]` 含 `date/state/label/temperature/broken_indices/reason`，其中 `broken_indices` 为代码数组。窗口最多覆盖截至T的20个严格市场交易日，复用已读广度、成交额、四指数来源及严格日历，不逐日调用panel或全量sentiment重算，不展开全历史。候选及后续观察只使用不晚于该回放日的数据；任一指数收盘低于候选日实际低点时 `failed`，终止该候选；四指数收盘齐备、均未破候选低点且广度温度≥60时 `continuing`；其余有据情况为 `diverging`。缺必要资料为 `unknown`，不得沿用旧状态充作当前有效；窗口内无候选为 `none`，新独立候选可替换已失效候选。`confirmed` 始终为false，上述状态是未验证的描述规则，不代表确定底部或可执行信号，也不外推20日窗口之外的延续情况。
- `provenance`保留新规则、输入指纹、实际计算时间、行情代际及 `mode=retained_retrospective`；当前留存材料回看不等于T日当时可知或已经发布盘前计划。指纹包括实际阶段输入、事件核验、三轴、候选及新增提示；不覆盖原sentiment指纹。新预案及候选跟踪仅读发布数据，不联网、不写业务表、不创建任务。已有手动情绪快照仅保存原三轴证据，不包含顶层预案、`insights`或候选跟踪，不声称已归档这些对象。

涨停梯队各股票行另外增加 `snapshot_id/pattern_events/pattern_status/pattern_date/pattern_as_of/pattern_coverage`，形态使用同一 `verified-source-close-pool-and-raw-ohlc-v1` 判定，沿用一次有限三日批量报价和小日历，不逐股展开全历史。前端必须同时匹配观察日、行及事件代际和原价basis。主涨停类显示地天板、一字涨停；失败组缺独立同日跌停状态时保持未知，不推测一字跌停。连板/概念/当日主线/反包/20cm卡片共用同一标记，不改高度、分类、原事件或策略。使用说明见[市场温度与下一交易日计划](../docs/市场温度与下一交易日计划.md)。

### 连板高度、板型与反馈展示

总览个股榜与收盘快照、近30日榜分页行、行业概念成分股、个股概要、梯队股票行、市场情绪与连板反馈提供 `board_presentation`，版本 `dated-stock-board-presentation-v1`。对象绑定 `instrument_id/date/snapshot_id`；不得跨证券、日期或行情代际复用。原始字段及旧 `pattern_*` 保持原义。

- `height/previous_height={value,label,state,source_date,reason}`：当日/严格前一交易日确认涨停与同日来源正整数连板高度。未知不补首板；前日高度必须明确标为“昨”。
- `types[]={code,label,state,source_date,reason}`：独立核验的 `earth_to_sky/one_price_limit_up/one_price_limit_down/limit_down/non_one_price_limit_up/open_count/repeated_break/pullback_reclaim/broken_pool_source/broken_board/rebound_board`。涨跌停使用原始收盘、正成交量与同日来源；炸板来源样本另核验身份、日期和价格。有同日来源炸板池且确认收盘未涨停时标记炸板；仅核实来源匹配时标记炸板来源样本。反包板候选按严格相邻三交易日核验涨停、未涨停、涨停，不代表K线实体完全包住。开板次数缺失不补0，反复开板为来源次数≥3的项目展示阈值，不是标准烂板评级。非一字成交价差不证明充分换手；回踩收回仅指日线最低价低于前收且收盘不低于前收。
- `volume={state,label,ratio,previous_date,reason}`：严格相邻交易日正成交量比较，`expanded/contracted/flat/unknown` 分别为放量/缩量/平量/未知。原始OHLCV须与当前代际panel相符且公司行动、价格连续性可比，比例不使用成交额替代。
- `feedback={code,label,state,source_date,cohort_date,reason}`：前日确认涨停样本的当日观察，优先跌停、晋级，然后可比收盘收益≤−5%的大幅回落、回落、正反馈或平盘。未知不当作晋级失败；正反馈不等于资金承接或可成交收益。`limitations[]` 保留各维度的证据限制。

连板反馈默认按已核验的昨日高度降序、代码升序，未知置末；新展示对象存在时，排序与高度分组均不从未核验原字段回填，原始 `previous_board_count` 保留。明细 `feedback` 仅返回昨日二板及以上已留存连板样本，缺高度证据保留未知。`/market/overview` 与 `/market/emotion` 同时返回 `first_board_feedback`，只供概览：按严格前市场日涨停池明确的正整数高度1筛选，并核验同日收盘涨停证据；不从缺失梯队推断首板，不为该队列展开题材。该轻量队列仅含证券、日期、代际、昨日高度、当日涨幅及核验展示对象；当日报价缺失保留null。`first_board_feedback_coverage` 返回 previous_date/source_available/confirmed_count/unverified_count/state/scope；state为ready/partial/unknown，缺来源不填0。首板不进入明细、排序或上一只/下一只导航。资金轮动不再返回feedback。总览两组前五共用一次最多10只P/T批量原始报价；连板明细与首板概览各使用一次对应队列的P/T批量；梯队复用原有有限三日报价及已读补充来源，不逐股展开历史或发起采集。具体定义与公开资料见[连板高度板型与反馈](../docs/连板高度板型与反馈.md)。

`board_presentation`另有`board_text={label,state,source,source_date,window_sessions,limit_up_count,reason}`，只读取扶摇同日已核验收盘池的`continue_day_text`原文（例如首板、2连板、5天4板），状态为reported/unknown/conflict。证券、日期、原始收盘及涨停状态均须匹配；缺字段不按连板高度推算，冲突不任选一条。N天M板仅为来源统计描述，不代表连续涨停高度，不新增策略信号或改变排序。公共梯队、情绪和盘面样本复用该字段；个股统计`board_presentation`以已有同日/前日有限报价批及同一来源闭包补充高度和文本，不新增全历史读取。相同的首板/连板标签去重。

### 手动留存复盘上下文

- `POST /market/sentiment-snapshots` 仅接受 `{date,expected_input_fingerprint}`；指纹必须与页面当前64位小写十六进制值一致，拒绝客户端payload、模型、时间和其他字段。服务器在SQLite写事务外从当前不可变发布源重算上下文，输入已变化时409且 `detail.current_input_fingerprint` 返回当前指纹，不能静默保存另一份证据。仅手动调用，不随GET浏览、日期切换、任务或定时自动创建。
- 成功200返回 `snapshot_id/date/recorded_at_ms/record_mode/model/rule_version/input_fingerprint/content_sha256/payload/created/reused/notice`。`payload`为服务器计算的完整sentiment，`record_mode=manual_retained_capture`；`recorded_at_ms`是本次实际留存时间，不代表交易日T已知。payload中的 `provenance.mode` 仍为 `retained_retrospective`，来源的 `first_known_at`、`fetched_at`不改写。`content_sha256`为规范化UTF-8 JSON内容的SHA256。
- 相同 `date+rule_version+input_fingerprint` 幂等返回原记录（`created=false,reused=true`），保持原payload、内容SHA与原留存时间。新输入/规则追加新记录，不覆盖旧记录。事务内校验实际时间不早于计算前检查时刻及全表已留存最大时刻，回退409且不伪造递增毫秒。018迁移建立独立append-only表、唯一键、更新/删除/替换保护和跨日期时钟保护，不填入任何种子记录。
- `GET /market/sentiment-snapshots?date=&known_at_ms=&limit=20&offset=0`：date与known_at_ms可省略，limit为1—100，offset为0—2147483647。按实际留存时间 `recorded_at_ms<=known_at_ms` 筛选，时间降序、ID降序稳定分页；返回 `date/known_at_ms/items/total/limit/offset/notice`。items是上述元数据摘要，不含payload。
- `GET /market/sentiment-snapshots/{snapshot_id}`：返回上述元数据与原始payload，内容SHA不符409；找不到404。两种GET只读SQLite，不重算当前行情、不联网、不回填旧快照。它们证明某份复盘内容在留存时点已被记录，不证明该内容在历史T可用、来源当日首次发布或预测得到验证。

## 个股/指数图表

个股/指数 `/instruments/{id}/chart` 与板块 `/market/sector-chart` 接受可选 `encoding=rows|columns-v1`，默认 `rows` 保持原响应。`columns-v1` 仅将 `bars` 换成 `bars_columnar={version:"chart-bars-columns-v1",count,fields,columns,missing}`；fields 为有序完整字段并集，columns 与字段对应且每列长度为 count，missing 将真正缺字段的行索引单列。缺字段、null、数值精度、负零、嵌套证据及全部图表元数据不得被省略或互换。前端校验维度后恢复私有行对象，兼容旧服务仍返回 rows；该格式不改写原始行情或业务快照。未支持的 encoding 返回422。

板块图表可在进程内保留最多8份完整计算结果，序列化正文合计不超过12MiB，键包含实际数据根、冻结计划、日历、观察日/周期与指标版本。每次命中仍重算入口和全部来源文件的实际SHA；同大小、恢复mtime的篡改也必须失效。返回值重新解码，调用者修改不能污染其他请求；缓存不落盘，故障不冒充行情采集失败。尚未具备完整依赖凭证的跨日历史投影保留原核验路径。指数摘要仅物化最后两行指标，完整报价序列仍用于递推预热，不改变图表或策略值。

`GET /instruments/{id}/tracking-baselines?as_of=` 只读该个股已保存的跟踪事件基准，按观察日排除更晚入池和退出信息，最多返回最近100个轮次并标明截断。默认最近仍在跟踪的轮次，否则最近历史轮次；返回原策略、轮次、冻结入池日期、原始收盘价与价格口径。全局搜索打开个股时也可选择这些基准，不创建跟踪或重算选股；从特定跟踪/自选来源打开仍优先原轮次，连续切股不沿用上一只基准。

每轮另含 `historical_event_checks`，仅投影本轮冻结 `entry_basis.entry_checks` 内的历史事件，不读取候选或行情、不重新求值。元素为 `{type:"history_event",result,reference_fields,selected_event:{date,open,high,low,close,offset,price_basis},event_checks:{result:true},history_event:{match,where?,price_path?:{reclaim:{event_field}}},evidence:{as_of,price_basis}}`。仅纳入事件条件明确满足、日期不晚于冻结入池观察日、有效有限正OHLC且原始未复权口径明确的已绑定事件；不要求其独立今日/价格过程最终result为true，保留原true/false/null，不能据此冒充新信号。where只为有界条件标签；无法按白名单读取时省略，原事件证据不推断。缺失、未知及未绑定证据返回空数组。扫描遵守既有100条件节点上限，异常超限时 `historical_event_checks_truncated=true` 并含 `historical_event_checks_reason`，不声称摘要完整。`reference_fields` 只从冻结then的all/any树抽取right_event_field，并合并price_path.reclaim.event_field，以close/high/low/open固定顺序去重；没有引用字段才默认 `["close"]`。事件日图标与每个参照字段的横线使用冻结事件原始OHLC，不能用入池收盘代替，也不能将±百分比比较阈值冒充原事件价格；百分比只在规则证据中解释。展示仍须遵守图表复权口径与锚定日的可比限制。

match接受latest、earliest、any。any仅投影整体result=true时已冻结的实际事件，标签为“最近一次已确认整体满足”；false/null不返回回退事件。它是从近到远找到的第一个已确认完整true，不表示已经证明所有较近的未知位置均不满足。较近事件失败或未知后，是否有较早的完整满足事件由原求值器决定，图表及此只读接口不重新搜索，也不根据当前策略版本改写旧运行。latest/earliest仍允许展示已绑定历史事件，即使该事件的今日或过程条件失败/未知。

`GET /instruments/{id}/chart?period=D|W|M&as_of=&price_basis=none|forward&indicator_continuity=quotes|market_sessions`。

- 返回 `instrument/period/as_of/bars/snapshot_id/quality/warnings`、价格/量额单位、实际与请求价格口径、`adjustment/indicator_gaps/quote_gaps/waves` 及涨跌停事件覆盖。
- `automatic_trendlines` 为个股、指数和行业/概念板块图表的可选展示字段。包含 `algorithm=confirmed-pivot-trendlines-v1`、`pivot_wing/lookback/instrument_id/snapshot_id/as_of/period/price_basis/items/reason`。同一已发布图表的最近 120 根内，只使用最新连续且完整可比的一段。用左右各两根完整柱确认高低拐点，各取最多一条未被后续高低价穿越、末端最接近最新可比收盘的斜支撑和斜压力。
- 每条线带 `line_type/start_date/start_price/end_date/end_price/slope_per_bar/confirmed_on` 及两个 `anchors`（各有 `date/price/confirmed_on`）。线止于最新可用完整柱。形成中的周月柱不会成为锚点或终点，缺口与无法核验的价格口径由空列表和原因表达。前复权请求回退时沿用父图实际显示的未复权 K 线，仍按公司行动覆盖与连续性分段。前端以父图表的证券、代际、观察日、周期和实际价格口径核对线条。这些线只供图表观察，手工画线、提醒、策略与回测各自保持原口径。
- `near_items` 复用主线字段，用最近 35 根可比柱的拐点形成近端线，各方向最多一条，并排除与主线近似重合的线。`zones` 含 `lower_price/upper_price/start_date/end_date/line_type/touches`，来自确认拐点的邻近价位聚集，属于水平观察区。`events` 记录近期穿线，带 `scope/state/date/price/line`，其中 `line` 使用前一完整柱已确认的旧线，止于事件日。`price` 为穿线当天的线价。事件只在图上展示。
- bar 含 date/OHLCV/amount、MA5/10/20/30/60/250、MACD DIF/DEA/柱、KDJ K/D/J；预热不足为 null。前端不另算一套指标。
- 周/月从日线聚合，形成中和不完整周期带状态；策略信号只读已闭合可比周期，图表可显示形成中柱。缺交易日报价不补 K 线。
- `trading_day_distance={anchor_date,basis:"market_calendar",state:"available"|"unknown",reason}` 为图表自己的市场日历距离证据；`anchor_date`固定本图`as_of`，不是电脑当前日期。每根bar追加`trading_days_to_as_of:number|null`，计算区间为`(bar.date,as_of]`，同日为0。停牌、缺报价不减少市场日间隔，不按K线根数、自然日或工作日猜算；已知的后续日历不会越过本图观察日参与计数。日历缺失、日期无效、重复、无序、观察日不在已留存日历或读取期间行情代际变化时状态保持unknown且距离为null；单根报价日不在该日历时该根距离为null。
- 日K悬停提示和OHLC栏显示“距行情截止日 YYYY-MM-DD · N个交易日”。周/月以聚合柱`date`的末根实际报价日计距，标为“末根报价距行情截止日”，不将周/月柱数当交易日，也不改变形成中、完整周期和缺口状态。板块缓存图复用同一字段与本次已留存市场日历，读取不联网；新生成研究冻结图保存自己的距离证据，旧报告缺字段时前端显示待核验，不请求当前日历补写历史报告。该字段仅供展示，不参与选股、跟踪、回测或指标计算。
- 默认 `none` 原价；显式 forward 仅对已核验事件做观察日锚定的本地仿射变换。证据不足回退原价并说明；不是完整分红/股数收益账户。
- 默认 `quotes` 可按实际日报价连续显示指标；`market_sessions` 严格按市场日中断。两者不改变策略、跟踪或回测口径。
- `waves` 仅为延迟确认的价格转折/骨架候选，点满足确认时间不晚于 as_of；不保证确定艾略特浪，不作自动交易信号。
- 上证 `advancers` 及广度范围按真实样本说明，不将有限可比样本称全市场完整。
- `annotation_projection_evidence={version:"chart-annotation-projection-v1",available,start_date,end_date,snapshot_id,source:"published_corporate_actions",reason}` 只读说明本图所属发布代际、日行情起点至观察日的公司行动覆盖依据。仅 available 为真、来源与代际匹配、原锚处在该范围内，且复权事件逐项可核验时，允许将已保存旧锚水平线换算到本图锚展示；区间无新增行动时线价不变。未知、缺口、未来锚或范围不含旧锚均保留隐藏原因，不能据此声称完整公司行动历史。换算不改保存价格、原锚、提醒版本或既有事件证据。

图表前端的 BOLL 仅用于显示，不增加或重算上述接口的 MA/MACD/KDJ 字段：基于返回且口径相符的连续可比收盘计算20周期均值及上下2倍总体标准差（方差除以20）。独立副图用同一组返回 OHLC 画小蜡烛，上/中/下轨分别为红/青/绿，不再叠加额外收盘折线；缺 OHLC 不填造蜡烛，轨道不足20根或连续性重置仍为空。图下开关、偏好面板和已开启副图统一按成交量、BOLL、KDJ、MACD排序，指数广度随后；个股/指数原有显隐偏好和接口参数保持不变。研究冻结图复用相同呈现，只使用报告已留存的行情，不借切换副图请求当前行情。

收盘涨跌停通过 `limit_events` 与逐方向 `limit_event_coverage` 返回；日 bar 的状态和证据区分 confirmed/source_absent/unknown/conflict。正事件必须通过来源身份、日期及原始收盘核验，不按涨幅 9.9% 猜，不用炸板触价当收盘涨停。空池、隔离、错价不能证明 false。周/月只汇总确切日事件，不称整周/整月涨停；指数不适用个股标记。显式前复权只改变图上锚点，不改变原始事件日期和核验价格。

个股正式日K的 bar 可附 `limit_down_price_reference={price,rate_pct,previous_raw_close}`，数值为原价人民币元与百分比。严格前一市场交易日的有效未复权收盘价按当日可用的常规板块档位计算，应用分价取整与最小价差，须与同日未复权收盘和最低成交价精确一致。要求已核验上市日期且至少5个先前上市后市场日、当日无公司行动证据明确及无板况冲突。候选档位舍入歧义、当日明确特别处理或已有确证板况时省略该字段。来源池未收录或覆盖不足时可独立展示「跌?」，原事件状态、计数、策略与回测口径照常。仅日K提供，图表以当日显示低点放置标记。

个股成交日形态另外使用 `pattern_events/pattern_coverage`，日 bar 带 `pattern_events/pattern_status`，与收盘池状态分开；不会将地天板同时记为收盘跌停，也不新增选股、提醒或回测信号。原kind为 `earth_to_sky/one_price_limit_up/one_price_limit_down`，沿用basis `verified-source-close-pool-and-raw-ohlc-v1`；新增展示kind `broken_board/resealed_board`使用basis `verified-dated-close-pool-board-events-v1`。事件包含具体 `date/bar_date/snapshot_id/raw_ohlc/raw_low/raw_close/raw_volume/raw_price_basis:"none"/chart_price/sources/evidence`。状态分别为 confirmed、not_matched、unknown，指数为not_applicable；来源不足、身份或价格矛盾时不补猜。

- 一字涨停/跌停：当日有效成交量为正，未复权开高低收按人民币分精度全等，且同日同证券对应收盘池的所有正证据精确匹配原始收盘价。仅证明全天已成交价格相同，不能称全天封单未打开。
- 地天板：上述同日收盘涨停已确认，且有效最低成交触及派生跌停价。派生须有严格前一市场日的有效原始报价、已核验上市日期及至少5个先前上市后市场日、当日公司行动覆盖已知且无行动；用已确认涨停价唯一匹配5/10/20/30%对称档，按分四舍五入并应用最小一档价差，再计算跌停价。不按当前ST名称或证券代码推断历史涨幅档。无法唯一匹配、OHLC越界或前日缺数均unknown。`evidence.limit_band`明确标为派生参考价，并保存档位、前收、前交易日、上市日期、取整和公司行动依据，不冒充交易所直接提供的限价字段。
- 旧收盘事件证据只增加 `observed_prices/pattern_validation` 及可用的原始来源SHA元数据，原 `limit_*` 状态与数值不变。形态比旧收盘池核验的容差更严格，不接受一分钱差异。
- 周/月bar增加 `pattern_summary`（各类count/unknown_days及逐日统计范围），事件保留具体日；不画“整周一字板”。前复权仅改变图上位置，原始判定和日期保持不变。旧冻结研究图缺少这些字段时不推测、不请求当前行情回填。
- 炸板/回封：炸板只用指定日`reference.breakpool`或`pool_history[day].breakpool`的同证券原事件，并精确对账同日未复权有效成交收盘；最高价触及参考涨停价不构成证据。回封须独立确认同日收盘封板，且已核验来源`open_times`为明确正整数；缺失/非法/0与正数冲突不补猜。封板与炸板正向来源同时冲突时两种新标记保持unknown，不改变原涨跌停字段或任何策略定义。新kind沿原已加载图表窗口与as_of截止，不扩大历史采集；周/月仅聚合周期内具体日事件，复权只改变`chart_price`放置，证据仍用原价。
- 盘中/午评单股quote的已校验`quote`增`limit_up_state/limit_down_state/pattern_events/board_transition?`。同快照正向来源池与报价核验后才标“盘中炸板”；回封须同档`open_times>0`，或严格相邻且在本档前已可知的原SHA快照证明先炸后封。同档冲突或缺档不回填，单纯封板不等于回封，零成交不画事件。临时事件`basis=verified-intraday-snapshot-board-events-v1/is_intraday=true`，附原快照ID/SHA/实际采集时刻；`chart_price:null`由前端按临时K线当前显示口径放置。临时K不写正式日线或策略/跟踪/回测。
- `GET /instruments/{id}` 的 `pattern_as_of/pattern_date/pattern_events/pattern_status/pattern_coverage`仅核验观察日，与日K一致；无当日报价时pattern_date为空且不沿用最近报价日。头部不受悬停及日周月切换影响。图上沿用涨跌停标记开关，头部仍显示所选日期已确认形态。使用与规则详见[成交形态标记](../docs/个股成交形态标记.md)。

## 统一个股统计

个股及已留存大盘指数图表支持独立的个人水平线：`GET/POST /instruments/{id}/annotations`，`PATCH/DELETE /instruments/{id}/annotations/{annotation_id}`。价格、名称与颜色写入业务库，不修改来源K线、指标、策略或跟踪状态。每个标的最多64条；未复权水平线跨日/周/月共用，前复权水平线固定记录锚定日。切换价格口径时隐藏不相符的线；切换前复权锚时仅按上述同代际、有界覆盖证据换算展示，其余在管理列表保留隐藏原因，不能直接把原价水平线画到复权价格轴上。指数仅使用原始点位，`alert_modes=[]/auto_watchlist=false`；服务层和SQLite约束共同禁止其进入个股提醒，规则消费显式排除指数。板块图不使用此个股/大盘入口。

`POST /instruments/{id}/annotations/recommendations` 由用户手动计算本地支撑压力候选，不联网、不创建任务、不写行情或业务标记。参数为 `{period:"D"|"W"|"M",as_of?,price_basis:"none"|"forward",lookback:20..240,snapshot_id?}`；默认D/120根/已发布尾日/未复权，UI使用当前图表周期、观察日、实际口径及代际。需要已发布范围内的交易日，版本变化返回409，前复权不能暗中降级。仅读取所选证券的已发布日价，周/月聚合为完整周期；不会计算或读取全市场广度、策略或未来价格。算法 `confirmed-pivot-cluster-v1` 使用左右各2根完整且连续报价确认严格高低分型，再将同侧最大跨度0.8%内的价位取算术均价；相对观察日最近已发布日收盘分别保留最近的至多3条支撑和3条压力（reference_date明确日报价日期，周/月未完成柱不参与分型确认）。日线缺报价、周/月缺周期或未完成周期阻断确认，未复权公司行动重新起算可比区间，个股公司行动覆盖未知时不生成可比候选。无候选返回明确reason，不编造点位。

预览返回冻结的 `parameters/preview_id`、`reference_price/reference_date/window_start/window_end/observed_bars/complete_bars/algorithm/pivot_wing/cluster_pct/notice/reason` 和 `items`。每项包含 `candidate_id/line_type/price/distance_pct/touches/last_pivot_date/confirmed_on/evidence/price_basis/anchor_date/origin/saved`，evidence逐项保留分型日期、价格及右侧确认日期。`POST .../recommendations/save` 接受 `{parameters,preview_id,candidate_ids}`（1至6项），服务端按冻结上下文重算并校验后短事务原子保存；`origin_key`幂等，不覆盖手工线或已修改的自动线。返回 `created/reused/total_saved/alerts_enabled:false`。保存仍使用普通annotation，但默认不启用提醒或自动自选。`origin`持久化算法、原候选价、观察日、周期、来源行情snapshot_id、窗口及分型证据；后续手动改价或类型标记 `manual_override:true`，不抹去来源。重算只改变临时预览，刷新后已保存线仍在，预览不跨证券/周期/观察日/代际沿用。

`GET /instruments/{id}?as_of=` 是搜索、盘面榜单、行业题材成员、自选、策略和跟踪共同使用的详情入口，图表偏好共用同一个股存储键，调用入口不提供另一套题材或报价。头部从该详情的真实未复权日收盘及实际报价日期读取，明确非实时；悬停历史K线或切换周/月不改变头部日收盘。观察日无报价时应标明最近实际报价日期或保持未知，不能将旧报价冒充当日成交。行情摘要仍由下述 summary 接口按观察日核验。

详情新增 `current_topics/current_industries/current_topics_observed_date/current_topics_scope`。分类项为 `{topic_id,name,kind,source_observed_date,members_complete,origin,manual_override,provider_member}`，与自选列表共用当前本地题材目录和人工增删语义；`current_topics_observed_date` 是当前业务关系有效日，每个来源采集日另列。无论图表回看哪天，该组都明确标为“当前归属”，不能当作历史成员、当日主线或上涨原因。原 `topics` 仍按详情观察日可用性返回，历史缺档不回填当前分类。此分类读取只读SQLite和本地来源目录，不读worker活动DuckDB、不联网、不创建研究/策略/跟踪；人工修改下次读取生效，不跟随行情缓存固化。空数组只表示暂无已知正归属。`GET /stock-topic-memberships/{instrument_id}`返回同组字段及证券ID，供详情手动补充后轻量刷新，不读历史报价。详情“补充题材”复用`POST /topic-members/associate`原子追加已有归属；新建使用`POST /topics`携带单股instrument_ids同事务创建与关联，失败不留下半成品。新建题材和人工加入均明确标记人工来源，当前业务日生效，不回填历史。

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

当日涨幅另有 `change_pct_reason`，缺观察日报价、前一市场交易日报价、公司行动覆盖，或观察日存在未复权公司行动时保持未知；不改用更早的可见报价，不随图表周/月切换改变为周期涨幅。其余各组另有 reason/coverage。近30交易日和年内收益保留原始市场交易日端点；端点核实为全日停牌时，最多向前核对20个交易日，连续停牌均有证明才沿用停牌前实际成交的收盘价。成功沿用时，coverage 额外提供 `start_price_date/end_price_date` 与 `start_suspension/end_suspension`，当日行情仍保持原状态。公司行动不可比、报价与停牌冲突、重复报价或缺口未核实时，收益保持未知。月内收益、周月K和严格交易信号维持原口径。次数只统计不晚于观察日的原始收盘核验事件。兼容别名 `annual_return_pct/annual_limit_up_count/latest_limit_up_date` 等不改变语义。成员与个股详情复用同一统计服务，无 N+1 来源请求。

个股摘要与当前、历史成分股行在观察日已有全天停牌证据且无实际成交时返回 `suspension`。当日收盘、涨幅与报价日期为空，`quote_available=false`。近30交易日或年内涨幅使用了停牌前收盘时，界面显示相应端点的实际收盘日期。

## 板块 K 线与成员

- `GET /market/sector-chart?topic_code=&date=&period=D|W|M` 返回通用 chart shape 加 `available/task/unavailable_reason`。已缓存且核验通过的板块行情支持 `automatic_trendlines`，按指定来源代码的指数点位计算，独立于历史成员可用性。周/月只使用完整可比的柱。未缓存时 bars 为空，读取不联网。
- `POST /market/sector-chart/jobs {topic_code,date?,request_key?}` 手动加载单板块；返回标准任务或 `{cache_hit:true}`，随后重读 GET。只加载该来源代码，不扫描全库。
- 历史缓存须先复验原始全段与 SHA，再按目标日截断重算聚合和指标；不得从未来已聚合结果切片。板块是原始指数点位，不做个股复权/涨跌停标记。
- 新手动盘后增量额外冻结 `sector_tail_request_policy_version=sector-tail-pacing-v2`，沿父任务传给板块增量子任务，并进入批次 `request_signature`；逐板来源窗口、身份、缓存签名与来源SHA不因节奏变化而改变。旧父/子任务缺省该字段，恢复、重试与后续创建子任务仍沿用旧节奏，不自动补版本。首次全库、单板块加载、主行情和盘面补充不启用该策略。
- 新节奏在唯一worker中保持串行，同批次扶摇历史指数端点的所有实际HTTP发起至少相隔0.6秒；最多800个逻辑请求，每个逻辑请求仍最多3次传输尝试。HTTP 401/403/429 或业务2001/2003/4001立即停止新增请求，不连续重试；后续缓存仍复验，未请求及失败各自保留。其他暂时错误保留有限退避；节奏等待与退避期间检查暂停、取消和代际guard，作用域退出还原默认策略。旧任务保持来源请求完成后1.2秒间隔。
- 新批次报告保留 `sector_tail_request_policy_version`，coverage增加 `request_policy_version/request_interval_seconds/request_interval_anchor/maximum_inflight/provider_transport_attempts`；状态读取兼容新旧签名，仅读小清单。此为0.1.9新增契约；短来源样本不构成710板块整链耗时承诺。
- `GET /sectors/{topic_code}/members?as_of=&membership_scope=latest|historical` 无需跟踪轮次。latest 仅用当前业务日报价，显示真实成员采集日；historical 严格查历史留存。无档保持未知，不按名称猜成员。
- 成员返回 `membership_as_of/quote_as_of/members_complete`、board 和上述收益/事件字段；`stock_entry_baseline=null`。展开成员不会创建个股跟踪，也不会把板块入池点位套给股票。

盘面成分股随选定日期读取。最新已发布日且来源代码明确时调用 `membership_scope=latest`，并传同日 `as_of`，防止并发发布后混用报价。较早日期调用所选日的历史成员接口，缺档保留未知。成员采集日与行情日分别展示，股票K线导航使用返回的 `quote_as_of`。当前成员需要明确来源代码或已核验唯一代理身份，不能按名称猜测；切换不联网、不排队、不补写历史。完整初始化只采集当时的当前成员，不补造五年成员变迁；日增量沿用成员和原采集日，完整成员更新仍需用户手动刷新。使用说明见[板块行情与成员档案](../docs/板块行情与成员档案.md)。

精确字段与校验入口见[平台路由](../backend/app/routers/platform.py)、[工作区路由](../backend/app/routers/workspace.py)及[市场服务](../backend/app/services/market_service.py)。

## 报价缺口的显式来源核验

`GET /data/quote-gap-source` 只读当前本地核验计划与已保存的小摘要，返回 `available/plan_id/diagnosis_id/snapshot_id/stock_count/missing_sessions/is_current/stale/evidence`。`evidence` 包括计划与已核验股票数、失败数、逐缺口核验日数、来源未返回报价数、发现可补实际报价数、重叠报价差异数、时间、任务ID；不查询远端、不扫描全部日K、不修改快照质量。

`POST /data/quote-gap-source {plan_id,request_key?,instrument_ids?}` 显式创建 `source_check / quote_gap_source_check` 任务。省略股票范围则核验当前报告全部缺口股票，提供时只能选择该报告中的股票。计划冻结行情代际、身份版本、本地诊断ID和凭据版本；变更后旧任务不继续提交。请求只覆盖含缺口的短日期窗口，保留上下文报价作对照，严格验证股票、原始日K口径、OHLCV和量额。逐股原始响应与SHA验收后可幂等恢复，暂停/取消及旧租约阻止提交；失败保持待核验。

完整本地核验视图在同一 `source_signature/snapshot_id/diagnosis_id` 下将已保存证据附到每段 `source_verification`，不改写原本地核验文件。状态区分 `source_no_quote/local_missing/partial/failed`，附本次未返回与有效返回日期、响应证据与下一步。`source_no_quote` 只说明本次来源未返回该日报价，不证明停牌；`local_missing` 只表明有可进入单独发布核验的实际报价。此操作不修改当前不可变Parquet、不填造停牌K线，也不把出现率伪报为100%。

## 图表高低涨幅（0.1.6）

个股、指数与板块图表的bars新增`chart_high_change_pct`、`chart_low_change_pct`、各自的`_reason`、`chart_extreme_change_period`和`chart_extreme_change_reference_date`。使用同图价格口径及同锚前收，值为百分数；价格继续独立保留。日为严格前一市场交易日，周/月为前一完整可比周期。形成中或缺口不可比返回null及原因；旧冻结图未带字段不由前端猜算。该生成由`include_chart_extremes`图表开关启用，原选股`intraday_high_change_pct`及默认指标输出保持原契约。

### 涨幅榜未排名范围

`GET /market/gainers` 新增 `rank_state=all|unranked`，默认all。unranked不能同时传top_n，其他页面不得使用该范围。summary含available_unranked_total、available_ranked_total、rank_scope_total、rank_state。未排名行保留真实报价与原因，rank及无法核验的收益为null。前端默认前100名，输入搜索时不传top_n，以便在全市场发现含未排名的证券；清空搜索恢复所选榜单范围。

最新公开页面的可选 `classification_reference`、固定前1000统计和 AI 日期边界见[涨幅榜分布契约](gainers-distribution-v1.md)。

收益期间仍使用市场交易日T/T−30。任一端点须经留存证据核实为全天停牌，才可跨过连续已证实的停牌日，向前最多20个市场交易日取唯一正成交的实际收盘。盘面日停牌显示`return_30d_observation_suspended=true`且当日涨跌幅为空；由停牌前收盘计算的收益带`return_30d_quality='verified_suspension_endpoints'`及`return_30d_start_quote_date/end_quote_date`。停牌前收盘不写成所选日K线，普通缺档、证据冲突、超出范围或公司行动无法核验时仍保留未排名。仅对证据覆盖端点的证券追加有界批读，不改变策略、跟踪及回测。

手动水平线新增默认色：support=green、resistance=red、reference=violet。客户端仅提供这三种新选择；原blue/amber仍兼容保存、读取和渲染，编辑类型或主动选色才改变已有颜色。配色不改变原价、锚、提醒规则或事件证据。

## 龙虎榜与买卖席位（0.1.11）

以下路径沿用 `/api/v1` 前缀。盘面共享观察日，不创建实时轮询或自动日更任务。

- `GET /market/dragon-tiger?date=YYYY-MM-DD` 只读取本地小索引和所选日不可变对象，一次返回该日三类榜；省略日期为当前最新已发布行情日。返回 `date/snapshot_id/collection_snapshot_id/state/availability/boards/labels/coverage/warnings`。`state=ready|partial|unknown`；`availability.available=false`、原因和空boards表示未留存或校验失败，不能当成真实零上榜。读接口不联网、不读取凭据、不打开worker正在写入的DuckDB。
- `boards.all/org` 为 `{items,source_count,source_stock_count,state,source,date,source_concepts_basis}`；`boards.hot_money.items` 为 `[{name,rows:[stockRow]}]`。`source_count/source_stock_count` 原样保留来源统计，不用规范代码缺失把不同标的合并。all/org仍核验count等于实际股票区间行数；hot_money的count为上游统计，真实响应中不一定等于标签展开行数，stock_count也不能当标签内股票去重数，二者对应口径尚未核验，禁止据差额推断具体漏股或补造记录。新增 `source_count_basis/source_stock_count_basis/statistics_state`；hot为unverified并带partial状态和warnings。实际返回的 `observed_label_count/observed_label_row_count/observed_stock_count/observed_stock_range_count` 分别报告标签数、标签行数、按来源代码去重股数与股×区间数（包含未匹配A股名录的原始标的）；展示可用A股数量由前端按已核验instrument_id与区间另算。`labels` 提供名称及 `stock_count_by_range:{"1":n,"3":n}`，范围分开，不加总。
- `stockRow` 含 `instrument_id/code/name/source_instrument_id/identity_state/identity_reason/range_days/source_range_days/range_state/range_reason/change_pct/buy_value/sell_value/net_value/org_net_value/hot_money_net_value/label_net_value/label_name/source_concepts/source_concepts_basis/source_limit_reason/source/date`；机构买卖数在来源提供时另有 `org_buy_num/org_sell_num`。`source_range_days`保留来源明确正整数区间；1/3映射为可展示range_days，其它合法区间（例如严重异常期间10日）为range_days=null、range_state=excluded，并在board.excluded_range_row_count及warnings说明；缺失、非整数或非正数仍拒绝。去重键与observed_stock_range_count使用原source_range_days，不能把同股10/30日投影null后合并；非1/3行不进入两个榜的筛选、汇总和导航。金额为人民币元，`change_pct` 已是百分数（如2.3表示2.3%），前端不得再乘100。来源概念资料不证明指定日历史成员，不能当作历史涨停主因；`source_limit_reason` 也不是龙虎榜披露原因。
- `instrument_id=null` 表示未匹配本地已核验A股名录，保留原始六位代码、名称及原因，主A股统计不得计入；名称、前缀或空标准代码不用于猜测身份。单日 `range_days=1` 与三日累计 `range_days=3` 独立筛选，窗口累计不能混加重叠三日榜。全部记录按日一次返回，前端对当前筛选全量排序后分页；名称、涨幅、净额等可排序，unknown始终末尾；K线上下只导航基于筛选后去重的全部股票，跨分页，不限当前页。
- 游资未选标签时，前端按股票和区间去重，以股票榜单净额展示，保留全部关联标签；选标签时才使用对应 `label_net_value`。再点同一标签取消。不得把多标签中的股票总买卖额重复累计，也不把机构与游资凑成互斥总额。

`GET /market/dragon-tiger/{instrument_id}?date=YYYY-MM-DD&range_days=1|3` 返回 `date/snapshot_id/instrument_id/name/range_days/state/records/availability/warnings`。标的必须在已核验A股名录，不能利用通用instrument fallback将未知代码标成股票。`records` 保留同股同日多条披露原因，每项有 `record_id/reason/range_days/range_evidence/summary/buy/sell/source/reconciliation/coverage`。`summary` 的 `buy_value/sell_value/net_value` 是该条公开披露记录的来源汇总，与扶摇股票区间汇总分别留证；差异在 `reconciliation` 显示，不凑平。点击股票展开席位并定位；多原因按record_id选择。

席位先识别“严重异常期间”等累计范围，再识别明确连续三个交易日与普通单日原因，不能因累计原因内出现“日收盘价格”误当日榜。未能确认1/3的披露保留在本地periods.unknown，原原因、CHANGE_TYPE、日期、买卖席位及金额均留存，不从来源后缀猜区间、不跨范围对账。当前席位接口不新增其它区间Tab；若股票仅有累计或其它范围，返回空records、partial及明确reason，另有unclassified_record_count。coverage.unclassified_disclosures/excluded_source_period_rows披露该日未分类原因与来源其它范围行数。

每方向行含 `name/source_id/buy/sell/net/raw_net/amounts_complete/rank/source`。买方按实际买入额、卖方按实际卖出额降序，不按名称或通用机构代码0合并；同一席位可同时出现在买卖榜，两表不能简单相加。全日来源分页总数验证完成后，按实际0至5行展示，不补造不足五行；某披露汇总的两方向均未取得任何席位时，该批次不能标为完整。对手方向未披露的金额为null，可核对净额也为null；即使来源NET字段将缺失方向按零计算，仍只将它留在 `raw_net`，不将其当作真实可核验净买额。所有已知买卖净额按元对账。

源内金额冲突采用同一规则：扶摇股票行、公开披露summary及买卖席位行的买卖与来源公布净额均已知但差额超过0.011元时，保留原始buy/sell，展示net_value或net为null，并分别在raw_net_value或raw_net保留公布净额；amount_check=conflict、amount_check_evidence包含精确十进制字符串buy_minus_sell_minus_reported_net/unit=CNY/tolerance=0.011。不得扩大容差、把净额补0或重算冒充来源值；买卖负数、非有限金额、错日期和错类型仍拒绝。冲突仅令相关字段未知并追加warning，不拖累其它明确记录或整日响应留存。board.amount_conflict_row_count与席位coverage.conflicting_amount_rows/conflicting_amount_summaries独立计数，不把冲突行混同对手方向未披露行。summary因此另含raw_net_value/amount_check/amount_check_evidence；前端展示其可用字段与冲突说明，不把原始冲突net当可信净买额。

`POST /market/dragon-tiger/jobs {date?,request_key?,backfill?:boolean}` 显式创建标准 `source_check/dragon_tiger` 任务，返回含 `task_id/job_id` 的标准任务视图，可通过 `/tasks/{task_id}` 查看、暂停、取消与手动恢复。默认backfill=true，冻结当前最新已发布行情日、本地实际市场日历最近30个交易日（含锚日）、行情代际和 `dragon_tiger_policy=dragon-tiger-v1`；backfill=false只取当日。历史观察日不允许另建更早锚或触发清理，须切到最新行情日。旧盘面补充成功回执不阻止此独立入口。相同request_key幂等，完整成功范围拒绝重复采集；窗口有缺口时允许手动补齐。页面读取和恢复服务本身不创建取数任务。

`coverage` 含 `anchor_date/retained_dates/expected_dates/missing_dates/retained_trading_days/complete/can_backfill/available/state/reason/failures/latest_market_date`。complete只表示窗口内逐日六类响应已留存并通过其可验证的日期、类型和数据校验，不证明上游统计口径或未披露字段完整；reason明确“响应已留存，来源统计与未披露字段仍可能未知”，不能将游资count恰好等于展开行数当作完整性证明。expected_dates按最新已发布市场日历取30日，失败不使用更早日期凑满；anchor_date仅由成功验收发布的龙虎榜日推进。新首次初始化链冻结backfill=true，新盘后增量链和新手动盘面补充冻结backfill=false，marker经题材与板块K线链传至唯一worker补充阶段；旧冻结任务无marker则完全不增加龙虎榜范围。独立补齐任务固定具体日期数组，执行时行情代际变化须重新核验，不自动改计划。

0.1.11已接受的逐日CAS读取时对hot统计及席位范围做只读投影：从已有items.rows计算observed、统一标未核验并追加warning；席位按已留存原reason重新分类，发生范围更正时附previous_normalized_range_days/range_projection_basis，累计原因不再留在单日榜。不改旧文件、来源SHA或已冻结任务，不联网重抓。组件复用仍按当前adapter核验原响应。

唯一worker按日期采集扶摇all/org/hot_money及东方财富买方、卖方、披露汇总全日报表，使用有界连接复用、分页和重试，不逐股打开时联网。实际来源日期不等于请求日不能验收；来源明确的空集与失败分开，缺口保持unknown。hot来源统计口径未知不会阻断有效标签及其它榜/席位留存；若hot真正请求失败、日期/类型错误或行金额等强校验失败，本版仍将当日视作未完成，已验证all/org等组件保留为可恢复CAS，后续显式补齐复用组件重试，不标成完整后永久跳过，也不伪造hot空列表。每个已验收日期保存独立不可变CAS及全部来源SHA，只用小索引定位该日。新日验收并发布后，才按最新成功龙虎榜日的30个市场交易日窗口清理；失败不先删旧资料、不推进清理锚。窗口内已取得组件可幂等复用，显式暂停/取消不自动继续。此清理仅限龙虎榜命名空间，不触及五年K线、其他补充250日或策略、自选、跟踪。

补齐时，若未验收日的东方财富买方、卖方及披露汇总三份缓存均通过各自校验，但披露记录集合无法匹配，worker 会同批重新取三份公开报表一次。新三份报表通过原有校验和记录集合核对后才一起绑定到当天归档；核对仍失败时保留缺口、原组件和已验收日。已验收日期与有效扶摇组件继续复用。

标准backup/restore包含龙虎榜head→CAS索引→逐日对象→来源对象闭包，以及六类可恢复组件指针；文件SHA、内容身份、日期、版本与稳定引用均核验。备份不遍历全历史CAS目录，恢复除文件清单外再检查来源依赖闭包。主密钥仍外置，原始响应、真实验收截图和运行数据不进入公开Git提交。

## 盘中观察快照

板块成分单股没有当日可核验报价、且同日有明确全天停牌证据时返回 `trading_status='suspended'` 及 `suspension`（日期范围、证券、来源和链接），当日行情字段保持空。午评和竞价提供同日 `stock_suspensions` 投影，午评成分明细逐行附 `suspension`；源行情快照及 SHA 保持不变。有真实成交的证券不标为停牌。唯一 worker 在已授权的新竞价、午评或盘中快照留存后，同日有界读取一次公开停复牌表并保留原始回执与摘要；旧冻结任务、普通 GET 不联网。来源日期区间、证券代码或全天时间不能核验时继续显示未知。

- `GET /api/v1/market/intraday?date=YYYY-MM-DD`只读本地，返回日期、当前时段及手动采集是否可用、最近任务、小型时段目录和当日已留存榜单。未留存的时段不会取数或回填。
- `noon_scheduled_at` 是北京时间 `HH:MM` 的午评自动采集计划时刻。有当日计划回执时优先读取回执（`noon_schedule_source=recorded`），否则仅今天且启用时读取配置（`configuration`）。历史没有回执时留空；该字段与实际采集时间、午间收盘时刻独立。
- `POST /api/v1/market/intraday/jobs`只接受`date`和`request_key`。新建 v7 使用北京时间 09:35、09:45、10:00、10:30、11:00、13:30、14:00、14:30 八个时点。`0935` 为“开盘观察”，`0945` 为“早盘跟踪”，各自创建窗口为 `[09:35,09:36)` 和 `[09:45,09:46)`，采集窗口为 `[09:35,09:38)` 和 `[09:45,09:48)`。原六档仍在第一分钟创建、三分钟内采集。手动与自动共享按日期/slot唯一名额。v6保留含09:35的七档，v5保留原六档，v1–v4仍使用各自原`opening/morning/afternoon/late`长窗口。版本与窗口显式匹配，未知版本拒绝，旧任务按原冻结范围执行。新档只使用本窗口实际采集。已有旧日期仅展示原计划与真实快照。
- `GET/PUT /api/v1/market/intraday/automation`读写独立开关，PUT 接收`enabled`与`expected_revision`。启用后唯一 worker 在既有自动任务之后检查，使用独立的已核验交易日回执。备份恢复默认关闭该开关。
- v7自动计划只在八档各自第一分钟、已核验交易日、业务无锁、主行情已发布且唯一worker空闲时创建；新增0945复用相同幂等规则、日历回执、来源预算和任务停止守卫。显式暂停/取消仍占据原时点名额，不自动重建或继续；过窗不补造。竞价、午评及盘后原计划和开关不变。GET配置、历史、成员、简报和AI工具只读，不触发新一轮取数。
- GET 盘中观察补充`briefs`和`anchors`。每份简报绑定截至所选时点已知的事实编号、来源及缺档时点。收盘使用已正式发布的同日快照。模型选择事实编号；数值由后端渲染，本机模型失败时返回已核验事实。
- 新计划的累计简报顺序为 `auction(09:25)→0935→0945→1000→1030→1100→noon→1330→1400→1430→close`，保留竞价原证据。档位缺失、晚收或晚发布时按目标时点标缺档，正文、ID、SHA及完成时间均可核验才产生该段。v7 的0945以0935为相邻基准，1000以0945为基准，缺档时保留未知。旧快照按原版投影，v6 的1000沿用0935，v5原冻结比较输入保持原样。
- AI `read_intraday_data`样本保留同档核验的`limit_down_state/board_transition`、上市板块及最多一行业/两题材（名单日单列），不把缺证据当正常或0；方向返回上涨占比分母、覆盖状态以及`breadth_reason/broken_count_reason`。原`query`可查完整已留存指数报价，计算仅限匹配方向及原短榜；成员证明从所选只读SQLite发布锚点与原代际reference/分片SHA核验，不接当前head。每次请求复用原成员证明与同档报价索引，不设跨轮缓存，不调用完整页面API。股票/方向条数保持原预算，单档10KiB、各档摘要3KiB、工具总24KiB；超预算先整条省略可选样本并提供`omitted_for_budget`，保留时段、计数、日期和unknown，不截断JSON。
- AI `read_market_data {date,section:noon}`自动形成同一只读恢复源、同一天的竞价→0935→0945（适用日期）→1000→1030→1100→午评前缀。`summary.noon_context.cutoff_at`为原午评实际`collected_end_at`，`phases`列明每档状态；盘中档自身SHA、日期、版本窗口和`finished_at_ms≤cutoff`均须核验，午评后才完成的旧时段不补入。缺午评正文/权限/截止时点不借其他资料；旧午评缺上午档时保留缺失说明。证据按时序独立返回原source ID/SHA，`source_bindings.noon_morning`保存上午绑定；不读取下午/收盘行情、不跟当前head、不触发采集或付费模型。每轮工具调用重新核验实际来源及同一午评截止，不沿用上一轮结果缓存，累计结果继续不超过24KiB。
- AI `read_market_data {date,section:close}`自动串联同一只读恢复源的原竞价、截至15:00已完成留存的当日计划内盘中观察、原午评及正式收盘；`summary.close_context`提供`cutoff_at/published_at/phases`，0935等旧日缺档、15:00后才完成的盘中档明确缺失，不补入后续资料。新增`closing_pools_brief`从有界已存close简报锚点定位其原正式代际，直接核验两日月Parquet、原来源池、公司行动及manifest/reference SHA；不调用完整盘面页面、不用当前代际替换。`pools.limit_up/limit_break/limit_down`分别提供核验状态/家数、最多3只原股样本及省略数，另有高度摘要与严格14:30→close变化计数；缺14:30仅变化未知，原收盘池仍保留。`source_bindings.closing_pools`保留原代际、正文/参考/月行情/公司行动SHA与真实发布时间，`close_intraday`保留盘中来源；无原锚点或来源损坏、日期/AI权限不符时保持unknown。该收盘证据数据≤2304字节，超预算只整条省略可选股样本并明确标记，不能拿截断名单推称全池；原午间至收盘交集证据独立保留。
- AI板况组的`classification`按两档原池与实际报价交叉核验后的完整已确认变化代码集合统计，再返回最多3项行业/概念top及`total/classified/missing/state`；不以服务32行或AI3股样本估算占比。概念可重叠，`missing`表示缺原归属证据，不等于无行业/题材。30日/年内收益、最近涨停和一年次数不属于盘中快照，当前轻量工具返回`historical_metrics_state=not_in_snapshot`及说明，不临时读取当前年度行情或伪称已支持。
- 午评/全天累计仍受24KiB上限：先整条省略可选股票/方向/指数样本并记录省略，再可省略与证据正文重复的metric目录项（数字仍留在证据），不删除已知时段。必要时使用`summary.phase_fact_columns`共享列名，逐档`values/rows`无损保存原数值、状态和null，单位见`fact_units`、日期继承顶层`date`；同一原成员证明放入`source_bindings.member_reference_sources`并由`member_reference_ref`引用，相邻已列档可用`source_slot_ref`引用原ID/SHA。`compact_phase_note`解释该格式；只压缩重复字段名/来源，不截断JSON或填零。必要来源和计数仍放不下时返回明确容量错误，提示逐档读取，不能静默丢掉尾盘或正式收盘。
- 收盘锚点的梯队与炸板明细在GET中按原`anchor.id`正式代际只读恢复：`intraday_close.closing_data`仅核验该代际manifest/reference、所选日及严格前日月行情对象SHA和公司行动，来源池事件价须匹配同日未复权正成交量收盘；不读当前head补旧日。`ladder.groups`与`broken_board.items`恢复有界原名单，缺高度保留`board_count:null/label:高度待核验`，原零高度不视首板；有完整空池回执才给0，缺失为unknown，交叉池冲突样本排除。`source_binding`含原代际ID、manifest/reference/月行情/公司行动SHA及真实发布时刻，收盘15:00是行情所属时点，采集/发布仍分别留证。`board_changes`支持严格14:30→close同来源对照，沿用五类定义与32只详情上限；旧14:30无跌停池或炸板样本不全仍unknown/下界，不从缺席推断开板。只读2MiB临时板况投影的SHA另记`projection_sha256`，不冒充原source SHA；不回写旧brief/body、capture或扩大原384KiB归档预算，不新增表/网络/采集任务，也不改变策略/跟踪/回测的日线事件定义。
- 简报读接口额外提供`brief.sections`，按该简报时点的实际可知截止时间返回累计时段前缀，且逐档匹配原`evidence.source_id/collected_end_at`；同slot替换身份、晚收或原简报未使用的资料不补入。每请求复用同来源段落计算：`slot/label/status/summary/highlights/comparison_note?/source_bindings`。每段最多8条重点：当前领涨2方向、严格相邻档相对走强/回落各2方向、封板及炸板各1条；每方向/条目最多2只已核实代表股，原`body/context/evidence`与已存正文不改。方向比较要求同日、同代码/类型/来源、人民币未复权、相同前收且价幅对账；指数真实价格不因缺成员消失，但无原成员凭证不填代表股或封板家数。板块封板增减另需同成员SHA、名单完整且两档池完整；部分池只能陈述下界，不相减。很小的正涨幅回落表述为“涨幅小幅收窄”，高度未知只称封板，不猜首板。午评段的`label`及比较文字取原自动计划回执时间，无回执只显示“午评”，不改原正文时间。封板段另可附`current_sector_counts`（`sector_id/name/kind/count/state`），从当前原轮动投影中优先窄行业、再具体题材列最多3项，宽口径成员超过300只的属性方向不参与此小排名，部分计数显示下界；不称全市场最多。封板/炸板段可附`topic_counts/listed_stock_count/classified_stock_count`，仅统计本段变化组已列名单中的原冻结行业/题材（最多3项），同股同标签去重，概念可重叠，不能把截取样本说成全池分布。`highlights`含`id/kind/text/sector_id?/stock_ids/stocks`及对应原数值；`kind`为`leading/strengthening/weakening/sealed/broken/auction`。当前与相邻原来源分别绑定快照ID及SHA；缺相邻档不跨档推断，未来或晚于目标时点才留存的资料不得回填。读取只访问有界原正文/原代际，每请求复用报价索引与轮动投影，不追加取数或模型推理、不改采集预算/原384KiB简报存储上限；AI聊天工具继续使用原3/10KiB摘要及24KiB总预算。
- 轮动使用独立只读`rotation_sectors`：盘中快照从原`comparison_input.sectors`、午评锚点从同份SHA正文、收盘锚点从其原正式代际读取全部已留存的行业与概念指数，最多1500行。真实价格独立保留；价格、前收和涨幅对账后才附可比元信息。缺前收不反推、不清空真报价，缺成员不清空指数价格。该字段不受强度榜前20/简报前24名限制，不含成员数组或个股样本，不改变原强度排名、简报正文、采集范围或2MiB快照/384KiB简报存储上限。成员名单与封板池证据充分时附成员SHA、日期、数量及封板数；证据缺失为未知，只有完整名单与完整核验池才能给精确0。显式空数组不回退短榜。非短榜题材下钻须先确认该快照有唯一真实指数报价，再通过原`member_reference`代际/日期及原成员文件SHA核验读取，不以短榜`board_stocks`样本的有无作为门禁；完整名单返回与轮动相同的成员SHA。旧正式代际缺失时不换最新代际。AI工具仅已有`query`非空时匹配全量留存轮动报价，继续只向模型返回原4/6条有界样本，默认摘要仍用原短榜。
- 方向通用指标由同档原成员名单、经证券唯一性及最新价/前收/涨幅对账的逐股报价、同窗口来源回执支持的涨停/炸板池只读推导：`up_ratio=advancers/rankable_members*100`，分母是该时点可比成员；同时提供`decliners/member_coverage_pct/median_change_pct/breadth_state/breadth_reason`。`limit_up_count/broken_count/first_board_count/multi_board_count`仅按原成员交集计算，完整成员与完整核验池才能确认精确零，部分池的正交集是下界、缺席保留未知；`broken_count_reason`解释缺档。盘中/午评报价不跨时点补齐，收盘使用原正式代际同日行情，不读取当前成员或联网。上述字段可回注同档短榜，保留排名、候选与股票数组；前端合并同ID丰富行时不得被轮动简化行的缺字段整体覆盖。该投影不写回原快照、不改变策略或回测指标。
- 页面聚合读取仅在本次请求中共享已校验的原SQLite行、固定日期/代际成员证明、同一原报价对象的证券索引与来源绑定；`read_context`是内部参数，不进入JSON，不设跨请求TTL。日期、代际、原输入对象、来源SHA或时点变更会重新核验；叙述仍执行采集完成时间和原简报证据截止检查。展示阶段丰富的板况标签不修改叙述使用的原梯队样本。优化前后API字段、顺序、数值与unknown语义保持相同。
- `GET /api/v1/market/intraday/members`按`date`、`slot`、`snapshot_id`和`topic_code`读取题材个股，支持上市板块过滤及分页。竞价范围限冻结候选，午评和盘中使用对应快照报价，收盘使用指定日正式日线。单列成员名单留存日和当期报价覆盖，快照或成员证据缺失时返回原因。先过滤、排序再分页，`sort`支持`rank`、`name`、`change_pct`、`closing_change_pct`、`amount`、`change_30d_pct`、`return_ytd_pct`、`last_limit_up_date`、`limit_up_1y`，`direction`为`asc`或`desc`，默认`change_pct`降序，缺值始终置后。`rank`是筛选后的默认涨跌幅名次。选择历史字段排序时附带本页的`history`，采用同一时点截止口径，涨停次数未知总数时按已确认下界排序。
- `GET /api/v1/market/intraday/auction-reference`读取指定日竞价快照中冻结的前日连板梯队，按可核验的前日板数及竞价报价展示。个股题材取竞价冻结的来源名单，展示名单留存日，缺少核验凭证时保持未知。竞价涨跌不判作当日封板、晋级或炸板。
- `GET /api/v1/market/intraday/history`按`date`、`slot`及最多32个逗号分隔的`symbols`返回历史字段。正式收盘时点可包含当日，其余时点仅使用此前已发布的收盘数据；`previous_board_count`始终取观察日前一交易日的已核验收盘涨停池及当日收盘价，缺证据为`null`。另提供独立的`closing_date/closing_snapshot_id`及逐股`closing_change_pct/closing_quote_date/closing_snapshot_id/closing_reason`：仅同日15:00后正式发布的固定代际、已通过原始描述符及对象SHA核验、同股同日正成交报价与可比前收/公司行动能提供收盘比较；不覆盖所选时点的涨幅或历史截止日，不以更晚日期替代。异常原子返回未知，读取中行情切代时整份结果保持未知并提示刷新。成员接口额外支持`sort=closing_change_pct`，先对完整筛选成员集合轻量读取严格两日收盘再排序分页，仅本页最多32行计算其他历史字段；原历史字段仍先全成员计算再排序。`GET /api/v1/market/intraday/quotes/{snapshot_id}/{symbol}`读取固定快照的临时报价供图表对账。
- 扶摇股票最多 8 页、板块最多 800 个且 50 个一批，板块使用已留存行业与概念目录，优先行业。v2 加采同日封板池，最多两页，整轮上限为 27 次逻辑请求、32 次实际尝试。已冻结的 v1 任务沿用原 25 次逻辑请求、30 次实际尝试上限。样本上限与有效覆盖分别展示。响应总时间需核验为当日，来源未提供逐条时间时只展示实际采集区间。
- 榜单保存 30 只股票与 20 个板块及覆盖摘要，能核实为上市前五个交易日的新股单列。前一次同日快照存在且价格可比时计算涨幅变化百分点。名次变化要求两次可排名代码集合一致；人民币累计成交额可比且未倒退才计算区间新增成交额。无前次快照或无法核验的字段保持空。 v2 另外提供板块强度、已确认封板、未列入已核验封板池的强势候选。成员名单及池覆盖不足时保留已确认下界或未知，旧快照继续展示原指数涨幅与个股报价。
- 盘中 `market_pulse.indices[].amount` 使用本轮已核验指数人民币累计成交额。`amount_comparison` 对照严格前一交易日同档，市场样本只比较两个端点都有报价的相同股票。`comparison_amount` 是今日可比累计成交额，`previous_amount` 是前一交易日可比累计成交额，`change_from_previous` 是两者的差额（人民币元）。指数的 `interval_amount` 与市场 `summary.interval_amount` 都以当日两个累计值求区间成交额，且需两个交易日的四个端点才计算同段差额。未留存端点或旧快照缺少指数成交额时保持未知。不同指数不相加，成交额不表示资金净流入。
- 题材轮动的非竞价指数行仅在价格与涨幅对账后提供 `quote_source=fuyao`、`quote_basis=provider_index`、`latest_price`、`prev_close`、`price_basis=none`、`currency=CNY`。前端只比较相邻同日、同代码/类型/来源/前收的两档，涨幅差单位为百分点；竞价候选均值、缺档或不可比口径不相减。精确封板家数差另要求相同 `member_reference_sha256`、完整同成员范围、两档 `limit_up_count_state=observed`；只读补充证明复用原冻结成员代际，不改写旧快照或重新取数。
- 盘中观察和盘中成员表可返回 `limit_up_reference_price`。已留存的未复权人民币报价、正成交额、前收和涨跌幅通过对账，按冻结证券身份和上市日期排除 ST 与上市初期后，最新价精确匹配常规涨停参考价才展示。该字段仅表示价格匹配，封板、首板和连板继续取已核验来源池。
- 封板梯队、炸板、前日涨停及炸板反馈、同日封板变化的个股明细复用 `themes`，最多一项行业与两项具体概念，保留成员名单留存日。旧快照仅在原冻结成员代际可核验时按该代际补充显示，不改写快照、事件与家数。
- 午评锚点的封板分组和炸板个股只读原午评快照的已核验来源池，每池受原采集 2,000 行上限约束。已有板数按板分组，缺板数保留待核验。题材使用本份午评冻结归属，不改变已生成简报。
- 盘中 v5/v6 封板池和炸板池只有成功响应明确给出空明细、首页和零总数，且来源时间核验在当前观察窗口内，才确认为 0。缺失、过时或结构不完整继续保持未知。
- 盘中 `board_changes` 是独立的相邻时点只读投影：v7 为 `0945←0935`、`1000←0945`，旧版1000只读投影仍使用0935（若有）。共同档位为 `1030←1000`、`1100←1030`、`1330←同日午评`、`1400←1330`、`1430←1400`；0935无同口径前档，不用竞价候选代替。`source_bindings.current/previous`分别包含原快照ID、日期、时段、采集区间、来源及正文SHA；两档原文、来源回执、同日和各自版本固定窗口均需通过核验。`groups`包含`new_sealed/new_limit_down/new_broken/opened/resealed`，分别表示新入封板池、新入跌停池、新增炸板样本、前档封板本档炸板、前档炸板本档封板；新入池不等于首次触板，缺席不推断开板或跌停解除。每组`state/count/items/omitted_count/reason_code`；完整两池可精确计数（含0），部分正向证据只给已确认下界，无证据为null。每组最多32只，明细带当档报价、已知连续板数、明确板况、原冻结代际题材及`quote_context`供原历史/图表接口读取。收盘档的变动名单 `items[].themes` 沿用同一正式收盘代际中已核验的当前池样本，缺少归属证据时留空。来源冲突、前收改变或报价缺失剔除，不能把未匹配视为0。旧炸板短样本只支持下界，旧快照没有跌停池时保持未知。AI单档摘要按同证据返回有限样本，多档仅返回紧凑计数、状态和前档来源绑定，继续遵守原3/10KiB预算，全档工具总返回仍不超过24KiB。
- 新建v7任务沿用v5已启用的`board_change_evidence=intraday-board-pools-v1`和`request_budget=32`冻结，复用原完整封板/炸板池并在原板块请求全部完成后最多加两页跌停池；跌停每页最多一次实际尝试、超时最多8秒且不能超过原观察截止时间。v5已带标记任务保持相同计划，没有标记的旧v5保持原30/35及原网络范围；旧版本不增加请求。v7每档仍为最多32次逻辑请求、37次实际尝试。新增0945至多增加一轮相同上限，不增加重复主行情任务或逐股取数。三池仅将完整/部分已核验代码及有效板数压缩保存在同份`board_pool_evidence`，源响应SHA和时间仍在原`source_checks`，不新增表、不改旧正文，继续受2MiB总上限和原28自然日清理约束。读取API不会联网。所有反馈/成员样本的当档`limit_up_state/limit_down_state`只补同份快照的明确正向池证据，不覆盖历史反馈原`state`。
- 快照正文和比较输入随 SQLite 标准业务备份，单份不超过 2 MiB，核验正文 SHA 后读取。新建 v7 任务冻结 `retention_calendar_days=30`，成功采集后按含观察日的 30 个自然日清理到期快照。既有无此字段的 v7 及更早冻结任务继续按原 28 个自然日规则执行。盘中简报按 30 个自然日留存。保留来源响应 SHA、时间与覆盖，源响应正文不重复持久化。盘中快照不进入正式日线、策略和回测。

盘中观察 v3 追加市场概况、今日封板梯队和接力反馈。四个基准指数一次批量查询，仍取本轮采集区间内的有效报价。市场广度与样本成交额按本轮有效股票统计。封板总数与连板高度各自保留覆盖状态，样本或板数不完整时只显示可核验下界或未知。接力反馈区分严格前一市场交易日已核验收盘涨停队列、同日上一份快照的封板池变化，后者的出池仅表示本轮未见封板记录。比较缺档时不重读其他时点补写旧快照。旧 v1/v2 继续按原冻结取数及读取口径。

盘中观察 v4 增加同日炸板池，最多两页，整轮上限 30 次逻辑请求、35 次实际尝试。涨停与炸板均需核对本轮股票快照价格；同轮同时被两个池确认的股票单列为状态冲突，不重复计数。炸板家数、样本和板块归属按可核验证据展示，出池仅作为同日快照变化。题材标签复用当时已留存且同源的行业、概念成员名单，记录名单日期，标签表示归属。全套快照仍保留 2 MiB 上限和 28 个自然日清理规则。既有 v1–v3 任务继续按冻结口径执行。

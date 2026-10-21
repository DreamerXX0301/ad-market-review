# Analytics API v1

根路径 `/api/v1`。成功直接返回业务对象；列表通常为 `{items,total}`，错误为 HTTP 4xx/5xx 与 `{detail}`。数值未知为 null，条件为 true/false/null；百分比 `3.2` 表示 `3.2%`。所有 GET 只读本地已发布数据，不触发来源取数。工作区写接口见 [Workspace API](workspace-api.md)。

## 日期、身份与覆盖

- `date`/`as_of` 必须属于已发布范围内的完成市场交易日；未来、无效格式或越界日期拒绝。盘面日期不会修改业务时钟。
- 股票/指数使用完整证券 ID，例如 `000001.SH` 与 `000001.SZ`；板块使用已核验目录的完整来源 `topic_code`。
- `data_coverage` 含 `label/start/end/basis/notice/selected_date_available/partial/available_dates`。basis 是可读口径；日期列表仅表示明确留存日，不保证中间连续完整。
- 主行情、来源涨跌停池、资金、异常名单和成员快照分别披露范围。缺来源不填零，不以抓取时间冒充数据日。

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

`gainers` 的检索/排序对完整候选集合执行后再分页，null 升降序都最后；年内次数下界不参与精确次数排序。`flow` 的近若干日是跨日趋势，不是全天盘中资金时序，也不推断机构真实决策。

`sentiment.market/short_term` 含状态、范围、指标、样本与缺口。`sample_count` 为有效可比数，`observed_count` 为观察候选数，`unknown_count` 为差额；收益样本覆盖不等于来源全集完整。晋级率证据不全时为 null，已知价格反馈不冒充接力晋级结论。

## 个股/指数图表

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

`GET /instruments/{id}/summary?as_of=` 仅适用于已留存股票，不用于指数/板块。返回身份、`board/board_label/as_of/data_date/quote_date/snapshot_id/quote_available/close` 及：

| 字段 | 含义 |
| --- | --- |
| `return_ytd_pct` | 上年末已核验最后交易日为基准的年内收益；不能用年初首根代替 |
| `change_30d_pct` / `return_30d_pct` | 精确 T/T−30 市场交易日收益，不能改成最近 31 条报价 |
| `return_mtd_pct` | 以上月末为基准的本月收益，与 30 日收益不同 |
| `limit_up_ytd` / `limit_up_ytd_observed` | 完整年内次数 / 已确认下界；不足时主值 null |
| `last_limit_up_date` / `sessions_since_limit_up` | 最近已确认事件日期及交易日距离；当日距离为 0 |
| `last_limit_up_complete` | 事件后缀证据是否足以确认确为最近；不是历史从未涨停声明 |

各组另有 reason/coverage；收益需端点与可比公司行动证据，缺当天报价为未知。次数只统计不晚于观察日的原始收盘核验事件。兼容别名 `annual_return_pct/annual_limit_up_count/latest_limit_up_date` 等不改变语义。成员与个股详情复用同一统计服务，无 N+1 来源请求。

## 板块 K 线与成员

- `GET /market/sector-chart?topic_code=&date=&period=D|W|M` 返回通用 chart shape 加 `available/task/unavailable_reason`；未缓存时 bars 为空，不联网。
- `POST /market/sector-chart/jobs {topic_code,date?,request_key?}` 手动加载单板块；返回标准任务或 `{cache_hit:true}`，随后重读 GET。只加载该来源代码，不扫描全库。
- 历史缓存须先复验原始全段与 SHA，再按目标日截断重算聚合和指标；不得从未来已聚合结果切片。板块是原始指数点位，不做个股复权/涨跌停标记。
- `GET /sectors/{topic_code}/members?as_of=&membership_scope=latest|historical` 无需跟踪轮次。latest 仅用当前业务日报价，显示真实成员采集日；historical 严格查历史留存。无档保持未知，不按名称猜成员。
- 成员返回 `membership_as_of/quote_as_of/members_complete`、board 和上述收益/事件字段；`stock_entry_baseline=null`。展开成员不会创建个股跟踪，也不会把板块入池点位套给股票。

精确字段与校验入口见[平台路由](../backend/app/routers/platform.py)、[工作区路由](../backend/app/routers/workspace.py)及[市场服务](../backend/app/services/market_service.py)。

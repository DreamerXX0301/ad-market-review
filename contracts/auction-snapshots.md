# 盘前竞价 v1

竞价是独立时点证据。主阅读路径为“前日已留存判断 → 本日终态竞价 → 开盘观察 → 盘后核对”。不改变盘后发布头、业务日期、策略、跟踪或回测。盘面页共享观察日，历史只读已经冻结的内容。

## HTTP

- `GET /api/v1/market/auction?date=YYYY-MM-DD`：默认北京时间今天。只读 SQLite，返回 `date,today,now,capture_window,can_capture,can_retry,can_resume,capture_reason,locked_for_day,state,task,snapshot,available_dates,preparation,scope`。`state` 为 `not_collected/queued/running/paused/failed/cancelled/available`。未留存时 `snapshot=null`。GET 不创建任务或联网，不打开 worker 活动 DuckDB。
- `GET /api/v1/market/auction/dates`：小台账日期与任务状态，用于合并盘面观察日期，不读来源正文或全历史目录。
- `POST /api/v1/market/auction/jobs`：接受 `date,request_key,instrument_ids?`。代码数组最多100个原始元素，明确 `SH/SZ/BJ` 后缀，去重保持顺序。空数组或未提供使用默认观察范围。相同请求、日期和代码重放返回原任务；不同请求在当天已有台账时409。API仅登记任务，由唯一worker执行。
- `POST /api/v1/tasks/{id}/retry`：失败且仍在原日窗口内时重排**原任务**，不复制任务、名单或日期。`resume`仅允许原日窗口内手动继续暂停任务。取消后当天不再采集；成功后不重复采集。失败重放POST不会自动重试。通用任务入口不能绕过这些约束。
- `GET /api/v1/market/auction/close-review?date=YYYY-MM-DD`：独立盘后只读核对，用户展开时读取。没有竞价留存不倒造；未到当日15:00或同日盘后数据未发布时`pending`。仅读取已发布的同日未复权Parquet，不联网、不写回原竞价。见下文。
- `GET /api/v1/market/auction/automation`：只读服务自动采集配置、当前worker心跳、下次候选时间及最近日运行回执。它是全局配置，不跟随盘面历史观察日改变。读取不创建任务。
- `POST /api/v1/market/auction/automation`：接受`mode:'off/once/daily',date?,instrument_ids?,expected_revision`。`once`绑定服务器今天，`daily`仅在用户明确开启后授权后续逐日竞价。代码与手动入口同样最多100个；缺省使用默认样本规则。配置使用修订号乐观锁，过期409，关闭可撤销后续自动触发。

`capture_window={allowed,reason,start_at,end_at,timezone:'Asia/Shanghai',calendar_verified}`。仅今天 `09:25:00 <= time < 09:30:00` 可以手动创建和执行。API先排除周末，worker通过真实来源交易日历核实今天及严格前一交易日。每次实际HTTP尝试之前、响应收到之后均检查窗口与租约；窗口外响应丢弃，不能用盘中最新价或历史开盘价补造。

## 显式开启的服务自动采集

迁移031新增`market_auction_automation`配置和`market_auction_automation_run`日运行小台账，新安装与升级初始均关闭。开启由用户明确操作或本会话明确授权，单纯浏览、刷新、重启或部署不会开启。此设置只授权竞价终态采集，不扩大原取数任务或旧冻结任务的范围。

自动调度由唯一worker服务执行，浏览器关闭仍可运行。北京时间9:25:05起至9:30前检查本日授权和原有日台账，通过来源日历核实交易日；节假日不产生有效竞价。不会抢占正在运行的长任务；服务未运行、电脑休眠或工作进程忙到窗口结束时显示跳过/过窗，不把盘中报价补成盘前结果。下次运行时间是本地排程候选，不代表来源交易日历已核验。

同日手动与自动共用一个竞价job和日台账，多个tick、重启或并发请求不会多造快照。当天已有手动任务只观察其结果，不取得自动重试权。自动创建的job绑定当时配置修订号及授权身份；关闭、修改、暂停、取消或手动接管会阻止其后续自动创建/恢复/重排。已经开始的取数按冻结授权结束，仍可显式暂停或取消。

只有本次明确非终态/`not_ready`的自动任务，才可在9:30前按有界间隔重排原job，最多3次执行尝试。网络层有限重试仍受每次HTTP时间窗和租约守卫约束。权限、错误身份、无效时点或量额核验失败不能据旧not_ready日志反复重跑。暂停、取消及用户手动接管的任务不自动继续。

`automation`视图包含`version,revision,mode,date,instrument_ids,today,now,timezone,start_time,end_time,enabled,next_run_at,can_enable,reason,last_run,worker`。`last_run`至少列`date,job_id,state,attempts,reason`，失败与等待、跳过与成功分开。UI展示服务是否在线、下一次候选时间；采集中用现有全局进度门禁止重复操作，终态后释放并查看原日期结果。

标准备份保存配置及日回执。恢复到任何新副本时强制关闭自动配置并增加修订号，不会把原安装的自动授权直接带到预演或灾备副本；原日运行回执仍保留。新副本须再次明确开启，避免两份安装重复取数。

## 冻结范围与前日判断

默认顺序：严格前日留存复盘的明确关注股、原观察和逐股主线 → 采集开始前已加入的自选 → 严格前日已核验涨停（高度降序）→ 严格前日已核验炸板。按代码去重，最多100只。`universe={mode,items:[{instrument_id,name,reasons,previous_board_count}],requested,total_candidates,omitted,scope}`。被省略数量明确显示，不把候选样本称为全市场。

显式名单只采集所填代码，仍可按代码关联真实前日理由和高度，不额外加入其他候选。前日缺档不退回更早一天；没有已留存判断时只能显示事实背景，不能把当前重算意见称作昨日原判断。

首次执行在外部取数和本地行情读取结束后，用短事务将 `frozen_inputs` 加入原任务参数并同步更新参数指纹：`previous_date,known_at_ms,universe,previous_review`。窗口内失败重试保留首次冻结名单及前日材料，不受后来修改自选、主线、复盘的影响。原文只取实际保存时间不晚于采集开始的版本；不从现今题材回填历史，不自动判定自由文字“兑现/证伪”。

`previous_review`保留日期、状态、实际保存时间、原版本及SHA、原复盘payload、原主线修订、原关注信息、前日事件交集和缺口。背景超过768KiB时只保留明确列出的投影字段并标注原始SHA仅作原件身份；仍超预算则拒绝，不悄悄截断后声称完整。

今天未成功采集时可附 `preparation`，只从 SQLite 取最近已发布日且采集前可读的留存复盘与主线，标记 `frozen:false,relation:'last_published_day_pending_calendar_check'`。它是尚未冻结的准备资料，严格前一交易日仍待worker核验；不得混入历史或替换已冻结 `previous_review`。

## 数据与数值契约

精确来源：

- `/api/a-share/calendar/trading-days`
- `/api/a-share/auction/snapshot?thscodes=...&stage=final`
- `/api/a-share/auction/short-term-benchmark?date=YYYY-MM-DD`

快照只有 `auction_phase='final'` 且 `data_status='ready'` 时才允许使用数值。**非就绪响应即使价格、成交等字段全部非空，也全部保持未知**。来源 `timestamp` 是响应组装时刻，只核实日期、时段与非未来，不当作逐证券报价时间；`quote_at/quote_as_of`保留null。没有连续时序，不推断9:20至9:24撤单、抢筹或封单稳定；未匹配量无方向，不推导净流入。

`payload={version:'auction-snapshot-v1',date,collected_start_at,collected_end_at,previous_date,universe,quotes,benchmark,previous_review,analysis,gaps,sources,evidence}`。

`quotes={state:'observed/partial/unknown',items,summary,gaps,source_timestamp,auction_phase,data_status,units,quote_as_of:null}`。每个请求代码保留一行，包括缺报：

`{instrument_id,name,state:'observed/unknown',reason,auction_price,auction_pct,auction_amount,auction_volume,auction_turnover_pct,auction_yesterday_ratio_pct,auction_volume_ratio,pre_close_price,quote_at:null,unit_check:{state,reason}}`。

- 价格为未复权人民币元，成交量为手（100股），成交额为人民币元。涨跌幅、换手和占昨日量为百分数原值，不能再次乘100；来源量比单独显示，不自行改算专业量比。
- 竞价价、前收价须为正有限数；涨幅与`(竞价价/前收价-1)×100`允许0.06个百分点的舍入误差。失败行整体不参与有效价幅统计，不用`last_price/open_price`替代竞价价。
- `amount ≈ price × volume × 100` 采用1%或1元的较大容差。量额缺失或不符时保持未知，不擅自改单位；已核验价幅仍可使用。已知量额0可保留，不把未知填0。
- 重复代码隔离对应证券；无关代码、错误纯代码、缺票、声明数量不符各自列缺口。缺报不认定停牌。布尔值、NaN、无穷、负成交量均不是有效数值。

`summary={requested,valid,missing,up,down,flat,median_pct,amount,amount_valid,scope}`。高低平开与中位数仅使用有效价幅，金额仅累加量额已核验部分，覆盖分母分别显示。无有效样本时统计值为null，非0。

`benchmark={date,state,items:[{instrument_id,name,auction_pct,tags}],reason,scope}`。必须返回明确本日 `date/date_ms`；空结果、错日、缺少有效记录保持未知，不自动回退。来源短线样本与标签单列，不当作全市场、个人样本的完整基准或买卖信号。

## 原件、存储与备份

迁移030新增 `market_auction_capture` 永久日任务台账和 `market_auction_snapshot` 不可变正文，初始为空。`job.kind='source_check',operation='auction_snapshot',version='auction-snapshot-v1'`。新增租约attempt身份，过期旧worker不能在原任务恢复后复写。

每次执行最多3个逻辑来源请求，单个原响应最多1MiB，全部原件最多2MiB，完整正文最多4MiB。规范JSON原件嵌入SQLite的 `evidence`，含实际请求参数、请求/接收时间、内容SHA、字节数及body；不新建外部raw链。凭据回显直接拒绝，不写入正文、日志或导出。失败且无有效价幅不产生成功快照；失败原响应不持久保存，不称已留存证据。

GET验正文SHA及每份来源原件SHA、大小、参数、时窗和sources闭包，向前端剔除原响应body，仅返回元数据与 `evidence_count`。标准SQLite备份完整包含竞价正文及内嵌原件；backup/restore均运行同一纯校验器。v1长期保留，不宣称套用了午评28日清理。

## 盘后核对

独立结果 `version='auction-close-review-v1',posthoc:true`，状态为 `not_collected/pending/unknown/partial/available`，绑定原 `snapshot_id/auction_content_sha256` 和当前已发布 `market_snapshot_id/source_sha256`。这份派生展示不会改变盘前正文或名单，明确标记为事后信息。

只对最多100个原代码请求同日已发布历史范围，要求唯一同日、同来源、人民币未复权OHLC及正成交量。缺日、重复、停牌或价形不符保持未知。开盘价与竞价价偏差超过0.011元时单列 `mismatch`，不纳入汇总。

每行包含 `auction_price,auction_pct,open,close,high,low,auction_to_close_pct,open_check,state,reason`。`auction_to_close_pct=(close/auction_price-1)×100` 只称“竞价至收盘价差”，不称实际收益。汇总 `requested,valid,missing,above_auction,below_auction,flat,median_auction_to_close_pct,open_mismatch`，未知不填0。这不是可实现收益、胜率或策略回测，未假设竞价能成交，未纳入费用、滑点和交易限制。

## 验证

专项覆盖真实曾出现的“非就绪但全部有数值”、量额/涨幅对账、窗口/跨日/节假日/跨窗响应、手动重试和暂停取消、候选冻结、原判断实际时点、请求幂等并发、原件闭包与标准恢复、GET零网络、日线与策略数据不变、未知排序、图表前日截止和盘后核对。

来源：[同花顺集合竞价API](https://fuyao.aicubes.cn/docs/api-reference/auction/)、[上交所2026年交易规则](https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20260424_10816482.shtml)、[深交所竞价撤单说明](https://investor.szse.cn/institute/video/classroom/knowledge/t20100721_537821.html)。真实终态验收须在当日合法窗口手动执行；自动测试和非就绪通路探测不等于真实竞价已采集或有效性已经获证。

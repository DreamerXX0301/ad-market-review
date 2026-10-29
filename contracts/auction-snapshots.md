# 盘前竞价 v1

竞价是独立时点证据。主阅读路径为“前日已留存判断 → 本日终态竞价 → 开盘观察 → 盘后核对”。不改变盘后发布头、业务日期、策略、跟踪或回测。盘面页共享观察日，历史只读已经冻结的内容。

## HTTP

- `GET /api/v1/market/auction?date=YYYY-MM-DD`：默认北京时间今天。只读 SQLite，返回 `date,today,now,capture_window,can_capture,can_retry,can_resume,capture_reason,locked_for_day,state,task,snapshot,available_dates,preparation,stock_boards,scope`。`state` 为 `not_collected/queued/running/paused/failed/cancelled/available/purged`；`can_capture/can_retry/can_resume`保持false，仅保留兼容字段。未留存时 `snapshot=null`。GET 不创建任务或联网，不打开 worker 活动 DuckDB。
- `GET /api/v1/market/auction/dates`：小台账日期与任务状态，用于合并盘面观察日期，不读来源正文或全历史目录。
- `POST /api/v1/market/auction/jobs`：已拒绝手动创建，返回403且不登记job；通用任务入口也不能绕过。新任务仅由唯一worker的每日调度上下文创建，范围由系统自动冻结，不接受手填名单。
- 竞价任务的`POST /api/v1/tasks/{id}/retry`及`resume`均返回403且不重排原任务；`pause/cancel`仍可显式停止，之后不手动或自动复活。成功后不重复采集，失败后只允许原有效自动授权按下述not_ready规则重试。
- `GET /api/v1/market/auction/close-review?date=YYYY-MM-DD`：独立盘后只读核对，用户展开时读取。没有竞价留存不倒造；未到当日15:00或同日盘后数据未发布时`pending`。仅读取已发布的同日未复权Parquet，不联网、不写回原竞价。见下文。
- `GET /api/v1/market/auction/automation`：只读服务自动采集配置、当前worker心跳、下次候选时间及最近日运行回执。它是全局配置，不跟随盘面历史观察日改变。读取不创建任务。
- `POST /api/v1/market/auction/automation`：兼容配置接口只接受`mode:'off/daily',expected_revision`，单次模式、日期及任何`instrument_ids`字段返回422。产品设置统一到数据中心的`GET/PUT /api/v1/settings/automatic-tasks`，竞价子项及独立修订号见[自动任务契约](automatic-review-tasks.md)。配置过期409，关闭可撤销后续自动触发；读取历史日期不改变全局设置。
- `GET /api/v1/market/auction/topic-summary?date=YYYY-MM-DD`：只读派生题材/行业样本股数、有效/缺失报价数和等权平均竞价涨幅，返回原成员代际、来源SHA及成员日期。只用原快照明确来源，不读取当前成员补历史。
- `GET /api/v1/market/auction/feedback?date=YYYY-MM-DD`：只用冻结的严格前日事件和本次竞价，分组展示延续、修复、转弱与未知，不联网、不改变策略或快照。

`capture_window={allowed,reason,start_at,end_at,timezone:'Asia/Shanghai',calendar_verified}`。仅今天 `09:25:05 <= time < 09:30:00` 可以由自动worker开始来源请求及接受响应；这5秒是本系统在9:25结束后的固定缓冲，不是交易所规定。worker先排除周末，并通过真实来源交易日历核实今天及严格前一交易日。每次实际HTTP尝试之前、响应收到之后均检查窗口与租约；窗口外来源响应丢弃，不能用盘中最新价或历史开盘价补造。窗口内已收取且核验合法的竞价可以在仍持有有效租约时持久化；若可选来源基准恰好过窗，基准保留unknown，合法竞价以partial保存，不额外请求或挪用迟到响应。

## 显式开启的服务自动采集

迁移031新增`market_auction_automation`配置和`market_auction_automation_run`日运行小台账，新安装与升级初始均关闭。开启由用户明确操作或本会话明确授权，单纯浏览、刷新、重启或部署不会开启。此设置只授权竞价终态采集，不扩大原取数任务或旧冻结任务的范围。

自动调度由唯一worker服务执行，浏览器关闭仍可运行。北京时间9:25:05起至9:30前检查本日授权和原有日台账，通过来源日历核实交易日；节假日不产生有效竞价。不会抢占正在运行的长任务；服务未运行、电脑休眠或工作进程忙到窗口结束时显示跳过/过窗，不把盘中报价补成盘前结果。下次运行时间是本地排程候选，不代表来源交易日历已核验。

同日最多一个竞价job和日台账，多个tick、重启或并发检查不会多造快照。升级前已有手动原任务只观察结果，不取得自动重试权；旧快照、原名单和SHA保持原样。旧once/非空手填名单配置可读但不驱动调度。新自动job绑定当时配置修订号及唯一worker创建上下文，单独伪造授权字典不能新建。关闭、修改、暂停或取消会阻止后续自动创建/恢复/重排；重新开启不复活原已停止任务。已经开始的取数按冻结授权结束，仍可显式暂停或取消。

只有本次明确非终态/`not_ready`的自动任务，才可在9:30前按有界间隔重排原job，最多3次执行尝试。网络层有限重试仍受每次HTTP时间窗和租约守卫约束。权限、错误身份、无效时点或量额核验失败不能据旧not_ready日志反复重跑。暂停、取消的任务不自动继续，手动retry/resume也不能接管。

`automation`视图包含`version,revision,mode,date,instrument_ids,today,now,timezone,start_time,end_time,enabled,next_run_at,can_enable,reason,last_run,worker,legacy_configuration`。`last_run`至少列`date,job_id,state,attempts,reason`，失败与等待、跳过与成功分开。UI展示服务是否在线、下一次候选时间；采集中用现有全局进度门禁止重复操作，终态后释放并查看原日期结果。

标准备份保存配置及日回执。恢复到任何新副本时强制关闭自动配置并增加修订号，不会把原安装的自动授权直接带到预演或灾备副本；原日运行回执仍保留。新副本须再次明确开启，避免两份安装重复取数。

## 冻结范围与前日判断

默认顺序：严格前日留存复盘的明确关注股、原观察和逐股主线 → 采集开始前已加入的自选 → 严格前日已核验涨停（高度降序）→ 严格前日已核验炸板。按代码去重，最多100只。`universe={mode,items:[{instrument_id,name,reasons,previous_board_count}],requested,total_candidates,omitted,scope}`。被省略数量明确显示，不把候选样本称为全市场。

当前版本不接受显式名单。旧冻结任务或旧正文中的显式名单只按原代码和原来源读取，不扩大范围或重写历史。前日缺档不退回更早一天；没有已留存判断时只能显示事实背景，不能把当前重算意见称作昨日原判断。

首次执行在外部取数和本地行情读取结束后，用短事务将 `frozen_inputs` 加入原任务参数并同步更新参数指纹：`previous_date,known_at_ms,universe,previous_review`。窗口内失败重试保留首次冻结名单及前日材料，不受后来修改自选、主线、复盘的影响。原文只取实际保存时间不晚于采集开始的版本；不从现今题材回填历史，不自动判定自由文字“兑现/证伪”。

`previous_review`保留日期、状态、实际保存时间、原版本及SHA、原复盘payload、原主线修订、原关注信息、前日事件交集和缺口。背景超过768KiB时只保留明确列出的投影字段并标注原始SHA仅作原件身份；仍超预算则拒绝，不悄悄截断后声称完整。

今天未成功采集时可附 `preparation`，只从 SQLite 取最近已发布日且采集前可读的留存复盘与主线，标记 `frozen:false,relation:'last_published_day_pending_calendar_check'`。它是尚未冻结的准备资料，严格前一交易日仍待worker核验；不得混入历史或替换已冻结 `previous_review`。

## 数据与数值契约

精确来源：

- `/api/a-share/calendar/trading-days`
- `/api/a-share/auction/snapshot?thscodes=...&stage=final`
- `/api/a-share/auction/short-term-benchmark?date=YYYY-MM-DD`

快照只接受两个已核验的精确终态组合：`(auction_phase='final',data_status='ready')`及真实服务返回的`(auction_phase='matched',data_status='final')`。两个字段必须成对匹配，原件及展示均保留来源原值，不能先改写字段再校验。交叉错组、`closed/live/not_ready`等其余组合仍拒绝。**非就绪响应即使价格、成交等字段全部非空，也全部保持未知**。来源 `timestamp` 是响应组装时刻，只核实日期、时段与非未来，不当作逐证券报价时间；`quote_at/quote_as_of`保留null。没有连续时序，不推断9:20至9:24撤单、抢筹或封单稳定；未匹配量无方向，不推导净流入。

`payload={version:'auction-snapshot-v1',date,collected_start_at,collected_end_at,previous_date,universe,quotes,benchmark,previous_review,analysis,gaps,sources,evidence}`。

`quotes={state:'observed/partial/unknown',items,summary,gaps,source_timestamp,auction_phase,data_status,units,quote_as_of:null}`。每个请求代码保留一行，包括缺报：

`{instrument_id,name,state:'observed/unknown',reason,auction_price,auction_pct,auction_amount,auction_volume,auction_turnover_pct,auction_yesterday_ratio_pct,auction_volume_ratio,pre_close_price,quote_at:null,unit_check:{state,reason}}`。

- 价格为未复权人民币元，成交量为手（100股），成交额为人民币元。涨跌幅、换手和占昨日量为百分数原值，不能再次乘100；来源量比单独显示，不自行改算专业量比。
- 竞价价、前收价须为正有限数；涨幅与`(竞价价/前收价-1)×100`允许0.06个百分点的舍入误差。失败行整体不参与有效价幅统计，不用`last_price/open_price`替代竞价价。
- `amount ≈ price × volume × 100` 采用1%或1元的较大容差。量额缺失或不符时保持未知，不擅自改单位；已核验价幅仍可使用。已知量额0可保留，不把未知填0。
- 重复代码隔离对应证券；无关代码、错误纯代码、缺票、声明数量不符各自列缺口。缺报不认定停牌。布尔值、NaN、无穷、负成交量均不是有效数值。

`summary={requested,valid,missing,up,down,flat,median_pct,amount,amount_valid,scope}`。高低平开与中位数仅使用有效价幅，金额仅累加量额已核验部分，覆盖分母分别显示。无有效样本时统计值为null，非0。

`benchmark={date,state,items:[{instrument_id,name,auction_pct,tags}],reason,scope}`。必须返回明确本日 `date/date_ms`；空结果、错日、缺少有效记录保持未知，不自动回退。来源短线样本与标签单列，不当作全市场、个人样本的完整基准或买卖信号。

来源短线基准支持按竞价涨幅高→低、低→高独立切换，默认高→低；未知始终排末，0是有效值。排序只改显示副本，不改原来源顺序、标签、冻结正文、SHA或主竞价表排序状态。全无有效报价时，任务追加有界`auction_quote_rejected`诊断：阶段和状态各最多40字符、时间核验状态、请求/有效数量及最多5条缺口摘要。未知标记组合明确说明不支持，与未就绪分开；日志不留失败原响应、Key或完整代码名单。

### 昨日事件与今日竞价反馈

`feedback.version='auction-feedback-v1'`。以0为明确高/平/低开边界，对原名单中前日已核验涨停/炸板样本分组：昨日炸板→今日高开（修复观察）、昨日涨停→今日低开（转弱观察）、昨日涨停→今日高开（延续观察）、昨日炸板→今日低开（弱势延续），两类前日事件的平开分别待确认。缺少前日事件、当次报价缺失和前日两池冲突分开未知；不从竞价涨幅猜昨日事件，不用今日板数倒推昨日，也不把未知算成平开。

组包含`key,title,interpretation,count,valid_count,missing_count,median_auction_pct,average_auction_pct,items`，均值分母为该组有效报价，列表只取原100只交集。`coverage`区分已分类与未知；分组用于观察相对昨收定价，不自动判全天强弱、兑现或交易信号。昨日市场广度等冻结事实可作为独立背景，不据此断言个股被大盘拖累；没有已保存的文字预期不补造“昨日预期涨停”。

首位另列`key='limit_up_open'`（页面“一字涨停开·参考”），仅作为竞价涨停开参考筛选，不证明今日实际限价或全天一字。`basis='auction-standard-band-price-reference-v1',confirmed_limit_price=false`。用原冻结代码的常规市场档（主板10%、创业板/科创板20%、北交所30%）与来源前收，以人民币分、ROUND_HALF_UP和至少一分价差计算参考价；竞价必须有正成交量/额、通过单位与价幅校验、价格精确相等。ST、N/C名称、未知市场身份、缺价或量额核验失败不猜档。未核验当日上市、重组、除权、制度变化等例外，不能把参考值当来源直接确认的涨停价；不支持的额外`limit_up_price`/标志字段不构成证据。

只拆分已有修复/高开延续组中的匹配股票，未知前日事件/冲突保持原组；各组互斥、coverage及总数不变。每个匹配行附`opening_reference={basis,rate_pct,reference_price,confirmed_limit_price:false,matched:true}`。可计算时组`state='reference'`，数量与均幅为本次真实匹配样本，`checked_count`保留可计算参考的有效报价数；缺口不填零。完全无可计算输入时才返回`state='unknown',count=null,items=[],average_auction_pct=null,median_auction_pct=null,reason`，显示待核验。行业概念联动按真实子集重算参考组；完全未知保持未知。本次不扩采集请求、不修改原正文，也不以接近10%的宽阈值或昨日标签代替分价匹配。

### 昨日板高筛选与显示

板高只使用原冻结的`previous_board_count`：严格前一交易日涨停池与该日未复权正成交量收盘核验，来源连续板数字段一致才有值。反馈行还要求`previous_state='limit_up'`；炸板、事件冲突、未核验或缺档均归“其他 / 未核验”，不能把空值补成首板。标签显示“昨日首板”“昨日N板”，五板及以上筛选仍保留逐股实际N，不表示今天已晋级。

前端提供全部、首板、二板、三板、四板、五板及以上、其他/未核验，数量仅为本份最多100只冻结候选的去重数量，不代表全市场昨日梯队。股数含缺报价候选，均幅只以本份有效竞价的有限数值为分母，零是有效值，缺失不补零；矛盾重复证券保持未知。空组股数为0、均幅为null，不显示0%。例如合成测试中，某层3只、其中2只有效报价+2%和−1%，均幅为+0.5%，第三只未知不参与分母。

强弱分类卡与竞价观察单共用按观察日期隔离的板高选择，切日回到全部；可与原行业概念交集组合，再点选中的条件取消。数量、有效分母及均幅按对应交集重算，筛选不改写原涨停/炸板→高低平开分类、API原响应或快照SHA。观察单默认昨日板高降序、同高竞价涨幅降序；未知高度/涨幅各自排后。强弱卡片保留原类别，组内同样先按昨日板高、再按竞价涨幅降序。全部逻辑为本地派生，不新增来源请求或逐日存储。

### 展开个股的前日事件与上市板块标签

`GET /market/auction`在原快照外返回`stock_boards`，按本份最多100个唯一冻结`instrument_id`映射到`{board,board_label,board_scope,board_reason}`。仅由已读取的完整股票代码及交易所后缀纯计算主板、创业板、科创板、北交所或未知，不新增SQL、来源请求或任务。无法唯一识别的代码保持未知，不默认归主板；`board_scope/board_reason`说明这是当前代码分类，不证明该日历史上市身份，也不用于涨跌停制度核验或策略筛选。

该顶层派生字段不加入不可变`payload`，不修改原快照正文、证据或SHA。前端对观察单、低开复核、强弱卡片、题材共振和题材/行业展开的个股使用同一标签组件：显示严格前日已核验的“昨日首板/昨日N板”或“昨日炸板”，并列显示代码派生的上市板块。前日事件只取原冻结事件及可核验状态，缺档、冲突不猜昨日板高或炸板；“昨日炸板”也不算作首板。事件事实与代码分类分别展示来源范围，不因卡片所在题材或当前涨幅改变。

展开个股列表另有组内控制：按“昨日板高”或“竞价涨幅”升序/降序排列，默认昨日板高降序、同层竞价涨幅降序，未知始终排后；提供全部、主板、创业板、科创板、北交所、未知快捷筛选。只处理已冻结的本组股票，显示“当前N / 本组M”，不为筛选额外请求接口。各展开卡片的选择互相独立，换观察日或题材后重置为全部及默认排序。组内板块筛选不覆盖页面共享的板高/题材条件，不重算或改写原强弱分组统计、前列标志、策略结果及原快照SHA；它与上方会按交集重算统计的全局条件分开。

### 原成员档案的题材数量与均幅

`topic-summary.version='auction-topic-summary-v1'`。新任务在首次冻结输入时保存`market_context={source_snapshot_id,data_end,source,known_at_ms}`，成功正文携带该对象；旧冻结任务不扩大输入。旧正文只允许使用已经明确记录的前日事实代际；没有明确可核验代际保持unknown。

来源链为原代际→SQLite已发布topics快照及catalog manifest SHA→reference文件的SQL SHA/大小→有界topics CAS分片的SQL及内部SHA。只读取原指定代际最多1500组、一份有界成员分片；不跟当前head，不遍历历史。来源、代码、类别、成员日期分别核验，晚于前日的成员拒绝，同名不同代码不合并。原件丢失、SHA错或锚点不唯一均保留未知，不修改原竞价正文或SHA。

组含`topic_code,name,kind,source,members_observed_date,sample_count,valid_count,missing_count,average_auction_pct,instrument_ids,members_complete`。样本股数为原名单与已核验成员交集，均幅仅除以有效报价数，缺失不补0；不完整成员的数量标已确认下界≥N。题材和行业可分别按数量/均幅排序，单组数量可展开股票。一个股票可属于多个组，组内去重，组间数量不可相加成去重总数；不是全板块家数或板块指数收益。成员档案日与竞价日醒目标明。

入选背景的可见标签缩写为“昨日涨停”“昨日炸板”“自选”“昨日关注/主线”等，分别用红、黄、蓝、紫区分；完整来源措辞保留在悬浮提示和冻结证据中，不改写数据。

## 原件、存储与备份

迁移030新增 `market_auction_capture` 永久日任务台账和 `market_auction_snapshot` 不可变正文，初始为空。`job.kind='source_check',operation='auction_snapshot',version='auction-snapshot-v1'`。新增租约attempt身份，过期旧worker不能在原任务恢复后复写。

每次执行最多3个逻辑来源请求，单个原响应最多1MiB，全部原件最多2MiB，完整正文最多4MiB。规范JSON原件嵌入SQLite的 `evidence`，含实际请求参数、请求/接收时间、内容SHA、字节数及body；不新建外部raw链。凭据回显直接拒绝，不写入正文、日志或导出。失败且无有效价幅不产生成功快照；失败原响应不持久保存，不称已留存证据。

GET验正文SHA及每份来源原件SHA、大小、参数、时窗和sources闭包，向前端剔除原响应body，仅返回元数据与 `evidence_count`。普通竞价阅读页不展示请求明细、响应阶段、SHA或原件目录，后台原件留存与强制校验不变。标准SQLite备份完整包含竞价正文及内嵌原件；backup/restore均运行同一纯校验器。032引入`auction-retention-28d-v1`：按新成功增量的已核验行情日期保留28自然日（起点=anchor-27，严格早于起点才到期）。新建increment冻结`auction_retention_policy`，旧冻结增量不扩范围，初始化、暂停、取消和失败不执行清理。只由唯一worker执行，普通API连接默认拒绝删除；校验原正文和内嵌原件SHA后，在精确快照身份授权下删除并记录到期回执。活跃备份或其他业务引用仍保护候选，清理失败只记警告，不撤销已发布行情。capture与自动日账继续保留防止重采，清理回执不得复制原market代际引用；保留必要原任务审计。历史GET返回`purged`，不伪装成从未采集，也不允许历史补采。

## 盘后核对

独立结果 `version='auction-close-review-v1',posthoc:true`，状态为 `not_collected/pending/unknown/partial/available`，绑定原 `snapshot_id/auction_content_sha256` 和当前已发布 `market_snapshot_id/source_sha256`。这份派生展示不会改变盘前正文或名单，明确标记为事后信息。

前端复用共享`/tasks/activity`轮询，对明确`kind='increment'`、状态`succeeded/partial/no_change`且完成时间不晚于服务端观察时间的回执发出`workbench-market-increment-finished`；首轮只建立基线，按`job_id:finished_at_ms`有界去重，不把任务消失、解锁、暂停取消失败或午评/竞价完成视为成功增量。已展开、可见且有原竞价的核对区收到通知后只重新GET当前观察日期；任务`target_date=null`不猜成今天，也不据其改变view date。收起/隐藏时不追加读取，展开/激活由既有读取生命周期补读；手动读取按钮保留。不创建、继续或重跑任何采集任务。

只对最多100个原代码请求同日已发布历史范围，要求唯一同日、同来源、人民币未复权OHLC及正成交量。缺日、重复、停牌或价形不符保持未知。开盘价与竞价价偏差超过0.011元时单列 `mismatch`，不纳入汇总。

每行包含 `auction_price,auction_pct,pre_close_price,open,close,high,low,close_pct,close_pct_reason,auction_to_close_pct,open_check,state,reason`。`pre_close_price`仅用原冻结竞价来源前收；`close_pct=(close/pre_close_price-1)×100`，缺前收不按竞价价和竞价涨幅反推。`auction_to_close_pct=(close/auction_price-1)×100`只称“竞价至收盘价差/变化”，不称实际收益。开盘不一致等非`observed`行保留实际开收价格，不进入上述涨幅均值。

汇总保留`requested,valid,missing,above_auction,below_auction,flat,median_auction_to_close_pct,open_mismatch`，另含`average_close_pct,close_valid_count,close_missing_count,average_auction_to_close_pct`。两种均值分别只以各自可核验的有限数值为分母，未知不填0；缺前收不把原可比价差行降级。这不是可实现收益、胜率或策略回测，未假设竞价能成交，未纳入费用、滑点和交易限制。

`groups`复用同一纯`auction-feedback-v1`对原正文得到的盘前分类及成员，不用收盘结果改组。每组含`key,title,interpretation,count,valid_count,missing_count,open_mismatch,close_valid_count,close_missing_count,average_auction_pct,average_close_pct,average_auction_to_close_pct,items`；行另保留盘前`previous_state,previous_board_count,auction_state`及可用的`opening_reference`。一字参考组置前，其余已核验强弱组按盘前均幅降序，未知事件/报价/冲突组统一放后；分组依据不足时以`unclassified`接住原唯一报价，不能漏股或按收盘涨跌重建背景。组内canonical竞价值沿用原反馈核验结果，未知报价不能在子集计算中重新变成有效值。

收盘前或同日行情未发布时，仍给出原分组、股数与竞价均幅，收盘相关字段保持null；顶层`summary=None,items=[]`的待发布语义保留，且不得读取盘后行情。到期/未采集返回空组。前端以紧凑组表比较竞价均幅、收盘均涨幅、竞价至收盘均变化及各自有效覆盖，整行展开个股与可复制代码；行业概念点选只按原成员交集重算均值和顺序，隐藏全量汇总避免与子集混淆。表格固定列宽并在独立行展开，不扩宽整页。

盘后区域复用同一原`topic-summary`提供关联行业概念快捷卡片及逐股紧凑标签，不发起新的成员请求。卡片选择身份与盘前、观察单共享；组候选仍按原关联数量每类前2选取，但该处数值明确标注“收盘均”，以原`instrument_ids`与同日同版本`close-review.items`交集中的`state=observed`且有限`close_pct`等权计算，零有效、重复收盘证券隔离，缺失/不一致/待发布不回退竞价均幅。标签完整弹层保留成员日期及代码复制，弹层均幅明确称竞价均幅。日期或版本不符保持待核验。

## 验证

专项覆盖真实曾出现的“非就绪但全部有数值”、量额/涨幅对账、窗口/跨日/节假日/跨窗响应、拒绝手动创建/重试/继续、暂停取消不复活、候选冻结、原判断实际时点、请求幂等并发、原件闭包与标准恢复、GET零网络、日线与策略数据不变、未知排序、图表前日截止和盘后核对。

来源：[同花顺集合竞价API](https://fuyao.aicubes.cn/docs/api-reference/auction/)、[上交所2026年交易规则](https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20260424_10816482.shtml)、[深交所竞价撤单说明](https://investor.szse.cn/institute/video/classroom/knowledge/t20100721_537821.html)。真实终态验收须由明确开启的唯一worker在当日合法窗口采集；自动测试和非就绪通路探测不等于真实竞价已采集或有效性已经获证。
# 题材共振派生视图

前端主要关联卡片以观察日及原(source,kind,topic_code)身份点选，同步过滤强弱分组与观察单，不创建任务或改写快照；取消回到原完整样本，切日不继承选择。筛选后的分组数量、有效分母、均幅和中位数均按关联候选交集重算，原API响应不变。共振表先按原始数值双向排序再截取15组；名称用中文顺序单独排序，零为有效值，缺失始终置后，展开仍绑定原组身份。

前端共享一次按观察日期与版本核验的 `topic-summary` 响应，供强弱对照、低开复核、观察单及分组表展示行业和概念，切日不得沿用旧标签。标签保留来源、代码和成员日期；精简显示不改变完整数据，代码独立复制。两表固定列宽，展开股票以跨全部列的独立行展示，个股卡片只用同份竞价中 `state=observed` 的涨幅，缺失保持未知；不用当前报价补齐。

`GET /api/v1/market/auction/resonance?date=YYYY-MM-DD` 返回 `auction-resonance-v1`。只读原竞价正文及题材汇总所核验的原成员代际，复用其SQLite发布锚点和SHA；不联网、不取当前归属、不修改原快照。

有效竞价按涨幅降序、代码升序取前20只，实际不足20时保留真实数量；这只是相对排名，弱市也可能包含低开。分组至少匹配3只冻结候选。每组给出有效/缺失数量、前列命中数、均幅、中位数、高开占比和竞价额有效覆盖；`up_ratio`、`top_share`、`top_ratio`单位均为0—1比例，分别以本组有效报价、全部前列实际数量、本组有效报价为分母。`concentration_lift = top_ratio / (前列实际数量 / 全部有效竞价数量)`，单位为倍数，缺分母保持未知。前端默认按集中倍数排序，只列命中至少2只前列股票的前15组，支持均幅、命中数排序；不能把宽标签高命中数直接称为板块整体强势。

`anchors.state=unknown`、列表为空：当前快照没有已核验的今日涨停价与一字时序/报价证据，不能以接近10%竞价涨幅、昨日标签或题材词认定今日一字涨停，也不生成剔除锚股后的强度结论。所有分组仅代表本次候选交集，不代表全市场或完整板块；成员档案日期单独显示。

### 页内目录

竞价页提供只作用于页面内部锚点的目录，存在性校验、当前区高亮、说明提示、窄屏横向排布；点击不修改hash路由、共享view date或自动采集配置。details目标及其外层折叠在定位前展开，读取盘后核对仍走原只读接口。

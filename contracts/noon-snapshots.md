# 午评快照 v1

用户手动创建，每个北京时间交易日只允许提交一个任务；提交即占用当天名额，失败、取消、清理均不释放。页面浏览不联网取数。唯一worker采集，不切换日线发布指针、业务时钟或策略输入。

## HTTP

前端默认日期及跨页规则见[页面与日期域](frontend-needs.md#页面与日期域)：未主动选择时午评默认服务端今天，基础行情默认最新已发布日；手动历史选择共用并保留，历史提示醒目显示。当日缺失不回退，切日仍须核对响应、快照与正文日期。

- `GET /api/v1/market/noon?date=YYYY-MM-DD`：本地只读。缺省今天。返回`date,today,now,capture_window,can_capture,locked_for_day,state,task,snapshot,available_dates,retention`。`state`为`not_collected/queued/running/paused/failed/cancelled/available/purged`。`task`使用现有任务摘要字段；`snapshot`可空，有值时含`snapshot_id,date,collected_start_at,collected_end_at,recorded_at_ms,quality,content_sha256,payload,membership_context,prior_event_context,workbench`。后三项为独立证据或版本化只读派生展示，不属于原始正文及其SHA。
- `POST /api/v1/market/noon/jobs`：只接受`date,request_key`。同一请求重放返回原任务；其他请求在当天已有台账时409。新任务仅允许今天11:31至13:00前；API只核验时间/星期/配置/初始化，精确交易日由worker读取来源日历验证。成功响应为现有task_view（含task_id）。
- `GET /api/v1/market/noon/dates`：永久小台账日期列表，含state/清理状态，不读正文和原件。供午评页合并盘面观察日期；不扩大策略/回测日期范围。
- `GET /api/v1/market/noon/{snapshot_id}/stocks/{instrument_id}/quote`：按快照ID与含市场后缀的股票代码读取同一份午评中的单只股票，包括从板块成分股名单进入的股票。核验正文大小、SHA、版本、日期及采集时间，返回`{snapshot_id,trade_date,collected_start_at,collected_end_at,previous_trade_date,quote,reason}`。`previous_trade_date`取自已核验的冻结日历，缺失时为`null`。`quote`包含`instrument_id,price_basis,open,high,low,latest_price,prev_close,volume,amount,change_pct`，价格为未复权人民币，成交量为股、成交额为元、涨幅为百分数。只有同代码唯一且开高低最新价一致的记录可绘制午间临时日K，附属量价字段缺失为`null`；无可核验报价时`quote=null`并给出`reason`。身份不合法返回422，快照未留存或已清理返回404，完整性校验失败返回409。读取复用已保存的午评，无需新表或额外行情采集，临时日K不写入正式行情。
- `GET /api/v1/market/noon/{snapshot_id}/notes`与`POST`同路径：读取或追加个人观察修订。POST严格接受`body`最多4000字及`expected_revision`乐观锁；返回`snapshot_id,revision,body,recorded_at_ms`。旧修订不可改写，过期正文清理时其专属修订级联删除。非当前修订409，已清理快照404。

`capture_window={allowed,reason,start_at,end_at,timezone,calendar_verified}`；`retention={calendar_days:28,automatic:true,trigger:'after_increment',notice}`。按请求日返回`can_capture`，历史日不回退今天。所有缺数为null并给reason，不填0。

## 独立存储与模块接口

迁移027建立`market_noon_capture(trade_date PK,job_id UNIQUE FK job,request_key UNIQUE,requested_at_ms)`永久名额台账、`market_noon_snapshot(snapshot_id PK,trade_date UNIQUE FK capture,job_id UNIQUE,collected_start_at,collected_end_at,recorded_at_ms,quality,content_sha256,payload_json,sources_json)`不可变正文、`market_noon_purge`清理回执。永久台账不可删除；正文只允许worker按精确清单清理。来源原件存`raw/noon/<job_id>/<safe_name>.json.gz`，记录规范JSON SHA及gzip文件SHA/大小，拒绝符号链接、越界和凭据回显。

采集服务`app.services.noon_service`提供`read_view(day=None),list_dates(),create_capture(payload),execute_capture(job,callback=None)`；任务`kind=source_check,operation=noon_snapshot,version=noon-snapshot-v1`。execute返回quality/message，worker完成任务。已保存结果按job_id幂等返回；恢复不得混入另一天或13:00后的响应。已暂停/取消不自动继续，过期失败不补造。

`payload`字段：`date,version,collected_start_at,collected_end_at,quote_as_of:null,summary,indices,stocks,sectors,events,hot_stocks,previous_review,analysis,gaps,sources`。`summary={breadth:{up,down,flat,valid,total,missing,scope},amount:{value,valid,total,scope},coverage:{...}}`。指数卡新增`workbench.index_comparisons`，按代码比较冻结的严格前日午评摘要`previous_review.previous_noon`与当前人民币成交额。`matched_period`须有两日逐条报价时刻相同的证明；午休快照缺时刻仅为`snapshot_comparison/较昨日午评`，缺基准为`unavailable`，不使用昨日全天金额，不称专业五日量比。摘要只在新采集时从此前已存且记录时间不晚于采集开始的同日SHA正文中提取，不在历史GET时补查。

报价行统一`instrument_id,name,latest_price,change_pct,open,high,low,prev_close,volume,amount,quote_at:null`，保留缺失原因。板块额外`topic_code,kind,members_observed_date,member_coverage,member_up,member_down`，成员资料不足保持null。`events={limit_up,limit_down,limit_break,statistics}`，三池各`{state,count,declared_total,items,reason}`；统计保留独立原值，不强行对齐。`analysis={summary:[string],observations:[string],risks:[string],afternoon:[string]}`为有据规则文本。所有跨域采集区间和来源时间分开，不声称同秒快照。partial也保存已有有效域，全部无有效行情则失败。

上个交易日上下文接口`app.services.noon_context.build_previous_review(market, day, previous_day, known_at_ms, quotes)`；返回`{date,state,snapshot_id,recorded_at_ms,content_sha256,payload,mainlines,observations,cohort_feedback,event_cohorts,sector_background,previous_noon,gaps}`，state为`retained/background_only/missing/calendar_unknown`。只取捕获开始前已保存、明确前一交易日的复盘与主线修订；旧午评读取冻结事实，不重新取数或回填历史。缺复盘可单列严格前日收盘事实，不能把更早留存日或今天重算意见当昨天判断。自由文本不自动判兑现。

新采集冻结`event_cohorts={limit_up,limit_break,limit_down}`。每池含`date,state,source_count,verified_count,unverified_count,items,reason,scope`；行包含证券身份、前日收盘与可核验的昨日连续板数。池所属日期必须等于严格前一交易日，事件价须与同日收盘交叉核对；未核验记录不当作已确认样本，高度字段冲突保留未知。`sector_background={date,state,items,reason,scope}`只保存固定行情代际中严格前日、明确来源代码的板块指数价格背景，成员缺档不清空已有真实行情。事件池事实、板块行情与用户是否手动保存昨日复盘彼此独立。

### 午评工作台派生展示

`snapshot.workbench.version='noon-workbench-v1'`。主要展示以冻结`payload`纯计算，不能读取当前行情、当前成员或新增人工判断。以下额外证据只能读取原正文明确记录的原采集代际，不跟随当前发布头，不联网、不打开活动DuckDB；不修改`payload_json`、`content_sha256`、采集台账或任务状态，不占用新的采集名额。

- `membership_context`：旧正文缺少板块涨停交集或逐股归属时，可读取该代际的不可变成员目录；核验manifest/reference/CAS SHA、来源代码、类别、名单日，并重新对账冻结成员数量、有效数量与上涨/下跌汇总后，才计算本份冻结涨停池的成员交集或行业/概念归属。原代际缺失或对账失败保持未知。
- `membership_context.stock_classifications`：按证券代码返回`{industries,topics,state,reason,source_snapshot_id,source_sha256}`；归属项为`{topic_code,name,kind,members_observed_date,source}`，状态为`observed_sample/partial/unknown`。新采集把可核验归属冻结在`payload.stock_classifications`，以后优先使用；已冻结的未知项不另读新目录补齐。旧正文只读原代际归属。归属是带名单日期的成员样本，不是昨日涨停原因或上涨因果；与`theme_source/theme_date`标注的来源理由分别展示。读取与冻结均有股票数、归属数和正文大小上限，容量外保持缺口。
- 归属目标范围包含已冻结的昨日关注/事件样本、当前三池、`summary.top_amount/top_gainers`各最多20只、按本份实际股票成交额排序的前10只，以及小时热榜前10只；仅为已保存数据中的有界展示窗口，不扩展到全市场、不新取数。原代际归属核验和新采集冻结使用同一目标规则，旧正文已冻结未知的项目仍不另补当前成员。
- `prior_event_context={date,state,source_snapshot_id,event_cohorts,source_sha256,reason}`：仅当旧正文`previous_review.event_cohorts`缺少`limit_break`或`limit_down`键时，允许从原代际恢复严格前一交易日事件。存在该键但日期、结构或内容无效时保留未知，不追读替换。恢复须核验原代际身份、扶摇来源、参考文件/CAS与单月未复权日行情SHA，逐股交叉核验同日正成交量收盘及事件价；读取范围只限明确前日、有限事件对象和一个月份行情。每池保留来源数、已核验/未核验数、覆盖、参考与价格SHA，部分覆盖为`partial`，不能宣称全市场完整。原代际或证据缺失为`unknown`。板块背景等其他未冻结事实仍未知，不能读取今天的材料回填。

- `previous_feedback`：严格前日涨停样本的本次午间反馈，包含日期、来源/核验/当前有效/缺失数量、总体明细和分组。`max_confirmed_height`与`highest_items`单独列明已核验样本的昨日最高高度及股票，不声称全市场最高；无已核验高度时为null及空列表。固定五个主分组按高度降序展示：五板及以上、四板、三板、二板、首板（`board_5_plus/board_4/board_3/board_2/first`），四板不与五板混放；每只股票单独标明实际昨日连续板数，组内先按实际高度降序，同高再按午间涨幅降序，缺报排末尾。每组直接展示全部对应股票及午间涨幅，长名单局部滚动，不截断或默认折叠。高度未核验样本单列且为0只时不展示空卡；每组包含上涨/下跌/平盘、平均涨幅、中位涨幅、明显回落数量和当前池状态分布。统计使用完整分组，不随前端排序或分页改变；无可比报价时数值为null。
- `negative_feedback`：昨日炸板、跌停样本的本次反馈以及已核验昨日高位样本中的明显回落。仅描述冻结样本或上述核验通过的独立原代际样本；昨日样本缺失与今天报价缺失分开说明，不用“没有负反馈”替代未知。高位回落及其他反馈明细同样展示可核验的行业/概念归属。昨日炸板、跌停的“查看全部样本”及“昨日高位样本明显回落”默认展开，仍可手动收起；其他长明细保持原默认展开范围。
- 反馈明细的`current_state`为`limit_up/limit_break/limit_down/not_listed/unknown/conflict`，分别表示本次来源池状态。`not_listed`必须有足够池覆盖证据，不能把未采到当作未入池；多池冲突单列，不按涨幅阈值猜涨跌停。午间状态不命名为收盘晋级或断板。
- `today_ladder`：本次已核验涨停池按明确连续板数降序列分布与全部对应股票；高位组并排紧凑展示，首板单列，宽屏由外层Grid容纳两个完整原生表格，小屏单列，不能将原生`tbody`改为Grid挤压表格列。长名单局部滚动，不只展示前8只。逐股列股票、午间涨幅、已核验行业/概念；冻结的`limit_up_reason`另列为可展开的来源原因，仅按原文加号拆分，不将成员归属当作原因。缺失分别说明，高度未知单列。不能从“几天几板”文本猜连续板数，样本高度不等于全市场收盘确认高度。
- `today_ladder.classification_counts`：涨停样本的行业/概念分组；`workbench.event_classification_counts={limit_up,limit_down,limit_break}`分别返回三池的同结构分组，涨停项复用前者。每池含`state,sample_count,classified_count,unclassified_count,partial_classification_count,industries,topics,scope,reason`。归属身份为类别、来源、明确代码、名单日期，同名不同代码或同代码不同名单日不合并。每组保留`count,items,members_observed_date,source,state,max_board_count,board_counts,unknown_board_count`，同一证券在同一组只计一次，按家数降序、同家数最高已核验板数降序排列。界面按行业/概念分别展示，可展开全部分组及逐股代码、涨幅；只有涨停池有可核验的当前连续板数及最高板数，跌停/炸板池不套用连续跌停或连板高度。成员归属非互斥，各组家数不能加总成来源池总数，不代表全板块涨停家数或上涨因果；归属未核验数、部分覆盖和来源缺口仍保留。
- `directions`：同来源代码、同类别板块的前日与本次涨幅事实对照，当日板块强弱样本，以及按昨日已保存人工主线原文归组的观察股票反馈。`watched.items`可按同一证券代码关联本份快照中严格前日、已核验的涨停事件或兼容旧版`cohort_feedback`，复用遗漏的`previous_board_count,previous_close`和带日期来源理由；关联不到则保持未知，重复或相互冲突的高度不硬选，不用今日连续板数填昨日。原人工观察不因关联被改写。无可靠代码对应时不按同名合并；当日领涨不自动称为新主线，人工主线样本涨幅不代表整板块收益或上涨因果。

报价表、涨停/跌停/炸板池明细、反馈表和板块表支持表头本地升降排序，状态以`aria-sort`及箭头标注；数值缺失在两个方向均排末尾，排序稳定且不改原数组。排序先于分页或展示条数截取，切换排序回到第一页；完整样本统计、来源数量和覆盖不受影响。可选列按完整表格样本决定是否展示：全无有效值时隐藏，已知0保留，部分缺失单元格显示“—”；股票身份与必要的来源/日期/缺口说明继续保留，不能把隐藏空列等同于数据完整。行业/概念、来源原因等文字表头和内容统一左对齐，数值列右对齐，不按固定列序号猜类型。池内和原关注股票同样复用本份快照已返回的带名单日期归属、已核验昨日高度及来源题材，不读取当前题材库补历史。

今日梯队提供“复制全部代码”和各高度组“复制本组代码”入口，复制完整来源样本中的有效六位股票代码，而非当前可见滚动区域或分页。只接受六位证券身份及明确SH/SZ/BJ后缀，复制时去掉市场后缀、保留前导0、按展示顺序去重，每行一个代码；不包含名称、表头、板数、涨幅、题材、原因或注释，无有效身份时不复制占位符。单只股票代码旁提供复制入口。复制只操作本地剪贴板，不修改快照或创建任务。

小时热榜使用本份快照的原代际归属；事件标签只按已核验同代码池状态和严格前日样本生成：本次首板/N连板/涨停用红色，本次或昨日跌停用绿色，昨日已核验首板/N连板用红色，炸板或池冲突为中性。没有对应证据不打标，不按涨幅猜涨停、跌停或昨日高度。布局按工作台实际可用内容宽度自适应，反馈卡与高位梯队自动换列，首板双列在内容宽度不足时改为单列，避免仅按窗口宽度造成挤压。

板块名称按快照中明确来源代码打开板块K线，旁边提供独立“成分股”胶囊标签，不按同名猜测。领涨、偏弱和跨日对照的`member_limit_up_count/state/reason`为成员样本涨停家数：完整覆盖可显示0，部分覆盖仅正数下界显示≥N，其他为null；不能以涨幅阈值猜涨停。新采集直接冻结这些统计，旧快照来源缺失不补当前成员。午评入口的图表最晚截至严格早于采集日的已发布收盘日，详情注明午评日及图表截止日，成员默认指定日历史范围；成员缺档保持缺口，不拿当前成员回填旧午评。显式切换成员范围不改写午评统计，从详情返回K线或个股图表仍受午评截止日约束。

- `temperature`：`version='noon-temperature-v1'`，返回`state,score,label,components,coverage,scope,reason`。这是独立的本地显示规则，纯计算本份午评及严格前日样本，不调用盘后情绪模型、不写策略输入。五项固定权重为涨跌广度25%、当前封板样本20%、涨跌停对比20%、昨日强势样本20%、指数支持15%。各项分值依次为`上涨/(上涨+下跌)`、`涨停/(涨停+炸板)`、`涨停/(涨停+跌停)`、`昨日样本当前上涨数/当前可比数`及四指数上涨支持比例，均乘100；指数平盘计半份支持。广度要求名录完整枚举且可比覆盖至少95%，昨日项要求严格前日来源与当前报价交集至少95%；事件池重复、覆盖不全或两池交集冲突均不硬算，零分母未知。四指数必须全部有效。五项全部有效才按固定权重合成0—100分并保留一位小数；缺失不补零、不重新分配权重，`score=null,state='unavailable'`。区间为`[0,20)`偏冷、`[20,40)`偏弱、`[40,60)`中性、`[60,80)`偏暖、`[80,100]`偏热。`components`逐项列`weight,value,contribution,evidence,reason`，界面可展开核对。该温度不是官方指标、收盘确认、预测或策略信号，跨域时点不保证同秒。
- 涨停、炸板和跌停池的明确零结果须为扶摇同一午休窗口内的成功回执。核对同日请求、对应来源池、来源时间及明确的空列表和首页分页（`page=1,size=200,pages=0,total=0`）后，记录`state='observed',count=0`。旧快照仅在符合该形态且原件双SHA、任务归属、路径及采集时点均通过核验时，按`limit_up`、`limit_break`、`limit_down`派生`workbench.verified_empty_event_pools`供界面和温度使用，原`workbench.limit_down_source_pool`继续提供相同跌停投影。不可变快照与SHA不变，缺报、未留存和校验失败仍为未知。已核验无涨停时首板与连板家数均为0，最高板数为空。温度的两个对比池同时为零时相应分项保持未知。

- `afternoon`：有界的`{kind,object,label,evidence,condition,scope}`列表，把具体股票、板块或市场对象、午间证据与后续核对条件并列。早盘高低和本次午间价仅作为事实参照，不生成策略条件、交易信号、跟踪轮次或回测事件。

各区域保留`state/reason/scope`及有效/缺失数量。派生展示版本变化不伪装成原采集时已经生成的分析；原快照事实及原有摘要继续可核验。

清理接口`app.services.noon_retention.after_increment(job,result=None)`，仅带新冻结参数`noon_retention_policy='noon-retention-28d-v1'`的increment，在主行情已发布或核验no_change后由worker调用。以已核验发布交易日D保留D-27至D，清理更早午评，未跑增量暂留。失败不撤销主发布，记录警告，下次增量补清；备份pin、业务引用、共享原件受保护。正文删除需SQLite连接上的`noon_purge_allowed(snapshot_id)`精确授权，普通连接默认拒绝。清理不删除永久名额、盘后复盘、行情、策略、跟踪或备份；历史显示purged。旧冻结任务不扩大范围。

每轮最多核验32份到期正文及32个到期终止任务，原件总预算仍为4096份、256MiB，不遍历历史原件目录。`market_noon_purge.manifest_json`内部保留`check_sequence`逻辑核验轮次及`last_checked_at_ms`实际检查时间；候选按未检查优先、最久未核验轮次及日期轮转，业务引用、来源清单身份/SHA异常或不可核验终止清单的保留项不会永久挡住后方正常到期项，后续已核验no_change增量同样推进。坏清单的所有原始TEXT仍参与对健康对象的引用保护，不因该行核验失败而忽略其引用。清单未验收时的warning回执不充当冻结删除清单；仅`inventory_verified=true`的原清单约束中断后的精确续清，保护对象始终保留。

## 页面与验收

盘面工作台入口`noon`，共享盘面view date；提供“今天”切换，不悄悄改变其他作用域。左侧提供“看整体、昨日接力、今日梯队、主线方向、午后观察”五步阅读目录，以及昨日原复盘入口；点击只切换本地内容、定位锚点，不改URL或创建请求任务。右侧上下箭头依序切换这些区域及昨日复盘，首尾禁用；支持`Alt+↑/Alt+↓`，输入框、可编辑内容及弹窗内不拦截。小屏收成顶部导航，定位避让工作台顶栏。摘要、昨日背景和反馈中的上涨家数及正涨幅用红色，下跌家数及负涨幅用绿色，平盘及零涨幅保持中性；板块成员上涨家数标红、下跌家数标绿，名单日期与覆盖数量保持中性。颜色只改变展示，不改写原文或数值。来源技术说明默认收起在页脚，不设显眼的数据与缺口页签。主屏依次展示市场概况与午间情绪温度、昨日接力反馈、今日连板梯队、主线与板块对照及午后观察；昨日样本明细可筛选首板/连板并分页查看完整记录，默认按昨日高度降序。昨日原复盘与采集时点、版本和缺口仍保留。小时热榜股票也可打开受午评截止日约束的个股K线。昨日分组来源原因优先冻结的前日池理由，其次原人工主线，最后只能单列本次午间来源线索；分别标注`theme_date/theme_source`，禁止把今天线索伪装成昨日归因。行业/概念归属与来源原因分别列出，名单日期不伪装成昨日原因日期。当天提交后禁用按钮；后台任务仅轮询本地状态。图表详情仍标实际收盘日，不把午间最新价写成close。

验收必须覆盖唯一日并发/请求幂等、已失败日不可新建、历史缺采与清理区别、周末/来源假期/过窗/跨日、来源分页不稳/缺报/时间缺失、冻结昨日版本与未来信息隔离、28日边界及no_change清理、清理失败不影响行情、来源SHA/单位/收益对账和既有盘后/策略回归。工作台另核对分组与全量统计、空样本与缺报、池状态冲突、历史背景缺失、同名不同代码不合并、旧正文SHA不变、历史查看不读取新事实，以及筛选分页不改变均值/中位数。真实演示在独立恢复副本或隔离数据目录，不迁移现用唯一业务库。

昨日接力、昨日炸板与跌停样本也分别提供同形的行业/概念数量分布（`previous_feedback.classification_counts`、`negative_feedback.cohorts[*].classification_counts`）。板数与最高高度只用严格前日已核验高度，成员名单日期独立列出，不以今天板数或成员归属推断昨天原因。默认展开昨日炸板/跌停、高位回落和昨日原关注股票明细，仍可手动收起。行业分布展开名单中的股票统一使用蓝色透明背景链接；没有可用历史K线时禁用并说明截止日缺口。

行业/概念分布的宽版区域每类只展示家数前7个方向；并排的昨日炸板/跌停卡与短线三池区域每类前5个，不再提供“其余”入口。行业/概念总数、统计与代码复制范围仍按全量事实计算。

行业与概念标签各保持单行，内容区较窄时允许横向查看，不把方向标签自动折成多行；展开单一方向的股票名单不受该限制。

午评采集提交后至排队/运行结束，前端复用盘后增量的全局进度遮罩、焦点约束和业务区失活，禁止其它工作台操作；成功后自动解锁并回到该日午评重新读取，不另加确认步骤。跨页签与重新打开应用通过本地任务活动及该任务状态恢复运行提示；请求状态未确认、网络异常时不提前解锁。原盘后增量的结果确认流程不变。

未确认提交的GET对账不能凭`not_collected`、无任务的`available/purged`或其他请求的终态释放锁。创建需核对日期、任务类型/操作及`request_key`；恢复需冻结原`task_id`与提交前`logs[].seq`上限，旧`paused/failed/cancelled`不能代表本次恢复结束，仅当前活动状态或上限之后新增`queued`事件证明已受理，随后才认可其终态。直接成功POST回包可确认该请求已经处理。确定拒绝受理的HTTP 400/401/403/404/405/409/410/422/423/429允许释放，网络错误、408及5xx保持待确认；拒绝后的本地日账本读取最多等待8秒，若发现其他活动午评则原子转接其身份，其余情况释放被拒绝请求。确认过程不重发POST。

午评股票名单显示主板、创业板、科创板、北交所标签，标签按本地既有证券代码/交易所分类；未知不猜测。板别筛选只控制结构化股票名单的展示与当前筛选代码复制，不重采、不改写快照，不重算市场广度、温度、分组均值或行业/概念家数等已保存全样本统计。原文判断与观察事实保留；过滤后的空名单说明“该板别暂无展示样本”，不当作来源缺失。

跌停、炸板的行业/概念分布及整池折叠行显示“平均涨跌幅”：以同份午评中正价且有限涨跌幅的有效报价计算算术平均，字段`mean_change_pct`，保留`valid_count`。原事件家数与归属家数不因缺报价减少；无有效报价时均值为null，真实0%与负涨幅均保留。显示红涨绿跌，不用上涨家数占比替代平均涨跌幅，不按当前上市板别筛选重算全组统计。该均值只描述对应事件范围内的股票，不表示完整行业指数收益。当前涨停池不重复显示该项。

阅读目录按实际窗口或嵌套内容区滚动同步当前章节，左侧高亮与右侧上一节/下一节共用同一位置。手动滚轮、触摸和滚动键会立即接管旧的点击定位；展开/收起及板别筛选改变内容高度后，重新核验锚点，不固定停留在上次点击项。

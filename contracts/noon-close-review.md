# 午评收盘回看契约

## 范围与时间

`noon-close-v1`是在已保存午评与严格同日已发布收盘事实之间追加的本地核验。午评正文、来源SHA、温度、每日一次名额及原人工记录不可改写；不启动供应商请求、不生成策略/跟踪/回测事件。未采集午评、未形成有效正文、等待盘后、部分缺失和到期清理分别展示。

仅新手动盘后增量冻结`noon_close_policy='noon-close-v1'`。唯一worker在主行情真实发布或核验no_change后，以该任务对应的固定来源读取仍在28天保留期内的有界候选；新核验阶段在主任务终态之前完成，之后沿用既有清理和后续链。普通核验失败不撤销已发布行情，只追加警告，下一次授权的成功增量补做。显式暂停/取消不自动继续；旧冻结任务不扩大范围。

核验保留实际生成时点、午评SHA、同日实际输入SHA、来源身份及规则版本。相同午评SHA＋同日输入内容SHA＋规则版本幂等复用，不因整个发布代际更新而重复保存相同历史结果。真实同日更正追加新版本；补齐历史须标实际补充时间，不假装当晚已知。

后续代际只丢失可选同日覆盖、其余已知事实全部一致且没有新增或更正确定值时，保留原核验版本并记录本次覆盖退化警告。来源损坏不等同于缺档，不得静默复用并声称重新核验通过。

## 保存与读取边界

新数据保存于业务SQLite，包含有界的单日派生输入证据、校验和、汇总及重点联合名单逐股结果。派生输入明确不是供应商原始响应；保留来源对象SHA和抽取身份，不新增整套raw响应，不硬引用整份旧发布代际。重点名单是午间三池、昨日事件与原关注、固定成交额/热榜以及收盘新增事件的并集；300/600只是容量测算规模，不是截断阈值，预算外必须明确partial与未保存数。

输入只按指定日读取需要的不可变参考对象、一个月份的日价、四指数和对应日补充，不遍历全历史或全目录。API只读已保存结果，不联网、不打开worker活动DuckDB、不跟随新发布头重算旧页面。汇总不携带完整逐股明细；列表按需分页。标准备份通过SQLite保存核验与派生证据，恢复须重新核对内部SHA/父午评身份。

回看与父午评按同一个28自然日窗口清理，不因晚补或修订重新计时。父快照的授权删除级联删除专属结果，自身外键不能使父快照永久被保护；真实外部业务引用和备份仍受保护。给次日使用的小摘要独立冻结复制，不以跨日硬引用串联全部历史。

## HTTP

- `GET /api/v1/market/noon/close-review?date=YYYY-MM-DD`返回`{date,snapshot_id,state,reason,review}`。state为`available/partial/waiting_close/not_collected/no_snapshot/purged`；无记录时review为null，不伪造采集/收盘/核验时间。
- 顶层`last_check_status,last_check_reason,last_checked_at_ms`描述最近补核验；覆盖退化只改变外层警告，不改变旧review生成时间、原状态、来源或修订。页面须将最近检查时间与原版本核验时间分列。
- review包含`review_id,version,revision,generated_at_ms,noon_sha256,close_input_sha256,source_snapshot_id,source_job_id,backfilled,summary,coverage,classification_counts,observations,tomorrow,gaps`。原午评原件与close输入SHA分别保留；不冒充午间就知道收盘结果。
- `GET /api/v1/market/noon/close-review/items`接受date、明确review_id、section（`all/events/relay/watch/leaders/tomorrow`）、transition、board、classification、sort、direction、cursor及limit（默认50，有界）。返回`{review_id,items,total,next_cursor,scope_total,sort,direction,available_fields}`。available_fields按完整筛选范围判定可选列，保留0，不因翻页跳列。固定review_id避免翻页时悄悄切换修订；游标须绑定同一筛选、排序和版本。
- transition为`all/kept/resealed/opened/new_limit_up/down_opened/new_limit_down/not_resealed/unchanged/unknown`；board为`all/main/chinext/star/beijing`。排序支持`final_board_count/afternoon_change_pct/close_change_pct/noon_change_pct/amount/name`，两方向null均排末尾，稳定排序。
- `codes_only=true`按相同筛选、排序返回完整重点范围的六位代码数组，保留前导0、去后缀、稳定去重，不受当前页影响。前端不逐页抓取全部结果，不复制名称、板数或题材。

## 核验行与统计

逐股行含`instrument_id,name,board,board_label,noon_state,close_state,transition,previous_board_count,final_board_count,relay_state,noon_price,close_price,noon_change_pct,close_change_pct,afternoon_change_pct,change_pct_points,noon_amount,close_amount,amount_delta,industries,topics,classification_keys,source_reason,sections,day_events`。状态为`limit_up/limit_break/limit_down/not_listed/unknown/conflict`；未入池必须有充分覆盖证据，缺报不能视为未封。

- `afternoon_change_pct=(close_price/noon_price-1)*100`；必须同代码同日、正价和口径一致。日涨幅之差只叫`change_pct_points`，单位是百分点。
- 成交额差仅在同来源、同币种单位及相同累计范围可核验时计算。负差/口径变化保留未知及原因；逐条时刻不足时标“快照后新增成交额”，不称精确午后成交额或净流入。
- 午间未封不叫断板。昨日N板、今日收盘涨停且真实N+1板证据一致才确认晋级；收盘未涨停已确认才可确认断板。没有板数字段不猜高度，不解析几天几板替代连续板数。
- 收盘最终封板状态与全天曾炸板事件分开。仅在来源明确声明炸板字段为日内事件时可与收盘涨停并存；来源语义未知或互斥终态冲突须保留unknown/conflict。
- 保持/回封等metric含`count,source_count,denominator,unknown_count,rate_pct`，比例分母仅使用对应固定午间名单中收盘结果可核验项，未知不算失败、零分母返回null。
- summary包含metrics、四指数、午间/收盘/共同可比广度、成交额、temperature、relay、固定amount/hot榜单。原午评温度不改；同规则收盘评分仅依据和覆盖齐全时计算并展示温差，不与不同算法盘后温度直接相减。
- 行业/概念按午间冻结的明确来源代码、类别和名单日分组，不用当前成员回填；组内保留数量、有效数、均值、回封/开板家数、最高实际板数及对应instrument_ids。同股可归多组，数量不可相加。指数价格与成员统计独立，同名跨源不合并。
- 原文判断和观察保留记录时点，只并列客观结果，不自动判自由文本兑现/证伪。不推断回封时刻/次数、分时路径、封单稳定或午后最大回撤；全天高低价不充作午后高低价。

## 次日与界面

明日正常昨日涨停/炸板/跌停与连板高度继续直接读取严格前日已发布收盘事实，不依赖回看成功。新增“昨日回封、昨日午间封板收盘未封、昨日午间未封收盘涨停”作为附加观察组，只冻结采集开始前已保存的小摘要；不重复计入昨日涨停总数，不以事后补核验改写已冻结次日午评。

原午评入口图表仍截止严格此前已发布日；收盘回看可看当日D且D必须已发布，禁止后续日越界。板块名称打开明确代码K线，旁边成分股胶囊独立打开指定日成员。股票/板别/排序仅筛明细，顶部完整统计不重算。名称蓝色透明可点；涨幅红涨绿跌，0中性；封板/回封/晋级红，跌停/开板/确认断板绿；未知/冲突黄色，缺报显示—。

新页面按需加载代码与小摘要，六步导航为整体变化、封板修复、接力结果、行业概念、原关注、明日衔接；左右快捷导航同步。原关注包括固定午间成交额/热榜回看。宽区方向前7、并排区前5，保持单行，不新增“其余”入口。来源、覆盖、真实核验时间与修订可查。

## 验收

必须覆盖：二板→午间炸板→收盘三板→次日昨日三板；午间封板收盘未封；跌停打开；日内曾炸板最终涨停；未知不算失败；未跑午评/盘后/人工复盘；no_change、失败、暂停、重复增量与重启；同日更正追加、晚补时间边界；跨日未来信息隔离；28天及自身/外部引用保护；备份恢复与证据SHA；口径冲突与0值；代码复制全筛选范围；表头null排序、固定修订分页；图表D/D-1；首次/缓存/并发/内存/容量真实测量。合成容量或计算测试不冒充真实行情或完整HTTP/渲染性能。

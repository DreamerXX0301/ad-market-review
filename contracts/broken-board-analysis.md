# 炸板结构与后续反馈 v1

`GET /api/v1/market/overview` 与 `GET /api/v1/market/emotion` 增加只读 `broken_board_analysis`；`GET /api/v1/market/sectors` 在每个方向行增加 `broken_board`。不新建业务表、采集任务、实时订阅或独立菜单，不改变策略、跟踪、回测、情绪阶段及开仓权限。

## 日期、来源与完整性

分析绑定所选完成交易日 `date` 和固定发布代际 `snapshot_id`，版本 `broken-board-analysis-v1`。只复用本地留存的同日来源涨停池、炸板池、原始报价及严格市场交易日历。缺池不以年内涨停补充、日涨幅或 `open_times` 猜测炸板全集；开板后最终回封和收盘仍未封住分开。来源重叠、证券身份/日期/价格冲突、缺报价或非正成交量均影响完整性，不双计。

`state=complete|partial|unknown`。只有同日同源同范围、来源分页和全集证据齐备、证券去重及报价核验全部通过时，才返回精确 `limit_up_count/broken_count/touch_count/broken_rate_pct`；比例为 `broken_count / touch_count × 100`，完整空两池家数为0、比例为null。部分覆盖只返回 `observed_limit_up_count/observed_broken_count/observed_touch_count`，不把观察数当完整分母。`overlap_count/unverified_count/coverage/reason` 解释缺口。

## 市场对象

- 顶层：`version/date/snapshot_id/scope/current/history/previous_feedback/observations/limits`。来源和范围不一致的日期不能计算变化百分点。
- `current`：上述计数、比例及覆盖，另含 `source/scope_key` 和有据 `items`。`drawdown_valid_count/avg_drawdown_from_high_pct/large_drawdown_count` 只评价已核验炸板样本的日高到收盘回落，公式 `close / high - 1`，百分数；明显回落采用不高于−5%的透明观察阈值，不是打板者实际盈亏，也不推算没有来源的涨停价。
- `history`：最多近7个严格市场交易日，逐日保留缺档，不向前借有数据日凑齐7条，不把缺档画成0或跨缺口连续线。
- `previous_feedback`：严格前一市场日已核验炸板队列在观察日的价格与池状态。含 `date/previous_date/state/source_count/observed_source_count/verified_count/valid_count/unknown_count/up_count/down_count/flat_count/avg_change_pct/median_change_pct/large_drop_count/limit_up_count/limit_down_count/return_limit_rate_pct/items/reason`。来源不完整时精确 `source_count` 为null；已知价格反馈仍保留，但有效数及缺失数分别披露。可比收盘收益与涨停反包证据独立，停牌、公司行动不可比或缺少相邻原始报价不入收益均值。完整前日队列和当日逐股涨停证据未齐时，不给涨停反包比例；跌停池或可比收益缺失不单独抹去独立已确认的涨停统计。`event_up_unknown_count/event_down_unknown_count` 分别披露事件状态未知数，明细 `limit_up_state/limit_down_state` 为 true/false/null；只能确认未涨停时 `current_state=not_limit_up`，不声称未入三池。
- 明细使用真实 `instrument_id/code/name/date`，当前结果 `outcome=limit_up|broken`，反馈的 `current_state` 区分涨停、炸板、跌停、未入池、未知和冲突。价格及回落字段缺证据为null。

总览的 `broken_board_analysis` 为轻摘要：保留上述统计、近7日历史、前日比较、反馈汇总、观察结论及缺口，但不返回 `current.items/previous_feedback.items`，也不解析今日与昨日两份成员档案或返回成员分布。需要股票与归属明细时读取市场情绪或按需单日接口；缺少明细字段不代表样本数量为0。

## 行业与概念对象

完整市场报告 `current.items` 中 `outcome=broken` 的炸板队列和 `previous_feedback.items` 补充 `industries/topics/topics_state/topics_date/topics_scope`。当前报告仍保留涨停行的事件与价格事实，但不为这些非炸板队列行装饰未被展示的成员标签。今日明细归属范围日为T，昨日炸板反馈的归属冻结在炸板日P；标签的 `observed_date` 是实际成员采集日，不冒充新的采集时间。来源、板块代码及行业层级分别保留，同名不同代码不合并。`current` 与 `previous_feedback` 的 `concept_distribution/industry_distribution` 按各自已核验炸板队列做去重关联计数，分母为整批队列而不是表格当前页或筛选结果；`sample_share_pct` 是关联股票占这批样本的比例，不是行业内炸板率。多概念可重叠，比例不能相加；分类覆盖与缺档另行披露。

市场情绪公开 GET 的观察日等于最新已发布完成日时，可为 `current` 炸板队列缺失的行业或概念补充本机已核验且采集日晚于 T 的来源成员。已有同类型归属优先。使用补充时，`broken_board_analysis.classification_reference` 提供 `date` 和可选的 `dates`，分别为参与补充的最新实际档案日和实际档案日列表。行的 `topics_date` 仍为 T，补充标签的 `observed_date` 和分布行的 `membership_date` 留存实际来源日。只有元数据逐日列出的实际日期才可作为较晚参考。页面显示“最新行业概念参考”及所用档案日期。

此参考仅用于最新市场情绪的当日股票标签与分类分布。昨日反馈、近7日的单日历史明细、其他页面统计和 AI 仍按对应日期归属取证。行情、事件数量与分母沿用原队列，未知归属保留。补充只读取本地档案，无联网采集、写入或历史回填。

分布行保留 `sample_share_pct` 兼容已有接口，前端不展示占比，改显示关联家数及组内样本的观察日平均涨跌幅。字段为 `sample_date/sample_avg_change_pct/sample_valid_count/sample_unknown_count/sample_basis`，`sample_basis=cohort_equal_weight`：按本批已核验炸板队列中的唯一股票等权计算，仅使用观察日可比 `change_pct`，保留原始精度；有效数与未知数之和必须等于关联家数。0是有效收益，未知不补0，全未知时均值为null；部分可比须在页面披露有效数量。昨日归属P不改变收益观察日T。分组统计直接复用该报告的股票明细，不读取或混入行业、概念指数行情；筛选、排序和分页不改变完整队列均值。

股票明细另有 `board/board_label/board_scope/board_reason`，复用证券代码与交易所分类，范围为 `main/chinext/star/beijing/unknown`，不按涨幅推断板块，也不将当前代码分类声称为历史身份档案。前端支持与行业、概念组合筛选，日期、行情代际或明细范围切换时隔离筛选状态；筛选与排序不改变原队列分布分母。

反馈明细的 `price_feedback` 描述今日可比收盘相对昨日收盘：大于0为正反馈，小于0且大于−5%为负反馈，不高于−5%为明显回落（前端简写“大面”，阈值仅在悬停说明展示），等于0为平盘，不可比为未知。它与涨跌停事件并列，不从−10%或−20%推测跌停。本页“涨停反包”指前日炸板、今日来源确认收盘涨停的队列事件，不代表已经验证K线实体或全部前日最高价的吞没。

`GET /api/v1/market/broken-boards?date=YYYY-MM-DD` 只读单日 `broken_board_analysis`，用于近7日点选后的明细和归属，不计算完整情绪与执行计划，不改变全局盘面观察日。仅按需读取选中日，接口无采集或写入行为；缺档与代际变化不回退到其他日期。

每行 `broken_board` 返回 `date/snapshot_id/state/limit_up_count/broken_count/touch_count/broken_rate_pct/observed_limit_up_count/observed_broken_count/membership_date/reason/details`。仅按该日已留存历史成员做证券交集；缺历史成员不能拿当前成员回填，不因成员缺失清空真实指数行情。成员不完整或池不完整只能显示已观察，不能参与精确家数及比例排序。名称相同但来源代码、类别或行业层级不同不能合并。

同股可关联多个概念，题材计数不可相加当作全市场家数；关联不是上涨或炸板的因果归因。题材触板样本数与比例同时展示，小样本不贴“最弱主线”等结论。

## 昨日板型与本地明细筛选

市场情绪与单日炸板明细按观察日D的严格前一市场交易日P补充`previous_event_version=broken-board-prior-events-v1`、`previous_event_date`、`previous_state`、`previous_board_count`、`previous_event_source`、`previous_event_reason`。只复用P日已留存的同源涨停/炸板池与P日原始正成交量收盘核验；来源日期、价格或事件冲突保持未知，不向更早日期寻找可用板型。P日收盘涨停且来源具体高度为一致正整数时显示“昨日首板/昨日N板”；高度缺失、字段或重复来源高度矛盾不默认首板。P日炸板只显示“昨日炸板”，不推算冲击N板、不作为连板晋级。昨日炸板今日反馈队列本身的P日事实也只标昨日炸板，不借P前一日高度标成P日连板。

`previous_state=limit_up/limit_break/none/unknown/conflict`与今日收盘状态独立。`none`仅在P日同源涨停与炸板池均完整、该股P日报价有效且未列入两池时返回；池缺档或该股证据不足为unknown。两者均不展示事件标签，但不得将unknown解释成昨日已确认没有事件。标签只消费新派生版本和相符日期，旧报告无字段时保持未标记，不重写原始来源或SHA。

明细新增“昨日板型”列，默认昨日已确认板高降序、同高度今日涨幅降序，缺失涨幅末尾；无高度的昨日炸板组先于未标记组，各组再按今日涨幅降序。列头仍可切换板高、今日涨幅、距高点等排序。快捷筛选为全部、昨日首/二/三/四板、昨日五板及以上、昨日炸板、未标记；五板及以上只合并筛选，逐股仍显示真实8板等具体高度。快捷数量来自本次完整返回的原队列，不随板型、上市板块或行业概念筛选缩小；三个筛选维度取交集，排序后分页，导航沿当前筛选顺序。日期、行情代际或明细模式切换时重置或隔离筛选，不改变上方总体统计和本队列归属均值。

近7日完整情绪报告已读取P日，不增加报价请求；按需单日炸板明细只把同一次批量读取扩至P+D。行业/概念的单日结构装饰仍只读取D，不扩大到前日；每次最多一次有界`history_for_instruments`调用，不逐股请求图表、不联网、不扫描全历史。当前服务直接构建报告，没有另造持久派生缓存；新派生版本用于区分旧报告标签能力。

## 展示、性能与验证

总览只显示结构摘要并链接市场情绪；市场情绪展示当前结构、近7日与昨日炸板反馈；行业与概念沿用原联动页面增加“炸板分布”，不创建重复模块。明细上方分布标签固定行业一行、概念一行，按关联家数优先展示，超出隐藏，不提供展开；不得将视觉截断后的标签数当成分类总数，隐藏标签不参与键盘焦点。名称沿用K线入口，代码沿用复制按钮，明细排序分页不改变全队列统计。

复用调用方已经读取的行情行、参考资料和历史成员；报价读取只针对所需近7日事件证券及反馈相邻日，批量且有界，不逐股全历史读取、不扫描全部板块历史、不联网、不写行情或业务表。缺证据不能缓存成完整事实。

验证覆盖完整/空/缺池、重复及重叠、同源范围、错误日期/报价、停牌/公司行动、严格前日、未来资料隔离、历史成员缺档、多重归属、排序分页和读入边界。该功能为留存事实描述，不提供未经历史验证的预测胜率，也不以固定炸板率阈值确认退潮或禁止开仓。

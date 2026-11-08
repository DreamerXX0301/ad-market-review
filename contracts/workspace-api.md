# Workspace API v1

根路径 `/api/v1`；成功直接返回对象，列表通常为 `{items,total}`，错误为 HTTP 4xx/5xx `{detail}`。SQLite 是业务事实来源，浏览器不保存策略或跟踪状态。盘面日期不改变本页业务日；执行/入池只用当前已发布业务日，历史运行读取冻结结果。

盘面总览的成交额 TOP10 近7交易日活跃字段与方向证据见 [成交额活跃契约](amount-top10-activity.md)；行业与题材的涨幅榜、成交额榜活跃见 [板块七日活跃契约](sector-seven-day-activity.md)，各自使用固定口径，不随当前表格排序改变。所有标记只读本地留存数据，不联网创建任务。

## 题材与多归属

| 接口 | 作用 |
| --- | --- |
| `GET/POST /topics` | 检索或创建分类；GET 支持 q/kind/include_archived |
| `GET/PATCH /topics/{topic_id}` | 详情、修订名称/别名/描述/归档 |
| `POST /topics/{topic_id}/members` | `{instrument_id,action,reason?}`，action 为 include/exclude/reset_to_provider |
| `GET /topics/{topic_id}/history` | 来源与人工覆盖审计 |
| `POST /topics/initialize` | 导入本地已有真实目录，不等于远端全量采集 |
| `GET /topic-members` | 多分类交并集、精确当日行情及全量排序 |
| `POST /topic-members/associate` | 多股票、多分类原子追加关联 |

来源成员和人工覆盖分开；撤销人工修改恢复来源集合，不抹历史。来源成员未完整时 `member_count=null`，`observed_member_count` 是样本下界；完整空集才为零。归档分类可读，不可新增关联。

联合查询使用重复 `topic_ids`（1–20 个），`match=any|all`、query、sort_by、sort_order；可选重复 `boards=main|chinext|star|beijing`（主板、创业板、科创板、北交所，最多4项，不接受重复或其他值）。省略 boards 时不限；指定多个板块取其并集，再与分类交并集、搜索结果取交集。按当前已核验股票名录复用股票范围的板块分类，不从没有名录身份的成员代码猜测；未知身份在不限时保留，指定板块时不匹配，勾选四个板块不等于包含未知的“不限”。返回 `selected_topics/members_complete/business_date/quote_date/snapshot_id/warnings/boards`（boards 按上述固定顺序回显）；行含身份、`board/board_label/board_scope/board_reason`、topics/industries/matched_topics、精确当日报价及统一个股统计。board 未知为 unknown，原因非空；当前名录板块不是历史上市身份。无当日报价保留行且行情为 null，不以旧值替代。

排序支持 change_pct/amount/turnover_rate/close/name/code/return_ytd_pct/change_30d_pct/limit_up_ytd/last_limit_up_date/sessions_since_limit_up；上市板块在历史指标批量计算前筛选，完整过滤结果与 total/metric_coverage 一致，服务端全量排序后前端每页25只，切换板块回到第一页。null 在两个方向均最后，下界不作精确次数排序。筛选不改变当前业务日，也不写入分类归属或已选股票的其他关联。

批量关联 body 为 `{instrument_ids,topic_ids,reason?}`，最多 100 股、20 分类。全部验证后同事务追加 include，保留其他归属；已有有效关系 unchanged，被人工排除的可重新纳入。返回 added/unchanged 及数量。它按当前有效状态幂等，不承诺跨后续人工修改的请求重放。

## 当日主线归因（手工证据 V0.1）

以下为已存手工数据的后台兼容契约。当前“当日主线”页面已改为全自动只读归类，不读取这些接口，不提供补录、更正或审计表单；已有记录、历史及接口不删除。自动来源分类不受旧手工修订状态控制，字段见[Analytics API](analytics-api.md)。

与 `topic/topic_override`、静态行业、来源概念成员、涨停池原始原因独立，不根据名称、关键词、人数或事后涨幅自动归因。不联网、不创建采集或研究任务，不读取 worker active DuckDB，不写行情文件。

| 接口 | 作用 |
| --- | --- |
| `GET /market/mainlines?date=YYYY-MM-DD&known_at_ms?` | 每只证券在该日的最新完整修订；可按真实系统记录时间截断 |
| `GET /market/mainlines/history?date=&instrument_id=&limit=100&offset=0` | 指定日、证券的完整审计分页，按 revision 降序；limit 1–500 |
| `POST /market/mainlines/revisions` | 一次原子追加全部归属状态；新增、更正、撤销均留历史 |

写入体：`{date,instrument_id,expected_revision,action,change_reason,attributions}`。`expected_revision` 首次为0，之后必须等于服务端当前revision；冲突返回409 `{detail:{message,current_revision}}`，客户端保留草稿、读取当前版本并由用户核对后再提交，不能静默重试覆盖。`action=replace` 需要1–12条归属；`revoke` 需要空数组；两者均要求非空 `change_reason`（最多1000字）。撤销后当前状态为空，不删除旧记录；移除单条归属使用replace并保存其余完整状态。

每条归属字段如下：

| 字段 | 约束与含义 |
| --- | --- |
| `mainline_name` | 必填，1–80字；准确手工录入的该日主线名称，不连接或修改目录分类 |
| `is_display_primary` | boolean，默认false；每股每日最多一条true，仅来源转述或用户判断可选 |
| `role` | `unknown`（默认）/ `core_candidate` / `followup`；后两者为用户标记，不等于已验证龙头地位 |
| `evidence_status` | `source_claim` 来源转述 / `user_judgment` 用户判断 / `unverified` 待核验（默认）/ `conflict` 显式冲突 |
| `source_name` | 必填，1–200字；注明是用户转录何种材料，不将文字中的供应商名称变成官方认证 |
| `source_reason` | 可空，最多2000字，保留来源原因原文，与主线名称、催化线索分开 |
| `evidence_ref` | 必填，最多2000字；网页、附件ID或本地证据定位，只保存引用文本，不抓取、不上传、不保证文件已归档 |
| `source_published_at` | 可空；带时区ISO时间，不晚于系统实际记录时刻；未知不得以交易日15:00填充 |
| `evidence_sha256` | 可空；用户登记的64位十六进制，不表示系统已打开文件并核验内容 |
| `catalyst_note` / `user_note` | 各可空、最多2000字；催化线索与用户解释分开，未经独立核验的公告/新闻说法不能标成已证因果 |

所有写入均由服务器固定 `source_kind=manual_user_entry`。客户端不可设置 `revision_id/assertion_id/recorded_at_ms/first_known_at_ms`，不接受未声明字段；未知时间为null。API仅校验输入格式与来源说明，并不认证引用材料真实性。源码及017迁移没有真实主线种子、用户截图或预置证券归因。

成功返回 `{date,instrument_id,instrument_name,revision,revision_id,action,source_kind,change_reason,recorded_at_ms,supersedes_revision_id,attributions,display_state,display_mainline,secondary_mainlines,display_basis}`。attributions另带服务器生成的 `assertion_id/first_known_at_ms`。只调整展示主组时保留连续修订中同一陈述版本的ID和首见时间；内容、证据、状态、角色、说明改变，或撤销后重新录入，产生新陈述版本，旧时间仍在审计中。首见时间描述该陈述版本首次进入系统的实际时刻，不能冒充材料原始发布时间。

展示规则按证券去重：有任何显式conflict，进入单一 `conflict` 区；否则有唯一选定主组使用它，或只有一个可用名称（source_claim/user_judgment）时使用该组，状态 `attributed`。多个可用名称而未选主次为 `primary_pending`；只有待核验陈述或没有归属为 `unattributed`。同名主线的多来源保留全部证据，只占一个展示组；多归属本身不自动断言冲突。`secondary_mainlines` 为其他可用名称，未核验/冲突陈述完整保留在attributions中。

列表返回 `{date,known_at_ms,items,total,notice}`，total为该日有历史修订的证券数量，含已撤销记录，**不是确认涨停家数**。历史返回 `{date,instrument_id,items,total,limit,offset,notice}`。GET只读SQLite，即使当前行情目录已滚动也可读取旧审计；`known_at_ms` 为正整数毫秒，按修订的 `recorded_at_ms<=known_at_ms` 选取当时最新状态，不按来源发布时间倒灌。毫秒相同的修订用revision排序；不能把毫秒精度当作更细的先后证明。

首次写入在事务外校验本地已发布名录中的明确个股及交易日历；保存事务再校验 `date<=business_clock`、乐观锁、服务器时钟不倒退，并仅追加一行。现有记录可在名录滚动后继续更正/撤销，使用其既有审计证券身份；当前名录不证明历史上市状态。017新增STRICT表 `market_mainline_revision`、索引和追加链/防更新/防删除触发器，无来源、行情或任务回填。

前端“当日主线”只使用 `ladder.system_mainlines` 自动归类结果。重点方向、其他方向与事件、个股来源线索及来源缺口分别展示；没有待整理或需要用户补充的工作。高标按全部同日确认样本独立核验，来源缺档不由旧手工陈述、当前目录或未来行情补齐。既有手工API的写入、乐观锁、撤销、时间与审计语义保持上述兼容契约；自动页面不会调用写接口、创建任务或改变旧记录。完整连板和目录关联使用原视图，不复制全量列表。操作见[当日主线复盘](../docs/界面导览.md#8-当日主线复盘)。

## 个股策略与条件

`GET /strategy-fields` 返回字段 type/operators、周期、fixed_period、整数边界及场景限制；前端依白名单构建，服务端最终验证，不执行用户 Python/SQL/JS。

前端条件组的“快捷添加条件”默认收起且不预选模板。选择、修改参数、取消都不调用规则onChange；仅点“加入条件”才把预览的标准条件加入当前草稿，之后仍须保存策略。历史where/then各自有入口，单叶旧结构打开面板也不重写。all/any中的组合始终作为一个原子子组加入，不拆散到OR。

- `GET/POST /strategies`、`GET/PATCH /strategies/{id}`：name、period=D/W/M、definition、policy、trade_rules、hot_universe；修改追加版本，旧运行冻结。
- 个股策略列表及详情返回 `run_availability={can_run,state,business_date,completed_run_id,reason}`；`state` 为 `ready/completed/unavailable`。按当前已发布业务日核验，与盘面回看日期和电脑自然日期无关。同一策略在相同实际规则、周期、复权、热点范围、买卖参数、引擎及有效输入下已有成功完成结果时不可新建重复运行，零命中或候选含unknown也不等于任务失败。只改名称/跟踪政策、空保存或将数值3改为3.0不解锁；实质参数、新业务日、发布快照或本地题材修订变化可再次运行，改回当日曾成功执行的配置仍复用其已完成状态。
- `POST /strategies/{id}/runs` 使用 `stock-daily-once-v2` 同日复用契约：先在写事务外准备补充依赖证明，再在现有短 `BEGIN IMMEDIATE` 事务内、创建job前核验已发布代际、本地题材修订及已验证文件身份。非冻结队列的新请求命中已完成同配置/输入时，事务外返回200原 `run_id` 与结果，响应额外标明 `result_reused:true/reuse_reason`；不新增job/run/candidate或运行选股计算。`can_run:false,state:completed`表示不需要再次计算，前端仍可点击“查看今日结果”。原 `request_key` 的幂等重放仍原样返回冻结原结果，不因后来输入变化而改算；失败/取消且尚无成功完成结果可以重试。
- 涨停、前日涨停、滚动一年涨停次数、历史涨停事件和热点范围依赖实际已发布的 `public_pools/annual_pools/annual_down_pools`。`universe_definition.selection_guard.supplement_input` 保存小型SHA证明；主行情未变但这些补充输入变化时，新请求重新计算，旧run与原始证据仍保留。冷核验检查索引及嵌套CAS实际SHA，暖命中仍逐文件核对身份；不遍历历史目录、不展开年度行情树，不在写事务内首次hash正文。保存结果前再次在事务外核验，再进短事务比较，拒绝保存混合输入；新批量子项也冻结并重核此证明。合法partial覆盖可复用其中逐股unknown，缺档不填零；损坏或超过有界证明预算的输入不取得可信复用证明。旧运行缺此证明时，依赖补充池的规则需新算一次；纯技术规则保留原兼容复用，不因无关资金或板块K线文件变化而重算。
- 新个股批次复用已完成相同配置的策略，只为待运行项创建筛选与跟踪任务；混合批次回执新增 `skipped_completed`、`skipped_completed_count`、`requested_count`，`total_count` 仅计实际排队项。全部已有结果时返回200只读回执：`receipt_only/result_reused:true`、稳定 `saved-results-*` 标识、`job_id:null/auto_add:false`、零新步骤，items引用原run/版本/业务日；不创建任何任务，不记录为新的持久化批次，前端可直接展开已保存结果。其 `total_count/completed_count` 表示可读取结果数，不表示新计算数。原批次请求重放与已冻结旧批次的继续仍保持原范围及原授权；板块策略不受此个股复用限制影响。已保存命中不会因为结果复用自动加入跟踪，需从原结果手动选择；同步后的重新筛选与自动加入跟踪继续由既有两个独立设置控制，不默认为用户开启。
- 前端“复制策略”只深拷贝当前策略的 name（默认追加“副本”）、period、price_basis、definition、hot_universe、trade_rules 与 policy 的可编辑字段，不带 strategy_id、版本、自动运行配置、运行结果或跟踪记录。打开和取消仅改前端草稿；确认保存使用原 `POST /strategies`，创建独立标识、规则V1及政策V1，不 PATCH 原策略、不应用到原跟踪轮次、不自动运行。后续调整副本与原策略独立。
- definition 为叶 `{field,op,value,period?}` / `{field,op,right_field,period?}`，或嵌套 `{all:[...]}` / `{any:[...]}`；兼容旧 logic/conditions 输入。
- 普通叶默认策略周期，也可逐叶 D/W/M；W/M 使用最近应闭合且有效周期，不能缺最新后退到旧柱。固定日线字段必须 D。右字段只能是兼容白名单数值，常量专用字段不能当右字段。
- 个股运行在同一规则内一次解析周期、结构与BOLL参数依赖；普通D/W/M指标、底部结构和每个BOLL参数组合分别有界缓存，仅保存固定数据根、行情代际、观察日、周期、算法及已核验来源闭包对应的小摘要。每次运行先核验实际价格、公司行动和日历来源，再允许命中时省略完整历史展开；旧适配器缺少该来源证明时保留原有有界读取。未命中按最多32只读取原始完整指标种子，不截断递推历史；W/M入池价仍独立读取观察日原价。缓存返回深拷贝，身份、题材与用户政策不进入技术缓存；unknown及其原因也按同一来源隔离。保存的完整命中证据、非命中精简证据、规则版本、三态判断和旧运行记录均不改变。
- 放量/缩量使用已有布尔 `volume_expand` / `volume_shrink`，跟随该条件周期；当期与前一可比周期均须有可核验的有限正成交量及有限正倍数，分别严格比较倍数 `>1` / `<1`，相等时两者均为false。当前或对比周期零量、缺量、缺报或不可比时两者为null，`==false`与`!=true`也不得将未知变为命中；日K比较前一市场交易日，W/M比较前一完整周期。数值 `volume_ratio` 保留既有显示与条件语义：例如当前零量且前期有效时可能为0；它不单独证明存在有效成交的“缩量”。不修改已保存条件、旧运行或图表指标。
- 含上述布尔字段的选股或买卖规则使用 `workspace-v16-volume-comparison-v1`；新手动运行沿已有机制追加同定义的新计算版本，旧选择结果保留，不自动重跑。受限回测使用 `restricted-next-open-v6-volume-comparison`，版本纳入输入复用键；旧完成报告继续可读，旧未完成任务拒绝使用新引擎续算，须手动新建。
- 技术组合快捷入口不新增服务端字段：MACD水上金叉保存为同周期 `all(macd_cross==true,macd_dif>0,macd_dea>0)`；KDJ低位金叉保存为 `all(kd_cross==true,kdj_k<U,kdj_d<U)`，U默认20、快捷输入范围0–100，指当前双线而非前期值；K>D状态保存为 `{field:"kdj_k",op:">",right_field:"kdj_d",period:P}`。每个叶子显式使用同一P；普通入口采用当前选择周期，历史where/then固定D。组合是外层all/any的一个子节点，不能把三项拆进OR。用户后续可编辑各叶子，仍由原有白名单、深度/节点数与三值逻辑核验。
- 卖出区技术组合优先展示MACD死叉、KDJ死叉、MACD水上死叉、KDJ高位死叉与K<D持续状态。普通死叉为前一期快线≥慢线且当前快线<慢线；水上再要求当前DIF、DEA都>0；高位再要求当前K、D都>H，H默认80、快捷输入0–100，等于门槛不算。K<D只检查当前状态，不要求刚死叉。所有快捷均展开成同周期普通叶子，不把“金叉=false”当死叉，不改变已保存条件或卖出启用时的既有默认条件。
- K值范围快捷保存为同周期 `all(kdj_k>=L,kdj_k<=U)`，金叉加范围再包含 `kd_cross==true`；只限制当前K，不隐含D范围。L/U为有限0–100且L≤U，等号有效；低/中/高快捷起点为0–20、20–80、80–100，20/80边界重合，它们是可编辑起点而非互斥状态或收益判断。过滤范围不修改KDJ指标本身的计算参数。
- 股票范围 board 包含/剔除与当前 is_st 在外层独立 AND。当前身份不是历史逐日 ST/上市档案；未知不能当 false。
- `hot_universe` 为独立范围配置，`null` 关闭；启用为 `{window_sessions:7,top_n:10,kind:"all"}`，窗口整数1–60、排名整数1–50，kind为`all/concept/industry`（concept包含人工题材）。在截至当前业务日的最近N个市场交易日（含当日），按已核验收盘涨停股票日数降序、活跃股票数降序、名称/ID稳定排序，股票属于前N名中任一当前分类即可；它不是成交额榜、资金净流入或已确认当日主线。与技术条件独立AND，技术false仍为false。来源日期、原始收盘核验、窗口长度或参与分类成员覆盖不足时范围结果为unknown，不能伪造0、强行入选或将unknown改成false。每次运行一次有界批量核验，补充数据只加载窗口三类涨停/跌停来源，每批最多256股，不逐股重扫全市场。
- 热点配置写入022迁移的`strategy_version.hot_universe_json`，与规则同版本。运行返回`universe_definition.hot_universe`：算法版本、配置、业务日、窗口、排名/计数、覆盖与缺口、当前成员联合证券ID（只存一次）、成员SHA及来源行情代际；逐股checks仅保存`type=hot_universe/result/matched_topic_ids/evidence_ref`，不复制完整排名或目录。运行结束同时复核行情与题材修订指纹，变化则409，旧运行证据不随当前目录修改。当前成员代理不等于历史PIT，回测availability明确不可用，创建及执行层拒绝，不静默忽略热点范围。
- 热点覆盖的`unverified_candidate_stock_days`保留待核验股票日数量，`unverified_candidate_stock_day_reasons`按`missing_raw_close/source_conflict/other_unverified`给出原因计数。来源日完整不代表证券原价与跨源证据全部一致；排名不完整时只能展示已确认事件下界，界面解释数据待核验不等于任务运行失败，不能补零或声称已证明前N边界。
- `close_breakout_high` 使用 `op=new_high,value=N`（整数 1–750），观察日日收盘严格大于前 N 个市场交易日 high，排除今日；缺报/零量/行动不可比为未知。
- `limit_up/prev_limit_up` 分别是当日收盘/前一市场交易日收盘来源事实，是/否均需证据。`limit_up_count_1y` 是截至昨日滚动自然一年次数，非负整数常量，不是年内次数；不完整 actual=null，有限上下界可证明的比较才成立。

MACD/KDJ 数值缺失时，条件 `reason` 说明已有公司行动/缺报重置原因、当前连续可比报价根数及所需至少35根；金叉与死叉需至少36根。根数未知时明确未留存，不填零。缺失发生在右侧字段时使用该字段原因；技术条件的 `indicator_evidence` 按缺失字段保存 `indicator_warmup_bars/required_warmup_bars/indicator_reset_reason/indicator_warmup_reason`，最多两个输入，不附整段行情。该说明使用已算元数据，逐股与历史批量路径一致；不改变指标、三值结果、完整周/月边界、预热、未复权行动重置或 evaluator_version，也不重写已保存运行。

`turnover_ratio_pct` 缺失时，reason 区分观察日有效成交不足与同日换手率未留存，并给出 `observation_date/observation_period=D`。成交额不是换手率，不以当前流通股本回填历史；该说明不补值、取新源或改变条件真假。

历史条件节点为 `{history_event:{window:{from:1,to:30},match:"latest",where:条件树,then:条件树}}`。偏移均为前 N 个市场交易日、不含今天且含两端：近 N 日是 from=1,to=N，自定义前5—20日是 from=5,to=20，指定前 N 日是 from=to=N；范围 1–750。编辑器新建默认前1—30日，读取旧规则不改窗口或版本。latest/earliest 指按距离从近/远优先，不跳过优先位置的未知去伪造匹配；绑定后 then 不满足也不改选其他事件。where 支持日 OHLCV/指标与同日 `limit_up` 的自定义 all/any，不支持递归 history_event、prev_limit_up、历史题材/身份、滚动涨停次数或跟踪基准。

`match="any"` 表示范围内任意一次完整满足。按近到远检查，每个候选必须用同一事件的 where、then 和已启用 price_path；第一个整体为true的事件才成为 `selected_event`，停止继续搜索，不代表已经枚举所有满足事件。较近日期未知不妨碍较早的完整true证明存在；没有true但存在未知候选时结果为null，完整已知但均不满足时为false。false/null时不绑定回退事件、不生成命中横线。不能把一次事件守位成功与另一次事件收复成功拼成命中。它与where内部“倍量或涨停任一条件”是两层独立选择；旧规则不会自动切换为any。

then 是独立今日条件树，例如 `{all:[{field:"change_pct",op:">",value:3,period:"D"},{any:[{field:"macd_cross",op:"==",value:true,period:"D"},{field:"kd_cross",op:"==",value:true,period:"D"}]}]}`，不要求比较事件价；也可用 `{field:"close",op:">",right_event_field:"close"|"high"|"low",period:"D"}` 比较观察日收盘与本节点事件价。事件绑定互不串用。none口径跨公司行动时事件价比较保持未知；forward口径仅在同观察日锚的行动证据完整时跨行动比较。缺中间有效日报价或来源覆盖不足仍保持未知，独立今日涨幅/技术条件按自身覆盖判断。新编辑器历史内部固定 D；无显式 period 的旧 then 保留旧版本解释。共享历史预备同时保留 where 和显式日线 then 的依赖，保证批量与标量路径一致。

“当前收盘高于事件收盘”对应上面的 `close > right_event_field:close`，只比较观察日与事件日两个收盘，不要求昨日低于事件价或事件后每日站上。“今日重新收复”是可选 `price_path.reclaim.mode=close_reclaim`，还要求昨日晚于事件日、昨收严格低于参照价而今收不低于；它最早在事件后第2个交易日触发。`close_at_or_above` 只要求今日收盘≥参照价，可连续多日满足，也不证明期间每天都站上；前端标为“仅看今日”。三者不能互换，不自动改写既有规则。

事件价格比较叶可追加 `right_event_change_pct=p`，只允许在本事件then的 `field=close` / `right_event_field=close|high|low` 形式使用，不与value/right_field混用。p为有限数值 `-100<p≤10000`；右侧阈值为 `事件价×(1+p/100)`，用未舍入十进制比较。例如p=3、op="<"指今收低于事件价上方3%；p=-3、op=">="指今收不低于事件价下方3%；它不等于只比较事件价的3%。0显式有效，缺少该键的旧叶结构、求值与证据保持。新证据增加原始 `event_price`、`threshold_decimal`、实际相对同口径事件价的 `event_change_pct` 与比较模型，`right_actual`为偏移后的阈值。无法安全表示的数值不输出非有限JSON，也不绕过所选价格口径的可比守卫。上下界可用all组合，等号是否包含由各比较符决定。

历史节点可追加可选 `price_path`，不带此键的旧定义保持原结构和解释。例如事件后守位、昨日回踩及今日回升：

```json
{
  "history_event": {
    "window": {"from": 2, "to": 30},
    "match": "latest",
    "where": {"field": "volume_ratio", "op": ">=", "value": 3, "period": "D"},
    "price_path": {
      "guard": {
        "max_drawdown_pct": 5,
        "require_pullback": true,
        "near_floor": {"sessions": 1, "band_pct": 1}
      }
    },
    "then": {"field": "change_pct", "op": ">=", "value": 2, "period": "D"}
  }
}
```

- `price_path` 必须是非空对象，只接受 `guard` / `reclaim` / `cross_cycle`，可同时设置，所有已启用项 AND，再与 `then` AND。latest/earliest先绑定事件，过程失败不会重选；any逐事件完成整套检查，直到找到最近的完整满足事件。
- `guard.max_drawdown_pct=r` 为有限数值 `0≤r<100`；保护价 `F=事件日收盘价×(1−r/100)`，E之后到T的每个市场交易日收盘均须 `≥F`。不按显示位数或任意epsilon判断，百分比运算使用十进制未舍入值；中途破位后再收回仍不满足。`require_pullback` 为布尔值，省略默认false；启用时E之后至昨日须至少一次收盘低于事件收盘。`near_floor` 必须含 `sessions`（整数1–750）和 `band_pct`（有限0–100），检查最近N个交易日至昨日与E后区间的交集，至少一次收盘在 `[F,F×(1+band_pct/100)]` 且低于事件收盘；N=1指昨日，没有事后交易日时不满足。
- `reclaim` 必须含 `event_field=close|high|low` 与 `mode`，基准来自本节点绑定事件。`close_at_or_above` 是今日收盘≥基准；`close_reclaim` 是昨收<基准且今收≥基准，昨日必须晚于E；`first_close_reclaim` 再要求此前没有E后的相同价格上穿；`high_at_or_above` 是今日最高≥基准；`touch` 是今日最低≤基准≤今日最高。跳空且全天在基准上方只满足最高达到，不算实际触及。
- 首次判断从E→E+1的价格变化开始，按价格事件本身计数；不因当时技术条件或当前模板的额外门槛未满足而重置。以事件最高为基准时，即使E+1已上穿却没有独立的昨日回踩日，也会消耗首次，后来再次站上不能改称首次。当前重新收复仍需昨日晚于E，最早在E+2日触发。
- `cross_cycle={indicator:"macd"|"kdj",require_dead_cross:true}` 核验E/T同类金叉及严格位于E与T之间的至少一次同类死叉；不含价格高低门槛。服务端限定 `window.from=1`、`match=latest`、where仅为对应D金叉等于true（单叶或单元素all），to仍可设1–750。where不能另加K范围、水上或价格限制，也不能用earliest、any或from>1跳过更近金叉；今日K范围与事件价门槛放在then。没有中间交易日或完整已知区间无死叉为false，缺信号预热/报价/行动可比证据保持unknown；不使用阻断后或T后的信号。新模板默认回看60个交易日，并以then显式加入今日同类金叉和close>事件收盘；用户可编辑价格门槛。所称“上次”限该窗口，未找到不能当作终身没有上次。
- 路径固定日线，只读E至当前T；不因外层W/M或回测预备到更晚日期而读取未来。缺报、重复、零量、未知行动或跨行动后的原价不可比，保留unknown。首个不可比点之前已证实的失守或更早价格收复仍可证明false；不把缺口后的低价冒充可比失守。所有子检查和then仍独立留证。

事件结果的 `price_path_checks` 仅在新定义出现，未绑定事件时为null；绑定后含 `type=history_price_path`、路径模型、观察日、result、guard/reclaim/cross_cycle小证据。guard含公式、保护价、最小可比收盘及日期、首个破位、可比/应有天数、回落/近保护价命中或缺口；reclaim含基准、昨今价、触发方式和此前首次价格收复日期；cross_cycle含E/T金叉、期间死叉日期、已知/应有信号日与阻断原因。不给出全区间行情对象。选股、跟踪和受限回测共用求值器；新增定义与回测版本单独标记，旧保存结果不重写，无数据库迁移。

价格过程为个股历史节点能力，板块条件不接受history_event。当前新回测引擎为 `restricted-next-open-v6-volume-comparison`，input/logical key包含版本；不同冻结引擎的未完成任务不得被新请求复用或继续模拟发布，需手动新建，旧完成报告仍可读取。不含上述放量/缩量布尔字段的定义，带百分比事件比较或cross_cycle仍使用 `workspace-v11-event-percent-cross-cycle`；只有原守位/收复的定义保留 `workspace-v10-history-price-path`，更早定义继续沿用其既有v8/v9，不自动重写旧版本。

包含match=any的定义（包括买入或卖出规则）使用 `workspace-v12-history-any`，其历史事件证据模型为 `daily-history-event-v2-any`。只增加新AST语义，不改变旧latest/earliest定义的版本、求值或已保存运行；新AST与输入SHA已隔离。搜索摘要只记录有界计数与首个未知位置，不保存完整历史候选数组。

独立趋势条件 `{"field":"higher_lows","op":"confirmed_rising_lows","value":60,"period":"D"}` 的value为8—750整数，默认60；period省略继承策略D/W/M。回看窗口包含最近N个所选完整周期的低点中心，左边另外需要2根预热；左右各2根且中心low严格低于四邻才确认，等低平台不算。只取最近两次确认低点，不跳过后续较低低点；要求L2>L1，且从L2右侧确认当期（含）至观察期的全部收盘≥L2。破位后收回仍不满足该对结构。完整窗口不足两底为false；缺失/重复/无效或零量报价、未知公司行动覆盖或不可比原价区间为unknown，不补停牌周期。W/M按市场日历完整聚合，观察日后右邻不参与确认。

该条件是独立状态，不自动限制两底在历史事件之后；可放外层all/any及历史节点then（日线），where拒绝。checks含`lookback_periods/structure_version=higher-lows-v1/covered_periods/required_periods/observed_date/confirmation_date`，其中`required_periods=N+2`；evidence含最近两底的date/confirmed_on/price/age_periods、`minimum_close_since_l2_confirmation`、`window_includes=pivot_centers/left_warmup_periods=2/hold_from=l2_confirmation_inclusive/price_basis=none`。另含有界`last_blocker={period_end,kind,reason,market_dates,affected_market_days}`或null，market_dates最多3个，仅说明最近一次缺报、重复、无效成交、行动覆盖未知、未复权行动、日历冲突或首周期边界阻断；unknown原因直接说明阻断日期/类型与连续可比根数、所需根数，例如“2026-07-22存在未复权公司行动，目前连续61根，60根窗口加左侧2根预热需62根”。只包含有界依据，不返回整段行情。仅使用新字段的个股定义使用`workspace-v13-higher-lows-v1`；旧定义版本保持。受限回测支持该条件，报告附`condition_contracts.higher_lows=higher-lows-v1`，输入AST隔离，旧引擎计算行为和旧报告不变；无数据库迁移，也不自动执行策略。

历史 `limit_up` 在选股、跟踪和回测复用同一已发布代际的来源池核验器：逐日期编译来源代码索引与完整来源缺席证据，每只股票再核对该日唯一有效原始 OHLC、收盘、成交量和来源身份；既有日报价标志或 +10% 涨幅不能代替来源证据。缺来源、缺报、停牌、价格/方向冲突均保留 unknown，不用未来池回填。checks 保存来源路径、快照、窗口、日期、偏移、原始价格与原因，不序列化完整历史上下文。回测只为历史 where 放开同日涨停，普通当前涨停及滚动次数条件仍沿用原有回测边界。

### BOLL收口与方向条件

叶子为 `field="boll_signal"`、`op="matches"`，`value`是白名单参数对象；可省略`period`继承策略D/W/M，或逐叶指定。与普通量能、MACD、KDJ叶子组成all/any，不自动加入其他条件、创建策略或执行任务。示例为默认参数：

```json
{
  "field": "boll_signal",
  "op": "matches",
  "period": "D",
  "value": {
    "mode": "breakout_up",
    "band_period": 20,
    "deviations": 2,
    "rank_window": 125,
    "squeeze_percentile": 10,
    "recent_squeeze_periods": 5,
    "require_squeeze": true,
    "middle_direction": "any",
    "slope_periods": 5,
    "require_expansion": false
  }
}
```

`mode`仅接受`squeeze/breakout_up/breakout_down/reentry_from_above/reentry_from_below`；`band_period`整数2–250、`deviations`有限数0.1–10、`rank_window`整数10–750、`squeeze_percentile`有限数1–100（百分数）、`recent_squeeze_periods`整数1–60、`middle_direction=any/rising/falling`、`slope_periods`整数1–60、两个require字段为布尔。未知参数、非法数值与字段比较拒绝；参数对象不执行表达式。服务端字段规格提供默认及参数边界供编辑器使用。

中轨=SMA(close,N)，上下轨=中轨±K×总体标准差；带宽=(上轨−下轨)/中轨。每期阈值只取之前L期带宽，分位按`(L−1)×p/100`线性插值，当前带宽≤阈值为当期收口。上破为`C[t−1]≤U[t−1] && C[t]>U[t]`，下破对称；上/下方回轨分别要求前收严格在相应轨外、当期收盘位于`[L[t],U[t]]`。方向条件开启`require_squeeze`时检查前1至R期至少一次收口，排除当期；`squeeze`模式检查当前收口。中轨rising/falling严格比较`t`与`t−S`，扩张严格比较当期和前一期带宽。关闭的过滤不增加分位或方向预热依赖。

浮点比较的绝对/相对容差均为`1e-12`，容差内按等号处理，避免理论触轨被舍入误差变成突破；`evidence.comparison_tolerance`留证。此容差用于轨道、收口、中轨方向及扩张，不是可调价格过滤。

严格市场日报价及明确行动覆盖构成连续可比区间，缺报、重复、无效OHLC、零成交、已知行动或行动覆盖未知打断预热；完整周/月按应有市场日聚合，观察日后的报价不参与。只返回有界配置和价格、带宽、阈值、前置收口等checks，不序列化整段历史。history_event.where不支持该叶，then支持显式日线；BOLL收口无须发生在所绑定事件之后，需事件后价格过程时仍使用独立price_path。

个股选股、跟踪、受限回测和板块自身策略共用`boll-signal-v1`定义；含该叶的新个股定义使用`workspace-v15-boll-signal-v1`、板块使用`sector-index-dwm-boll-signal-v3`，未使用BOLL的旧定义版本保持。多参数与逐条件周期缓存隔离。checks含`signal_version/observed_date/covered_periods/required_periods`，evidence含`close/upper/middle/lower/bandwidth_pct/historical_threshold_pct/recent_squeeze_date/middle_reference/middle_change_pct/expanding/primary_match/squeeze_match/middle_match/expansion_match`及有界`last_blocker`；带宽和阈值单位为百分数。新回测冻结`condition_contracts.boll_signal=boll-signal-v1`，未完成任务版本不匹配时拒绝恢复，不改变旧完成报告；无数据库迁移，也不改变图表BOLL偏好。用户入口见[BOLL选股条件](../docs/BOLL选股条件.md)。

`trade_rules={entry:条件树|null,exit:条件树|null}` 与选股独立版本化：买入为选股 AND entry，exit 不要求仍满足选股。只接受已支持回测特征；不支持题材、未完整历史涨停或跟踪锚点，不因为 AST 能保存便保证可回测。

### 个股策略的价格口径

创建及修改个股策略可显式提供 `price_basis:"none"|"forward"`。旧客户端省略时，创建沿用none、修改沿用已有版本；新建页面和页面中的默认模板显式选择forward。已有策略可切换后保存新版本；不改写原版本、旧运行或已入池轮次。024迁移只为`strategy_version`增加该列，旧行默认none。

forward复用本地`event_affine`变换，契约为`strategy-local-forward-v1`，求值版本为`workspace-v17-local-forward-prices-v1`。每个观察日T独立锚定，只使用不晚于T的已留存公司行动及行情；固定来源SHA、逐股动作覆盖、证券身份和事件字段核验失败时，价格依赖保留未知，不静默按原价放行。MA/MACD/KDJ/BOLL、结构及历史事件价比较使用同T价格轨；交易日缺报、零量及完整周/月边界仍严格核验，不直接采用图表的报价连续显示模式。历史where(E)的价格字段表示观察日T锚下回看的E值，绝对价格门槛也按该口径解释；它不声称E当时已冻结的可执行信号。回测每个T单独锚定，不能拿回测终点锚评价更早的T。原价日涨幅、涨跌停事实、量比、成交量/额及换手率保留独立原始证据，公司行动日不能因价格轨解除断点就伪造可比量比。

策略返回`price_basis/price_basis_contract`。运行列表与详情的`requested_price_basis`来自该次冻结的策略版本；候选`metrics.price_basis`为实际none、forward_event_affine或unavailable，并保留`anchor_date/adjustment`及失败原因。输入签名与派生缓存包含口径、锚、契约及已核验来源闭包。改变图表偏好不会改变策略口径。

前复权历史窗口另有可丢弃的进程内纯值缓存：压缩体总量最多64MiB、16,384项，单项解压上限8MiB；键绑定数据根、行情代际、已核验来源SHA、证券、观察日锚、窗口与所需字段及算法版本。每次运行先核验实际来源再命中，不能跳过源核验。缓存不保存策略阈值、题材、政策、事件绑定或判断结果；每次生成新历史上下文并重新核验涨停池证据。缺失、损坏、超界或键不匹配时丢弃重算，不据缓存错误认定采集失败。

个股手动执行的准备层可在来源核验后按需使用至多4个spawn纯计算子进程，批次仍最多16/32股；子进程仅接受父进程已读取的有界输入，不打开SQLite、DuckDB或网络，不写缓存、不求值策略、不发布行情。父进程保持确定证券顺序、任务暂停取消检查及唯一业务写入；暖缓存不启动子进程，基础设施失败可记录诊断后走原串行路径，控制异常与计算错误继续传播。完整严格周期输入可按原边界共享，参数改变仍重放当前参数的原始递推。只有完整来源证明与成功行动读取绑定的规范日期准备失败，才能缓存相同unavailable输入；unknown、原因、源指纹、日历和锚均不放宽。年内涨停计数摘要仍逐次核验实际来源及补充文档内容，不缓存题材、政策或策略决定。具体容量与回退边界见[选股执行性能](../docs/日期切换与选股性能.md)。

跟踪轮次冻结入池策略的信号口径，原始入池价与高低价仍独立保存。forward复核在每个T重新投影冻结参考价，不用后来的行情替换原始基准。浏览保持小原价缓存；缺少同日同代际的复核比较证据时，原价最新收盘照常显示、复权比较待核验，不借用昨日核对。政策仍每次重新读取，修改策略当前口径不改变旧轮次。

forward回测冻结口径和契约，信号逐日锚定；真实撮合、费用及限价仍用原始价格。现有权益账本未处理分红、送股、配股，因此回测区间内有公司行动的证券仍按原受限契约排除，不能将前复权价格收益冒充已记账账户收益。旧none任务继续使用原引擎及冻结内容，不自动重跑。

## 个股策略删除与恢复

| 接口 | 作用 |
| --- | --- |
| `GET /strategies?deleted=false&limit=&offset=0` | 默认排除已删除策略；不传limit保留原有完整列表语义，显式limit为1–100 |
| `GET /strategies?deleted=true&limit=25&offset=0` | 只看回收站，支持分页 |
| `DELETE /strategies/{id}` | 同一SQLite短事务将策略及当前可见关联回测移入回收站 |
| `POST /strategies/{id}/restore` | 恢复策略及本次策略删除带走的回测，幂等 |

策略可见性另存于019新增的 `strategy_deletion`，关联回测的可见性另存于 `backtest_deletion`；不物理删除策略版本、运行、报告、任务或输入保留引用，不修改已完成回测内容。策略metadata含 `active_purge/deleted/deleted_at_ms/can_delete/delete_blocked_reason/can_restore/restore_blocked_reason/backtest_count/related_backtests_count/cascade_backtest_count`；后两项表示本次会带走的当前可见回测数，总回测数另列。列表返回 `{items,total,limit,offset,has_more}`，未传limit时为null；删除/恢复返回 `{strategy_id,deleted,deleted_at_ms,affected_backtests}`。已删除策略详情404，PATCH为409。

删除前事务内检查相关选股、批次、跟踪核验和回测任务；排队、运行、暂停或尚未释放租约均409，不自动取消。已取消回测以任务终态及租约释放为准，不因其遗留计算状态仍为queued/running而永远无法清理。删除后禁止新建选股、回测、批次、自动选股或从旧运行新入池，原 `archived=false` 更新不能绕过恢复接口。旧归档策略语义保留，恢复时还原其删除前归档值。

跟踪轮次、入池基准、政策和事件、自选及行情均保留；已有独立跟踪仍可继续核验，不自动结束。恢复不重跑任务、不追加策略版本、不重设基准，也不恢复删除策略之前已经单独移入回收站的回测。删除与恢复均是可见性变更，不构成磁盘空间回收。

## 回收站逐项永久删除

| 接口 | 作用 |
| --- | --- |
| `GET /strategies/{id}/purge-preview` | 只读核对策略及全部关联运行、跟踪、报告的永久删除范围 |
| `GET /backtests/{id}/purge-preview` | 只读核对单份回测的专属清理范围 |
| `POST /strategies/{id}/purge`、`POST /backtests/{id}/purge` | 提交名称与范围确认，排队交由唯一worker清理 |
| `GET /workspace-purges?limit=20&offset=0&status=unfinished&target_kind=strategy`、`GET /workspace-purges/{id}` | 读取清理记录及当前状态；实体清理后仍可查 |
| `POST /workspace-purges/{id}/retry` | 显式续执行原冻结授权，不扩大范围 |

仅回收站中的对象可永久删除。preview返回 `target_kind/target_id/name/can_purge/blocked_reason/confirmation_token/confirmation_text/expires_at_ms/counts/files/protected_watchlist_count/protected_watchlist/retained`。counts包括策略、版本、政策、选股运行与候选、跟踪轮次与事件、政策应用、忽略项、执行设置（`execution_settings`）和回测（`backtests`）数量；files为 `exclusive_count/exclusive_bytes/shared_count`，字节数只指当前可清理的专属报告文件。preview本身不写授权、不清理文件；确认token绑定当前对象、证据、引用和文件范围，10分钟有效，不是身份认证凭据。

提交体为 `{confirmation_token,confirmation_text}`，名称必须完整匹配。对象、引用、自选或文件范围变化、token过期、未在回收站、有相关活动任务或未释放租约均拒绝，不自动重算更大的授权。策略的任意现有或历史跟踪证券只要仍在独立自选，即使是手动收藏、并非该轮次加入，也阻止整条策略的永久删除；返回总数与最多20条保护明细。不能通过结束轮次或删除策略的可见性标记绕过。普通移入回收站继续保留原记录。

确认受理只表示任务已入队。状态为 `queued/cleaning/completed/failed`，另返回 `task_id/business_deleted/counts/removed_file_count/removed_file_bytes/remaining_file_count/can_retry/can_reconfirm/superseded_by` 等。只有completed才表示本次清理完成。业务事务提交后文件清理失败，`business_deleted=true`，不得再宣称策略可恢复；失败重试只续原文件台账，已经缺失的文件不计作实际移除字节。业务尚未删除而原范围失效时，可重新预览、重新手输名称确认生成新授权；旧失败授权被标记superseded，不能重放，unfinished列表排除它。业务已删除则只能续原文件台账。列表支持limit 1–100及offset，返回items/total/limit/offset。已有备份保留，SQLite不会自动VACUUM，返回字节数不等于整个磁盘释放量。

worker在业务事务内重新核对冻结范围、自选保护和任务租约。仅此连接、精确目标ID可通过020修改的八个DELETE守卫；其他连接保持拒绝，UPDATE不可变守卫不变。策略永久清理包括全部版本、政策、运行、候选、跟踪、事件、忽略项及全部关联回测；单份回测不删除策略和跟踪。仅解除本次对象拥有的retention_pin，且仅清理无其他引用的专属backtest_result文件及其本模块head。共享报告、输入行情、其他对象的pin、自选、划线与划线触发事件、任务及发布目录元数据、清理审计和已有备份保留。报告按白名单路径、类型、大小、SHA与实际归属核验，异常保留并报错。

已确认排队/清理中的对象不能恢复，也不能通过恢复父策略间接恢复正在清理的报告。永久清理后的ID不得由初始化、内置策略种子、旧任务重试或旧请求键复活；可显式创建新ID的策略。永久删除没有回收站恢复操作，也不自动整理其他策略、自动发起取数或运行。

## 个股运行与跟踪

| 接口 | 作用 |
| --- | --- |
| `POST /strategies/{id}/runs?response_decision= {request_key?}` | 实际求值并保存全部候选，可选仅返回一个分类 |
| `GET /strategy-runs?strategy_id=&limit=`、`GET /strategy-runs/{run_id}?decision=` | 运行列表及 matched/unmatched/unknown 证据 |
| `GET/PUT /strategies/{id}/automation` | refresh_after_sync、auto_track；默认关闭，auto_track 需 refresh |
| `POST /tracking-rounds {run_id,instrument_ids}` | 仅当前版本/当前业务日 matched 入池；重复与忽略项跳过 |
| `GET /tracking-rounds?strategy_id=&status=&triggered_only=&view=full\|summary` | 当前轮次、进度、行情、题材及提醒，默认完整响应 |
| `GET /tracking-rounds/{round_id}`、`GET /tracking-events` | 详情和追加事件 |
| `POST /tracking-rounds/review {strategy_id?,request_key?}` | 按市场日补核对，按政策版本与日期幂等 |
| `PATCH /tracking-rounds/{round_id}/policy` | 显式迁移政策，追加审计，不改入池锚点 |
| `POST /tracking-rounds/{round_id}/remove` | 软结束；可设置 ignore_reentry |
| `DELETE /strategies/{id}/ignores/{instrument_id}` | 解除忽略重入 |

个股政策使用 alert_logic/exit_logic=`AND|OR`、alerts/exits、duration_sessions（默认 30）、事件日开关（默认 false）。D0 为入池日；未知交易日进度不补零。提醒不结束，死叉不等于“金叉=false”。本轮高/低锚点是原入池快照唯一日 K 的 high/low，不是跟踪累计值或周柱；严格高于/跌破，相等不触发，行动不可比时未知。

个股/板块跟踪列表额外返回 `counts={active,history}`，是当前策略范围内、应用 status/triggered_only 前的真实轮次数；触发提醒数使用事件接口 total。前端分类以数量分段展示，题材只预览两项，完整归属和逐项触发依据按需展开。

个股跟踪页面使用显式 `view=summary`；省略或 `view=full` 保持原完整响应，其它值返回422。摘要仍返回该筛选范围的全部轮次，保留全部排序字段、价格与未知原因、完整政策、当前题材及热点作用域。`entry_basis` 保留完整 `entry_checks` 与原有标量，只省略 `source_lineage/entry_topics/entry_high/entry_low`；行级省略 `topics/topics_snapshot/entry_high_basis/entry_low_basis`；`latest_event/alerts` 保留事件头而省略 `evidence`。图表历史检查、连续导航与同股不同轮次的价格基准不变，前复权 `tracking_reference_evidence` 仍完整。摘要额外返回 `view=summary` 与 `counts.alerts`，后者与同策略范围的 `GET /tracking-events?event_type=alert` 总数相同，跨活动和结束轮次，不受列表状态或触发过滤影响。详情接口及独立事件接口始终完整，政策编辑、题材和事件每次重新读取；摘要不是整页缓存。

上述个股跟踪列表/详情、已保存个股运行列表/分类详情以及自选列表的GET响应，直接编码已构造的JSON数据，避免重复递归复制。完整响应的字段、精确值、未知状态及证据不变，读取不执行新策略、不修改轮次，不改变分类筛选和计数范围。

`GET /tracking-events` 每行含冻结的 `entry_business_date/entry_price/entry_basis` 和 `valuation_date/observed_close/today_change_pct`。事件行情按各条 `business_date` 与其精确前一市场交易日，从同代际本地批量读取、复用有界行情缓存；缺报、行动覆盖不明或价格不可比返回 null，不使用最新行情、结束日或历史证据中的近似值回填。没有有效市场日历的临时读取不写入报价缓存，以免污染同代际后续有效读取。此补充只用于浏览，不改已存事件、策略判断或冻结基准。触发列表中“今日涨幅”是该行触发日涨幅，排序仅覆盖接口已加载事件；当前题材仍使用当前有效关系。板块提醒的观察日行情使用其已冻结的日线证据，不把当前轮次报价套回过去。

修改默认政策可显式 `apply_to_active_tracking=true`，仅迁移沿用默认的活动轮次，保留自定义与结束轮次。返回影响/跳过数量；缩短已到期不静默结束，需核对。历史事件和入池日/价不可改写。

普通周/月信号日期与入池日分开。入池始终用业务日 T 的真实日收盘，不能拿最近周/月价冒充。运行与核对有任务租约、真实进度和取消；同步 API 用例不支持后台任务式暂停/重试。

数据中心自动计划使用`GET/PUT /settings/automatic-tasks`，与盘面观察日无关，详细窗口与调度规则见[自动计划契约](automatic-review-tasks.md)。GET在同一业务SQLite读事务返回原`timezone/revision/noon/after_close/last_checks/policy`及`auction`投影；竞价含`enabled/revision/mode/fixed_time/window_end/can_enable/reason/next_run_at/last_run/worker/legacy_configuration/legacy_instrument_count`，其中时间固定09:25:30至09:30:00，旧`once`仅作兼容只读状态。午评默认12:05、盘后默认17:00，最早盘后时间17:00；已保存的自定义时间不强制覆盖。新安装三个开关均关闭。

PUT保留完整`noon/after_close={enabled,time}`与可选`expected_revision`；新增可选`auction={enabled}`，提供竞价开关时必须同时提供非负整数`expected_auction_revision`，不接受竞价时间、日期、模式或名单。两个版本在同一短事务内核验，冲突409时全部不写；未带auction的旧客户端不改变竞价配置，完全相同的规范配置不增加修订号。旧单次、日期或手工名单经显式保存规范为off/daily及空名单时，即使有效enabled仍为false也可能增加竞价修订号。仅启用状态实际改变时重新核验竞价可启用权限，临时来源不可用不阻断已有daily开关的noop。保存不创建任务、不启用初始化、不绕过整链运行锁；兼容`POST /market/auction/automation`也仅接受off/daily和当前修订号，拒绝once、日期、名单及时间。

`GET /tasks/activity` 为全局任务提示提供轻量只读摘要：当前 running/queued（最多200项，`active_count` 为实际总数）及最近5个非活动任务；不返回完整日志或任务参数。行含 job_id/request_key、kind/title、target_type、strategy_id/strategy_name、status/stage、progress、实际批次数、日期和结束时间。刚开始且未回报测量时 progress=null，真实0%保留，完成为100%，失败不伪装完成。个股与板块选股因已有运行任务冲突返回409 `{detail:{message,active_task}}`，message包含当前任务与策略名称。

已明确开启的自动竞价在原job暂时`failed`、同日自动日回执为`waiting_retry`且`automatic_control=true`时，仍计入活动任务与`active_count`，不同时出现在最近终态列表。其轻量摘要附`automatic,automatic_control,automation_state,retry_after_at_ms,attempts,max_attempts`，不展开原始响应或完整配置。前端据此持续显示原任务的全局进度门；最终结束、显式暂停/取消或自动授权停止才释放，读取活动摘要不会创建或重试任务。

`GET /tasks?limit=200&summary=true`供数据中心历史轮询，最多200条小回执，单个SQLite只读事务完成。兼容的默认`GET /tasks`及服务`list_tasks()`仍返回原完整内容；选中某任务继续`GET /tasks/{id}`读取完整日志，不删除或截断审计事实。summary行保留身份、状态、真实进度、日期、duration_ms、摘要和显示用parameters白名单（operation/mode/target_type/strategy_id/window/data_chain_version、明确链标记、来源/板块代码、单板块名称等标量；不含任务冻结大计划、成员或SHA全集）。logs只带chain_authorized/followup_queued/chain_blocked每种最新一条，按真实seq排序，details仅next_task_id/原因；足够恢复待排队/已排队/阻断状态，不能把缺失后续误显示完成。不存在的记录数保持null。完整批次子项不在历史列表展开，精细进度仍从activity与选中详情读取。

盘面补充任务摘要保留冻结的 `year_history`、`recent_days` 和 `revalidate_only` 标量。数据中心用这些字段与父任务标记区分当日盘面增量、历史回补和本地证据核验。

策略列表的 `latest_run` 用于 GET 恢复最近已保存运行；返回页面、刷新浏览器或读取旧结果不创建任务。旧日期、旧规则版本保持原值，不能冒充当前结果；手动刷新失败或尚在运行时保留上次成功结果。浏览器持久设置仅记住个股/板块目标和各自策略ID偏好。

个股策略列表在同一次只读SQLite事务内批量读取本页版本、政策、跟踪、清理阻塞及运行事实，再在事务外核验运行可用性的文件证据；一次请求的事实不跨请求缓存。板块列表同样批量读取当前版本和最近运行，仍独立于个股策略。排序、分页、回收站、政策覆盖和运行准入结果保持原口径。完整周/月的最近完成日仅可缓存纯日历推导，按完整日历内容、观察日、周期区分，最多32项；不缓存候选结果或改变交易日闭合规则。

迁移034仅增加 `selection_run(strategy_id,created_at_ms)` 读取索引，最近运行继续按 `created_at_ms DESC,rowid DESC` 定位，同毫秒顺序不变。KDJ九根报价的极值扫描可用保持相等值先后顺序的单调队列；MA、MACD、KDJ递推公式、运算次序、预热、缺口与公司行动重置全部保持原值。非普通数值或非有限值窗口走原min/max语义，不通过过滤异常报价制造连续性。

个股与板块的 `GET /...strategy-runs/{id}?decision=` 及 `POST /...strategies/{id}/runs?response_decision=` 仅接受 `matched|unknown|unmatched`，省略保留兼容的全部候选。SQL只物化所选分类；返回计数以及板块的total仍覆盖整个冻结运行，不因筛选缩小。POST的筛选只影响响应，不影响已保存候选或之后读取其它分类；相同request_key幂等回读也应用本次响应筛选，不重新执行。前端默认命中分类，切组获取该分类全部记录，保留完整排序和导航；只在模块内存保留最多24片、8MiB估算载荷、30000候选且60秒过期，切策略复用前重新GET元数据核对版本/最新运行/删除状态。候选证据不写入localStorage。

## 本地盘面证据重验

`POST /sync/market-supplement {revalidate_only:true,request_key?}` 可显式创建本地原始证据重验任务。任务为 `source_check`、`operation=market_supplement`、`source_id=local-cache`，冻结当前业务行情日；不读取来源凭据、不构造远端客户端、不补拉封板/资金/板块行情。它仅探测当年已留存日历中每个交易日的涨停、跌停两批 accepted 回执（总批数为交易日数×2），校验规范化 artifact 与 raw SHA、请求日期、来源及池类型，然后用当前身份与当日原始收盘重新归一化。身份签名变化时不能直接复制旧 items；缺证据、缺报价、错价、空池及隔离项继续保持未知或部分覆盖。

任务进度返回 `actual_completed_batches/actual_total_batches`，补充索引另存 `revalidation` 的 planned/completed/revalidated/missing/failed 批次数；revalidated 包含通过重验的部分池，不表示全池完整。按新签名保存已核验批次，全部处理结束并再次确认任务状态与行情代际后才原子发布索引。同一行情代际下，离线重验沿用250个交易日窗口内已发布的往年证据，重验批次仍为本年。

正常日增量会探测本年已验收证据。冻结250交易日保留策略的任务同时按日定位窗口内往年已留存证据，签名变化时用当日原始收盘和原响应离线重验。日增量的来源请求范围仍为目标交易日。年内计数只统计本年，近一年覆盖可使用窗口内的往年证据。

## 独立板块策略与跟踪

- `GET /sector-strategy-fields` 返回 `{items,total,target_type:"sector",unknown_policy}`；不是股票字段列表。
- `GET/POST /sector-strategies`、`GET/PATCH /sector-strategies/{id}`：name、period=D/W/M、sector_kind=all/industry/concept、definition、policy、archived；版本独立持久化。
- 板块条件仅 all/any 与白名单叶，逐叶周期；量额/MA/MACD/KDJ/BOLL收口与方向/新高/底部抬高可用，股票 ST、涨停、题材成员、换手和 history_event 不支持。固定日线 N 新高不变成 N 周/月。
- `POST /sector-strategies/{id}/runs {request_key?}` 读取本地板块证据实际求值，有可轮询真实 selection 任务；请求返回运行结果。只接受当前业务日，不从盘面历史日期入池。
- `GET /sector-strategy-runs?strategy_id=&limit=`、`GET /sector-strategy-runs/{id}?decision=`：返回冻结版本、目录签名、输入 SHA、cache_refs 及候选。候选含 target_type=sector、target_id/topic_code/topic_id、sector_kind/name、decision/close_price/metrics_by_period/checks/coverage。
- `POST /sector-tracking-rounds {run_id,target_ids}`、GET 列表/详情、POST `/review`、PATCH `/{id}/policy`、POST `/{id}/remove`、GET `/sector-tracking-events`：独立板块轮次，不写个股跟踪。
- 板块政策使用 alert_logic/exit_logic=`ANY|ALL`；其余期限、事件日开关与提醒/结束含义相同。锚点固定日线原始指数点位，不能套到成员股票。
- `GET /sector-tracking-rounds/{id}/members?as_of=&membership_scope=` 是带 round_id 的兼容入口；无需轮次可调用 `/sectors/{topic_code}/members`。latest 与 historical 含义见 [Analytics API](analytics-api.md)。

底部抬高在板块上复用上述严格日/完整周/完整月结构，含该字段的新定义使用`sector-index-dwm-higher-lows-v2`，只判断板块自身指数价，不推导成员信号。

板块指标摘要绑定固定来源及算法，命中仍核验 raw/规范化 SHA；缺本地 K 或预热为 unknown，不自动远程加载。板块没有回测或自动交易接口。

板块派生摘要使用 `sector-strategy-metrics-verified-pair-v4` 契约，隔离旧磁盘及内存scope，保留放量/缩量修正并增加仅供盘面补充复用的精确T/T−1两行私有投影。每次消费仍核验完整实际来源SHA；损坏摘要回退原始链，实际来源损坏保持失败或未知。旧摘要保留但不命中，新摘要仅从同一已核验本地来源重算并留存回执。公开指标、coverage与cache_refs结构、行情规范化及板块策略evaluator不变，不创建取数任务或重写旧运行结果。

## 研究与个股回测

`POST /research/jobs {instrument_id}` 手动创建任务，GET 列表及 `/research/jobs/{identifier}` 回读报告。研究限定标的和材料范围，主动访问后七天过期；列表轮询不续期。材料、来源日期、获取时间、单位和缺口分开，清理不影响行情或业务轮次。

新任务冻结 `research_plan`，报告v2含六区、来源证据、主营构成、公告线索及本地技术评估/冻结图；无marker的既有任务沿用原采集范围，旧报告不自动补抓。新版字段、预算与不支持能力见[研究v2契约](research-v2.md)。

报告v2阅读界面使用本地章节状态：桌面左侧小目录、窄屏横向标签，每次只显示一章，默认总览；完成任务、来源缺口与详细核查信息默认折叠。证据搜索、材料类型/发布时间状态筛选、每页8条分页，以及完全相同问题与原因的展示合并，均在已回读报告上进行，不新增服务端字段、筛选接口或研究任务，不删改留存证据、未知状态、来源时间或报告内容。条目数不是独立文档数；日期过滤只区分来源是否提供发布时间。引用保留原证据编号，可自动清筛选、选择目标页并定位，不改变应用hash路由；冻结图仅进入趋势章节后挂载，副图切换不读取当前行情。旧v1继续按原结构可读，不自动升级或重新采集。

`POST /backtests/availability {strategy_id}` 返回可用区间、预热、身份/条件限制；`POST /backtests` 冻结策略、规则、区间和配置，唯一 worker 执行；GET 列表/详情读取结果账本。七项 stats 为 total_return/annual_return/max_drawdown/win_rate/payoff_ratio/trade_count/final_equity，可无定义项保持 null。

`GET /backtests?deleted=false&limit=100&offset=0` 返回正常历史，`deleted=true`只看回收站；limit为1–100，offset非负，返回items/total及分页信息。`DELETE /backtests/{id}` 可单独移入回收站，`POST /backtests/{id}/restore` 幂等恢复。metadata含删除状态、时间、可删除/恢复及阻断原因，另带本报告的active_purge；活动任务或租约未释放409。父策略已删除时，单独恢复回测409，须先恢复策略；已删除报告的详情404。原request_key指向回收站记录时409，不能静默返回隐藏结果；用户可在恢复后重读，或明确使用新request_key新建。

曲线默认展示累计收益率，支持切换账户权益并保留下方回撤。累计收益率 `=(equity/initial_capital−1)×100`；initial_capital优先使用该报告冻结的 `stats.initial_capital`，它是引擎按十进制ROUND_HALF_UP到分后的实际起始权益。兼容旧报告仅在该字段缺失时用同一报告config的初始本金按分舍入；字段存在但非法不掩盖回退。绝不使用当前表单或第一条普通权益记录。零权益为−100%，缺值不填零；无有效本金时只展示权益并说明原因。百分比、金额与回撤提示统一单位和两位小数；切换视图不重算回测。

撮合仅受限主板/创业板当前非 ST、有效上市日期、区间连续报价且无公司行动样本。收盘信号下一市场日开盘尝试；T+1、限价、费用、滑点、拒单均留记录，期末不强平。未知不成交；不完整上市身份可使回测在创建前不可用，不伪造权益。不是历史全市场无偏或完整复权账户回测。

精确校验入口见[工作区路由](../backend/app/routers/workspace.py)、[个股条件](../backend/app/domain/conditions.py)和[板块条件](../backend/app/domain/sector_conditions.py)。

## 我的自选池

- `GET /watchlist?query=` 返回 `{items,total,matched_count,business_date}`。独立于策略轮次，持久化于业务 SQLite，最多 2000 只本地已核验 A 股；空安装返回空列表。行情读取本地已发布业务日，复用固定行情代际的批量两日报价缓存，不联网、不逐只计算全历史指标。
- 自选的两日报价摘要同时保留用于价格比较的已核验公司行动日期。缓存命中仍校验当前发布来源。行情代际或来源校验值变化后按新输入读取。已发现的来源损坏使报价保持未知并给出 `quote_reason`。缓存只保存证券的行情派生输入，自选清单、加入基准、题材归属与提醒每次读取现值，加入后涨幅按该次读取的基准计算。
- `GET /watchlist/ids` 返回 `{instrument_ids,total}`，供所有星标入口共用一次轻量读取；新增和移除在当前页面各入口之间同步。
- `POST /watchlist {instrument_id,tracking_round_id?,source_kind?,source_id?,source_name?,observed_date?}` 返回 `{item,added,already_exists}`。source_kind 为 stock/tracking/sector/topic。跟踪来源必须提供个股轮次，服务端核验其股票并复制原 `entry_business_date`、`entry_price_e8` 和价格口径，忽略客户端价格；不重置、创建或结束策略跟踪。重复添加幂等，保留首次来源、加入时间和事件基准。
- `DELETE /watchlist/{instrument_id}` 幂等移除自选，返回 `{instrument_id,removed}`，不修改原跟踪或题材成员。

自选列表在今日涨幅旁展示 `watchlist_change_pct`（加入后涨幅）。其独立基准为首次加入自选业务日的唯一有效原始成交收盘，持久化 `watchlist_entry_price_e8/watchlist_entry_basis_json`；公开字段为 `watchlist_entry_date/price/price_basis/state/reason/basis`。原 `tracking_entry_date/event_close/event_date` 继续表示原策略轮次，不能用于加入后涨幅。重复收藏保留首次日期/价格/来源，移除后重新收藏是新的加入记录。

`watchlist_change_state=ready|unknown` 与 `watchlist_change_reason` 解释比较；同日可计算真实0，跨日仅在已核验公司行动覆盖且期间无行动时按当前收盘/自选基准−1计算。有行动且无可核验冻结基准转换、缺少唯一有效成交报价或覆盖未知均返回null，不补0。读取仅访问已发布本地数据，不联网、不创建任务。

013迁移只加字段及成功基准不可改写约束，不读取行情或迁移用户价格。旧行在首次列表读取时按原 `added_business_date` 分批核验、短事务CAS幂等补齐，最多128只一批；成功后不重写。某代际缺数记录未知，同一代际同一核验方法不重复回查，下一已发布行情代际可重新核验缺失基准。方法 `exact-published-added-session-close-v2` 可将旧方法的 null 基准在同代际安全重验一次，修复早期首次并发读取的临时缺日历回执；成功基准无论旧方法或新代际均保持原价与原回执。不能用今日价或原策略价冒充旧加入价。MarketData 首次加载在可重入锁内先完整读取 reference，再一起发布 head、snapshot 与 metadata，不能向其他请求暴露已切代但日历尚未加载的中间态。实时行情浏览与自选基准分别缓存/冻结，当前有效归属仍每次批读。

新初始化/盘后增量在同步 plan 冻结 `master_request_params={asset_type:"a-share",exchange:"SH,SZ,BJ",limit:10000,offset:0}`，显式交易所范围用于来源返回证券上市日期；实际仍未知的日期保留未知。旧已有 plan 无此字段时使用原无exchange参数，保持 accepted/master.json 的请求签名与验收证据，不能升级时静默重写旧任务。原始响应继续留证，已验证同身份上市日期的恢复规则不变。
- 行字段含 `source_kind/source_id/source_name`、`added_business_date/created_at_ms`、`observed_date`、`tracking_round_id/tracking_entry_date`、`event_date/event_close/event_price_basis`、`latest_price/today_change_pct/quote_date`。人工收藏且无原个股轮次时，事件日和事件价保持 null 并提供 `event_baseline_reason`；板块成员不套用板块指数的事件基准。
- 每行 `current_topics` 为当前有效行业、概念与人工研究归属，`current_industries` 为行业子集；元素含 `topic_id/name/kind/source_observed_date/members_complete`。`current_topics_observed_date` 是业务日有效关系日期，不冒充来源采集日；逐来源日期见标签字段。归属使用题材库当前来源成员及人工修改批量读取，与普通个股详情共用同一只读归属服务，不缓存原收藏时归属，不查询逐股接口或扫描历史行情。缺少已知归属不推断不属于；`query` 同时匹配股票、代码、首次来源和全部行业/题材名称。界面合并为“行业 / 题材”一列，默认最多1枚行业和2枚概念标签，其余可展开且仍可搜索。
- 自选股票详情可按筛选排序后的完整列表上一只/下一只；每只股票独立传递其冻结事件基准，切换到无事件来源股票时不会继承上一只的基准线。该导航的观察日期使用业务行情日，不受盘面历史日期影响。

## 手动批量执行策略

`POST /strategy-batches {target_type:"stock"|"sector",scope:"selected"|"all",strategy_ids?:string[],request_key?:string}` 手动创建持久化“筛选并自动跟踪”批次，所选需1—200个不同ID，全部只包含当前类型的未归档策略，不跨个股/板块标签。按侧栏顺序逐策略完成筛选→命中自动入池，再处理下一策略；仍需用户点击开始。新批次固定冻结 `auto_add:true/plan_version:"selection-and-tracking-v1"`，与策略版本、业务日、输入快照、本地题材修改签名和执行顺序共同留存，自动入池意图参与请求幂等签名。活动任务冲突409含可读 `message/active_task`，未结束批次冲突409含 `batch_id`。

无此plan marker的旧排队/已完成批次继续只筛选，重放原request_key只返回原批次，不能升级时追加入池或追溯执行。新建新key才表示新操作。单策略原运行接口保持筛选后手动选择入池的流程。

`GET /strategy-batches?target_type=stock|sector&limit=10` 和 `GET /strategy-batches/{batch_id}` 只读回执：`batch_id/job_id/request_key/target_type/status/business_date/items/total_count/completed_count/succeeded_count/failed_count/cancelled_count/current_strategy/progress/auto_add/plan_version` 及创建/结束时间。每项含 `strategy_id/name/version/sequence/job_id/tracking_job_id?/status/stage/progress/run_id/error/selection_status/tracking_status/matched_count/added_count/skipped_count/ignored_count`，sequence从1开始。汇总也提供四种计数；未回报为null，真实0单独保留。ignored是skipped的子集，命中数不冒充已入池数。item.status是筛选与自动入池的组合状态，completed_count只有整体步骤终结才增加；`completed_steps/total_steps` 分别统计已终结步骤与总步骤，自动批次每策略有筛选/入池两步，百分比按终结步骤/总步骤计算；策略completed_count仍只有整体终结才增加，当前步骤阶段/百分比独立展示。

底层复用 `source_check(operation=strategy_batch)` 父任务、`selection(operation=strategy_batch_item)` 筛选子任务和 `source_check(operation=strategy_batch_tracking)` 入池步骤，无新schema。所有步骤同事务预建，唯一worker按冻结顺序运行；父项在所有步骤终结后才可汇总。一项失败继续下一项，筛选成功而入池失败仍保留独立run_id和错误。规则/日期/输入变化则未完成步骤失败，不能混用新输入。

自动入池复用原个股/板块入池校验与冻结事件日价格；已跟踪、手工忽略、同次运行已生成过的结束轮次均跳过，既有轮次日期/价格/状态不被重置。板块自动批次也保留手动移除的忽略意图，不创建个股轮次。个股每块最多500、板块最多800；每块跟踪写入与 `auto_tracking_chunk` 计数回执在同一SQLite事务提交。崩溃在筛选保存后由已排队入池步骤继续，崩溃在入池提交后按已提交回执跳过，不重新筛选、不重复计数、不复活已结束轮次。每次领取有独立lease attempt，旧进程无权提交或结束恢复后的步骤。

`POST /tasks/{batch_id}/pause|resume|cancel` 级联尚未完成的筛选与入池步骤；暂停/取消不自动继续，已保存结果和已入池轮次保留。继续需用户明确操作且冻结输入未变化。批次不支持通用retry，需明确新建批次。浏览器仅POST一次创建/明确控制，其余GET轮询；空闲/终态停止高频轮询，隐藏页面不持续拉取。

## 显式个股划线提醒与自动自选

水平线返回 `line_type:reference|support|resistance`、`alert_modes:string[]`、`auto_watchlist:boolean`、`revision`、`effective_business_date`，另含 `first_alert_date`（尚未提醒为null）、`post_alert_tracking_sessions=60`。012旧线在015增量迁移后固定为 reference/[]/false，不根据名称猜规则，不扫描历史自动启用。新建或修改可提交上述显式字段；价格、类型、模式或自动收藏改变才递增revision并从当前业务日重新生效，纯名称/颜色不产生新触发版本。`alert_modes:[]`暂停规则，移出自选不暂停规则。

支撑模式为 `support_reclaim`（严格前市场日收盘>=线、当日最低<=线、收盘>=线）与可选风险 `support_breakdown`（前收>=线、收盘<线）；压力模式为 `pressure_close_breakout`（前收<=线、收盘>线）和 `pressure_touch`（low<=线<=high，含等号，不需前收）。穿越模式不回退最近有报价日，缺少严格前市场日报价保持unknown。失守为 `state:invalidated`，单独提示风险，不视为回踩收回，不自动新增自选；其余命中为triggered。事件唯一键为annotation/revision/rule/trade_date，持续站上不重复产生收盘突破。

启用规则保存与排队在同一SQLite事务。`POST /watchlist/line-alerts/evaluate {request_key?}` 显式创建标准source_check job（operation=line_alert_check）；浏览和GET不创建任务、不扫描行情。新手动初始化/增量任务冻结 `line_alert_check_version:explicit-local-line-alert-v1`，主行情成功后接续本地检查；恢复只补该新marker已授权的遗漏，不追溯旧已完成同步任务。暂停/取消与每次领取lease隔离，旧attempt无权续进度或提交。每次任务冻结行情head、规则签名/单调clock及输入快照，改线、删线或切代后旧任务停止提交。

核验从规则生效日或最近已核验交易日起增量补跑至已发布业务日；新规则不看创建前日期，未知旧日期保留待核验并可在新代际重验。一次至多128条线涉及的股票，历史每次批读最多20个交易日及其前一交易日，逐日仍严格核对当日和前一市场交易日；另读取当前日及公司行动，不联网，不展开全市场Python历史。每条rule/day的结果持久化，可在中断后跳过已提交结果。离线多日补跑时event.trade_date保持真实历史触发日，若自动加入自选，added_business_date与独立收盘基准采用实际执行的当前业务日，不冒称历史已收藏。

固定前复权锚水平线使用同一event_affine算法，按已核验公司行动将原锚线价与前收映射到事件日raw价基准；事件同时保存 `line_price` 原锚价和实际判定 `comparison_line_price`，两者不能混称。缺少行动证据、无效/重复行动或锚后转换失败保持unknown。none原始线在除权日不直接混比前收，touch可单独使用当日raw范围。

`GET /watchlist/line-alerts?as_of=&limit=100&scope=all` 返回所有股票（包括尚未自选）的已存事件、total、business_date、as_of、scope、today_total、history_total、当前rules_total、latest_check和unknowns。`scope` 为 `all`（兼容默认）、`today`（trade_date等于最新已发布business_date）、`history`（早于business_date且不晚于as_of）；今日不接受更早as_of，历史补跑按事件trade_date归档，不按created_at误归今日。total按当前scope完整计数，不受limit影响（1至1000）；today_total按业务日，history_total按截至日期统计更早事件。不从当前行情反推旧事件。latest_check含job_id、checked_at_ms、market_snapshot_id、checked_rules（本次rule/day结果数）、trigger_count、unknown_count，以及 `is_current/stale/stale_reason`，行情日、head或规则改变后不能将旧完成回执冒充当前完成。

`GET /watchlist/line-alerts/summary` 是全局小提醒入口的有界只读摘要：business_date、当日total、limit=5与items，只含event_id、证券名称代码、rule/state/title、trade_date、created_at_ms。按line_alert_by_day索引读取，不取行情、解密凭据、创建任务或更新提醒；缺业务日返回null/0/空数组。读取或浏览器内去重不修改服务端历史记录，也不改变首次提醒后60交易日的清理规则。

事件保存event_id、instrument_id/name/code、annotation_id/revision、line_type/rule、trade_date、原价与判定价、price_basis/anchor_date、raw OHLC、严格前收/转换前收、转换依据、state/title/reason、创建时间和冻结market_snapshot_id/input_snapshots；引用快照以manual retention pin保存，手动删除水平线不即时删除历史证据；首次实际提醒后的到期清理规则见下。`/watchlist`每行latest_line_alert通过按股票分组的批量SQLite摘要返回，不依赖提醒列表limit或逐股GET。

仅triggered且auto_watchlist启用时可自动收藏：事件与新自选同一短事务写入，已有自选保持原日期、来源与基准，新增source_kind=stock/source_name=划线提醒/source_id=annotation_id，无跟踪轮次时不伪造策略事件价。`added_to_watchlist/watchlist_add_state`分别记录added、already_present、disabled、not_eligible（失守）、capacity_reached、missing_current_quote；容量或当前收盘缺失仍保存可见事件和解释，不让整次核验失败。原事件重复核验不再次收藏，因此这些未入池事件可由用户手动收藏。unknowns只说明缺口，不伪造触发或价格。

新线未实际提醒时持续监控。首次triggered或invalidated事件日为第0日，之后第1至60个市场交易日继续监控，超过60日停止新提醒。重复提醒或修改线不重置最早日期。新增盘后increment冻结 `line_alert_retention_version=first-alert-plus-60-sessions-v1`，仅其成功/部分成功/无变更后的授权source_check子任务可在短事务中删除到期线、全部版本的关联事件与核验记录，并解除这些事件自有pin；不删除自选或跟踪。旧冻结任务、初始化、GET及普通手动核验不清理。日历不能证明首次提醒日期时保留未知；已有孤立提醒同样到期清理。只保留任务清理计数和检查点，`latest_check.cleanup`可返回累计数与complete。026守卫默认拒绝事件删除，仅worker当前连接的本批精确ID可通过；UPDATE仍拒绝。每批至多100条线，校验head、规则clock与lease并原子记录续跑检查点；人工修改后不续旧任务。详见[保留与恢复边界](../docs/划线提醒到期清理.md)。

## 上市身份与人工确认

`GET /data/listing-status` 返回 `business_date/market_snapshot_id/revision/summary_is_current/identities/confirmations`。identities含 `stock_count/catalog_count/listed_count/known_count/pending_listing_count/unknown_count/pending_listing/unknown/indices_excluded`，名录数量包括待上市，不将指数放入股票分母。已上市日期覆盖分母为listed+unknown，pending单列，不要求尚未上市证券已有历史K线。

有效来源list_date不晚于观察日为listed；来源日期在未来且本代已核验无报价记录为source pending，若已有报价则保留身份冲突unknown。来源日期缺失时，仅显式人工pending确认且本代无报价记录可标pending；没有确认的缺报仍unknown。来源仍缺日期但已有报价为quoted_unknown_date，计入unknown，不以首根报价伪造上市日。重复、无效或仅存在于原始文件的报价也阻止宣称无报价，原始文件不可核验时同样不能确认pending；quote_presence分开记录valid_quote_count、analysis_observed_rows、raw_observed_rows、observed_quote_rows、ambiguous_quote_keys与absence_verified。新来源日期已到达时自动采用来源listed，历史人工确认仍保留。每行有 `instrument_id/name/listing_status/source_list_date/evidence_kind/source/confirmed_date/confirmed_at_ms/effective_business_date/reason/reference/can_edit`；confirmed_date兼容表示确认观察业务日，confirmed_at_ms为实际审计创建时间。reference保存本代报价计数/首末日以及所依据的人工审计，不把用户说明标成官方证据。

`POST /data/listing-status/confirm {instrument_ids,status,reason,evidence?,observed_business_date?,request_key?}` 是明确人工操作。每次1至128只完整A股代码；status只接受pending或unknown，后者撤销人工pending判断；reason为1至1000字符，evidence最多4000字符。观察日期仅当前规范业务日，省略时由服务器冻结。禁止客户端填写上市日期；来源已提供有效上市日或本地出现唯一有效OHLCV/成交额报价（包括合法零量K线）的证券不能标pending。即使尚无唯一有效报价，只要原始包或分析序列留有异常、重复、非市场日等记录，也保持待核验；没有通过原始文件核验时不确认无报价。报价聚合在写锁外，提交核对head、业务日及人工revision，变化返回409，原始行情不改。请求key同参数重放返回原confirmation_ids，参数改变409；重复提交不添加新审计。响应为当前read view加confirmation_ids/already_exists。

016迁移仅新增append-only `listing_status_confirmation`、单调revision及派生摘要，不硬编码股票、不填来源日期、不改行情文件或快照quality。`source_kind`固定manual_user_confirmation，记录用户说明、确认业务日、真实创建时间、原始行情head和无报价证据；unknown更正也是新记录。`GET /data/listing-status/confirmations?instrument_id=&limit=100` 提供全部更正历史（上限1000），不删除旧确认。当前GET只读小名录与SQLite摘要，不扫描历史行情、不联网或排队。

派生摘要精确匹配primary daily_raw head、market generation、业务日、来源名录签名与人工revision。无匹配摘要时不复用旧代人工pending回执，分类保守待核验；人工确认变化使已有本地覆盖报告过期，需重新核验。显式本地覆盖检查可复用已有SQL报价聚合写该小摘要。新手动初始化/增量冻结 `listing_status_check_version:listing-evidence-v1`，成功后接续source_check(operation=listing_status_check)，仅对来源日期缺失/未来的证券分批SQL聚合本地报价，自动捕捉上市或首次报价后的转换；旧任务无marker不扩范围、不回溯自动执行。暂停/取消不自动恢复，领取nonce与head/day/revision共同阻止旧attempt提交。待上市不从远端行情采集股票范围排除，后续真实行情照常接纳。

## 热点浏览与数据保留（0.1.6）

跟踪与自选列表响应增加`hot_filter_scope`，用于将近期热点浏览绑定到同一业务日、主行情快照和题材修订。`GET /hot-universe`读取已留存来源，复用选股热点排名与成员代理口径；窗口、前N、类型及scope受校验，日期/代际/修订变化返回409。不创建任务、不联网、不持久化新策略。浏览“已观察归属”与严格完整命中分开，数据不完整时逻辑结果仍unknown。

新手动同步和普通盘面补充任务冻结`retention_policy_version=supplement-retention-250-v1`并逐链透传，旧冻结记录不补字段。025仅新增`supplement_retention_run`、`supplement_retention_file`，保留策略、行情与旧审计行。250为市场交易日活动窗口；引用保护、当年统计和五年K线来源例外必须披露，不把逻辑范围当作已释放物理空间。清理仅在补充成功发布后由唯一worker按SHA清单执行，API/备份与清理通过共享业务卷文件锁协调。逐文件删除事务外执行，短事务登记不可变计划、状态与释放字节；未知依赖、任务或引用变化时停止并留回执。


## 生肖跟踪

独立年度人工观察池使用 [生肖跟踪 API](zodiac-api.md)。观察年度加一为目标生肖年度；不复用或创建策略轮次、自选条目。新增迁移033包含独立成员、审计与幂等回执；默认展示共享年内 / 30日 / 涨停统计及当前行业概念。

个股和板块选股支持 `trendline_pullback`（回踩上升支撑）与 `trendline_breakout`（收盘突破压力）。条件为 `{"field":"trendline_pullback","op":"matches","value":0.5,"period":"D"}`，value 为 0–10 的百分数，默认编辑值 0.5，period 可选 D/W/M 或继承策略默认周期。首版仅用于外层选股/选板条件，支持现有 all/any、个股历史事件的并列组合和当前热点范围。回测、跟踪、历史事件内部及买卖规则暂不接受这两个字段。当前热点范围保持当前成员代理口径。

`strategy-confirmed-trendlines-v1` 以观察日最近完整周期为信号周期，从前一周期及之前最多 120 根连续可比 K 线取主线，至少需要 12 根历史和 1 根信号 K 线。左右各 2 根确认分型。沿用图表主线的两锚点排序与未穿越校验，将前一周期线价延伸一根到信号周期。回踩要求正斜率、前收在线上或上方、信号周期最低价落入上下容差内且收盘在线上或上方。突破要求前收在线上或下方、信号周期收盘严格超过线价加缓冲。完整输入无合格线时为 false，缺少足够连续可比输入时为 unknown。个股复用策略价格口径，板块使用缓存的来源指数原价，和成员覆盖无关。checks 保留线型、锚点与确认日期、投影价、OHLC 判定用价、完整周期日期及缺口。

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

联合查询使用重复 `topic_ids`（1–20 个），`match=any|all`、query、sort_by、sort_order。返回 `selected_topics/members_complete/business_date/quote_date/snapshot_id/warnings`；行含身份、topics/industries/matched_topics、精确当日报价及统一个股统计。无当日报价保留行且行情为 null，不以旧值替代。

排序支持 change_pct/amount/turnover_rate/close/name/code/return_ytd_pct/change_30d_pct/limit_up_ytd/last_limit_up_date/sessions_since_limit_up；完整候选排序后前端分页，null 在两个方向均最后，下界不作精确次数排序。

批量关联 body 为 `{instrument_ids,topic_ids,reason?}`，最多 100 股、20 分类。全部验证后同事务追加 include，保留其他归属；已有有效关系 unchanged，被人工排除的可重新纳入。返回 added/unchanged 及数量。它按当前有效状态幂等，不承诺跨后续人工修改的请求重放。

## 当日主线归因（手工证据 V0.1）

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

前端涨停页保留“连板梯队”“概念关联”，增加“当日主线”。仅把当前返回的确认涨停池按证据分组，每股一张卡；池外已存归因单列且不计涨停统计。反包、晋级失败、20cm三类继续直接展示，不依赖主线完整。日期切换隔离旧响应与编辑器；前端当前展示最新修订，时间截断回看通过上述API读取。操作见[当日主线复盘](../docs/界面导览.md#8-当日主线复盘)。

## 个股策略与条件

`GET /strategy-fields` 返回字段 type/operators、周期、fixed_period、整数边界及场景限制；前端依白名单构建，服务端最终验证，不执行用户 Python/SQL/JS。

前端条件组的“快捷添加条件”默认收起且不预选模板。选择、修改参数、取消都不调用规则onChange；仅点“加入条件”才把预览的标准条件加入当前草稿，之后仍须保存策略。历史where/then各自有入口，单叶旧结构打开面板也不重写。all/any中的组合始终作为一个原子子组加入，不拆散到OR。

- `GET/POST /strategies`、`GET/PATCH /strategies/{id}`：name、period=D/W/M、definition、policy、trade_rules；修改追加版本，旧运行冻结。
- definition 为叶 `{field,op,value,period?}` / `{field,op,right_field,period?}`，或嵌套 `{all:[...]}` / `{any:[...]}`；兼容旧 logic/conditions 输入。
- 普通叶默认策略周期，也可逐叶 D/W/M；W/M 使用最近应闭合且有效周期，不能缺最新后退到旧柱。固定日线字段必须 D。右字段只能是兼容白名单数值，常量专用字段不能当右字段。
- 技术组合快捷入口不新增服务端字段：MACD水上金叉保存为同周期 `all(macd_cross==true,macd_dif>0,macd_dea>0)`；KDJ低位金叉保存为 `all(kd_cross==true,kdj_k<U,kdj_d<U)`，U默认20、快捷输入范围0–100，指当前双线而非前期值；K>D状态保存为 `{field:"kdj_k",op:">",right_field:"kdj_d",period:P}`。每个叶子显式使用同一P；普通入口采用当前选择周期，历史where/then固定D。组合是外层all/any的一个子节点，不能把三项拆进OR。用户后续可编辑各叶子，仍由原有白名单、深度/节点数与三值逻辑核验。
- 卖出区技术组合优先展示MACD死叉、KDJ死叉、MACD水上死叉、KDJ高位死叉与K<D持续状态。普通死叉为前一期快线≥慢线且当前快线<慢线；水上再要求当前DIF、DEA都>0；高位再要求当前K、D都>H，H默认80、快捷输入0–100，等于门槛不算。K<D只检查当前状态，不要求刚死叉。所有快捷均展开成同周期普通叶子，不把“金叉=false”当死叉，不改变已保存条件或卖出启用时的既有默认条件。
- K值范围快捷保存为同周期 `all(kdj_k>=L,kdj_k<=U)`，金叉加范围再包含 `kd_cross==true`；只限制当前K，不隐含D范围。L/U为有限0–100且L≤U，等号有效；低/中/高快捷起点为0–20、20–80、80–100，20/80边界重合，它们是可编辑起点而非互斥状态或收益判断。过滤范围不修改KDJ指标本身的计算参数。
- 股票范围 board 包含/剔除与当前 is_st 在外层独立 AND。当前身份不是历史逐日 ST/上市档案；未知不能当 false。
- `close_breakout_high` 使用 `op=new_high,value=N`（整数 1–750），观察日日收盘严格大于前 N 个市场交易日 high，排除今日；缺报/零量/行动不可比为未知。
- `limit_up/prev_limit_up` 分别是当日收盘/前一市场交易日收盘来源事实，是/否均需证据。`limit_up_count_1y` 是截至昨日滚动自然一年次数，非负整数常量，不是年内次数；不完整 actual=null，有限上下界可证明的比较才成立。

历史条件节点为 `{history_event:{window:{from:1,to:30},match:"latest",where:条件树,then:条件树}}`。偏移均为前 N 个市场交易日、不含今天且含两端：近 N 日是 from=1,to=N，自定义前5—20日是 from=5,to=20，指定前 N 日是 from=to=N；范围 1–750。编辑器新建默认前1—30日，读取旧规则不改窗口或版本。latest/earliest 指按距离从近/远优先，不跳过优先位置的未知去伪造匹配；绑定后 then 不满足也不改选其他事件。where 支持日 OHLCV/指标与同日 `limit_up` 的自定义 all/any，不支持递归 history_event、prev_limit_up、历史题材/身份、滚动涨停次数或跟踪基准。

`match="any"` 表示范围内任意一次完整满足。按近到远检查，每个候选必须用同一事件的 where、then 和已启用 price_path；第一个整体为true的事件才成为 `selected_event`，停止继续搜索，不代表已经枚举所有满足事件。较近日期未知不妨碍较早的完整true证明存在；没有true但存在未知候选时结果为null，完整已知但均不满足时为false。false/null时不绑定回退事件、不生成命中横线。不能把一次事件守位成功与另一次事件收复成功拼成命中。它与where内部“倍量或涨停任一条件”是两层独立选择；旧规则不会自动切换为any。

then 是独立今日条件树，例如 `{all:[{field:"change_pct",op:">",value:3,period:"D"},{any:[{field:"macd_cross",op:"==",value:true,period:"D"},{field:"kd_cross",op:"==",value:true,period:"D"}]}]}`，不要求比较事件价；也可用 `{field:"close",op:">",right_event_field:"close"|"high"|"low",period:"D"}` 比较观察日收盘与本节点事件价。事件绑定互不串用。跨行动、缺中间有效日报价或来源覆盖不足时，事件价比较保持未知，独立今日涨幅/技术条件仍按自身覆盖判断。新编辑器历史内部固定 D；无显式 period 的旧 then 保留旧版本解释。共享历史预备同时保留 where 和显式日线 then 的依赖，保证批量与标量路径一致。

事件价格比较叶可追加 `right_event_change_pct=p`，只允许在本事件then的 `field=close` / `right_event_field=close|high|low` 形式使用，不与value/right_field混用。p为有限数值 `-100<p≤10000`；右侧阈值为 `事件价×(1+p/100)`，用未舍入十进制比较。例如p=3、op="<"指今收低于事件价上方3%；p=-3、op=">="指今收不低于事件价下方3%；它不等于只比较事件价的3%。0显式有效，缺少该键的旧叶结构、求值与证据保持。新证据增加原始 `event_price`、`threshold_decimal`、实际相对原价的 `event_change_pct` 与比较模型，`right_actual`为偏移后的阈值。无法安全表示的数值不输出非有限JSON，也不绕过原价可比守卫。上下界可用all组合，等号是否包含由各比较符决定。

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

价格过程为个股历史节点能力，板块条件不接受history_event。当前新回测引擎为 `restricted-next-open-v5-event-percent-cross-cycle`，input/logical key包含版本；不同冻结引擎的未完成任务不得被新请求复用或继续模拟发布，需手动新建，旧完成报告仍可读取。带百分比事件比较或cross_cycle的新定义使用 `workspace-v11-event-percent-cross-cycle`；只有原守位/收复的定义保留 `workspace-v10-history-price-path`，更早定义继续沿用其既有v8/v9，不自动重写旧版本。

包含match=any的定义（包括买入或卖出规则）使用 `workspace-v12-history-any`，其历史事件证据模型为 `daily-history-event-v2-any`。只增加新AST语义，不改变旧latest/earliest定义的版本、求值或已保存运行；新AST与输入SHA已隔离。搜索摘要只记录有界计数与首个未知位置，不保存完整历史候选数组。

独立趋势条件 `{"field":"higher_lows","op":"confirmed_rising_lows","value":60,"period":"D"}` 的value为8—750整数，默认60；period省略继承策略D/W/M。回看窗口包含最近N个所选完整周期的低点中心，左边另外需要2根预热；左右各2根且中心low严格低于四邻才确认，等低平台不算。只取最近两次确认低点，不跳过后续较低低点；要求L2>L1，且从L2右侧确认当期（含）至观察期的全部收盘≥L2。破位后收回仍不满足该对结构。完整窗口不足两底为false；缺失/重复/无效或零量报价、未知公司行动覆盖或不可比原价区间为unknown，不补停牌周期。W/M按市场日历完整聚合，观察日后右邻不参与确认。

该条件是独立状态，不自动限制两底在历史事件之后；可放外层all/any及历史节点then（日线），where拒绝。checks含`lookback_periods/structure_version=higher-lows-v1/covered_periods/required_periods/observed_date/confirmation_date`，其中`required_periods=N+2`；evidence含最近两底的date/confirmed_on/price/age_periods、`minimum_close_since_l2_confirmation`、`window_includes=pivot_centers/left_warmup_periods=2/hold_from=l2_confirmation_inclusive/price_basis=none`。另含有界`last_blocker={period_end,kind,reason,market_dates,affected_market_days}`或null，market_dates最多3个，仅说明最近一次缺报、重复、无效成交、行动覆盖未知、未复权行动、日历冲突或首周期边界阻断；unknown原因直接说明阻断日期/类型与连续可比根数、所需根数，例如“2026-07-22存在未复权公司行动，目前连续61根，60根窗口加左侧2根预热需62根”。只包含有界依据，不返回整段行情。仅使用新字段的个股定义使用`workspace-v13-higher-lows-v1`；旧定义版本保持。受限回测支持该条件，报告附`condition_contracts.higher_lows=higher-lows-v1`，输入AST隔离，旧引擎计算行为和旧报告不变；无数据库迁移，也不自动执行策略。

历史 `limit_up` 在选股、跟踪和回测复用同一已发布代际的来源池核验器：逐日期编译来源代码索引与完整来源缺席证据，每只股票再核对该日唯一有效原始 OHLC、收盘、成交量和来源身份；既有日报价标志或 +10% 涨幅不能代替来源证据。缺来源、缺报、停牌、价格/方向冲突均保留 unknown，不用未来池回填。checks 保存来源路径、快照、窗口、日期、偏移、原始价格与原因，不序列化完整历史上下文。回测只为历史 where 放开同日涨停，普通当前涨停及滚动次数条件仍沿用原有回测边界。

`trade_rules={entry:条件树|null,exit:条件树|null}` 与选股独立版本化：买入为选股 AND entry，exit 不要求仍满足选股。只接受已支持回测特征；不支持题材、未完整历史涨停或跟踪锚点，不因为 AST 能保存便保证可回测。

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
| `POST /strategies/{id}/runs {request_key?}` | 实际求值，返回冻结 run 与候选/计数 |
| `GET /strategy-runs?strategy_id=&limit=`、`GET /strategy-runs/{run_id}?decision=` | 运行列表及 matched/unmatched/unknown 证据 |
| `GET/PUT /strategies/{id}/automation` | refresh_after_sync、auto_track；默认关闭，auto_track 需 refresh |
| `POST /tracking-rounds {run_id,instrument_ids}` | 仅当前版本/当前业务日 matched 入池；重复与忽略项跳过 |
| `GET /tracking-rounds?strategy_id=&status=&triggered_only=` | 当前轮次、进度、行情、题材及提醒 |
| `GET /tracking-rounds/{round_id}`、`GET /tracking-events` | 详情和追加事件 |
| `POST /tracking-rounds/review {strategy_id?,request_key?}` | 按市场日补核对，按政策版本与日期幂等 |
| `PATCH /tracking-rounds/{round_id}/policy` | 显式迁移政策，追加审计，不改入池锚点 |
| `POST /tracking-rounds/{round_id}/remove` | 软结束；可设置 ignore_reentry |
| `DELETE /strategies/{id}/ignores/{instrument_id}` | 解除忽略重入 |

个股政策使用 alert_logic/exit_logic=`AND|OR`、alerts/exits、duration_sessions（默认 30）、事件日开关（默认 false）。D0 为入池日；未知交易日进度不补零。提醒不结束，死叉不等于“金叉=false”。本轮高/低锚点是原入池快照唯一日 K 的 high/low，不是跟踪累计值或周柱；严格高于/跌破，相等不触发，行动不可比时未知。

个股/板块跟踪列表额外返回 `counts={active,history}`，是当前策略范围内、应用 status/triggered_only 前的真实轮次数；触发提醒数使用事件接口 total。前端分类以数量分段展示，题材只预览两项，完整归属和逐项触发依据按需展开。

`GET /tracking-events` 每行含冻结的 `entry_business_date/entry_price/entry_basis` 和 `valuation_date/observed_close/today_change_pct`。事件行情按各条 `business_date` 与其精确前一市场交易日，从同代际本地批量读取、复用有界行情缓存；缺报、行动覆盖不明或价格不可比返回 null，不使用最新行情、结束日或历史证据中的近似值回填。没有有效市场日历的临时读取不写入报价缓存，以免污染同代际后续有效读取。此补充只用于浏览，不改已存事件、策略判断或冻结基准。触发列表中“今日涨幅”是该行触发日涨幅，排序仅覆盖接口已加载事件；当前题材仍使用当前有效关系。板块提醒的观察日行情使用其已冻结的日线证据，不把当前轮次报价套回过去。

修改默认政策可显式 `apply_to_active_tracking=true`，仅迁移沿用默认的活动轮次，保留自定义与结束轮次。返回影响/跳过数量；缩短已到期不静默结束，需核对。历史事件和入池日/价不可改写。

普通周/月信号日期与入池日分开。入池始终用业务日 T 的真实日收盘，不能拿最近周/月价冒充。运行与核对有任务租约、真实进度和取消；同步 API 用例不支持后台任务式暂停/重试。

`GET /tasks/activity` 为全局任务提示提供轻量只读摘要：当前 running/queued（最多200项，`active_count` 为实际总数）及最近5个非活动任务；不返回完整日志或任务参数。行含 job_id/request_key、kind/title、target_type、strategy_id/strategy_name、status/stage、progress、实际批次数、日期和结束时间。刚开始且未回报测量时 progress=null，真实0%保留，完成为100%，失败不伪装完成。个股与板块选股因已有运行任务冲突返回409 `{detail:{message,active_task}}`，message包含当前任务与策略名称。

策略列表的 `latest_run` 用于 GET 恢复最近已保存运行；返回页面、刷新浏览器或读取旧结果不创建任务。旧日期、旧规则版本保持原值，不能冒充当前结果；手动刷新失败或尚在运行时保留上次成功结果。浏览器仅记住个股/板块目标和各自策略ID偏好，候选与证据始终读取服务端。

## 本地盘面证据重验

`POST /sync/market-supplement {revalidate_only:true,request_key?}` 可显式创建本地原始证据重验任务。任务为 `source_check`、`operation=market_supplement`、`source_id=local-cache`，冻结当前业务行情日；不读取来源凭据、不构造远端客户端、不补拉封板/资金/板块行情。它仅探测当年已留存日历中每个交易日的涨停、跌停两批 accepted 回执（总批数为交易日数×2），校验规范化 artifact 与 raw SHA、请求日期、来源及池类型，然后用当前身份与当日原始收盘重新归一化。身份签名变化时不能直接复制旧 items；缺证据、缺报价、错价、空池及隔离项继续保持未知或部分覆盖。

任务进度返回 `actual_completed_batches/actual_total_batches`，补充索引另存 `revalidation` 的 planned/completed/revalidated/missing/failed 批次数；revalidated 包含通过重验的部分池，不表示全池完整。按新签名保存已核验批次，全部处理结束并再次确认任务状态与行情代际后才原子发布索引。正常日增量也会先按同一本年日历探测旧 accepted 证据并在签名变化时离线重验，以保留仍可核验的历史；日增量的远端范围不因此扩展到旧日期。

## 独立板块策略与跟踪

- `GET /sector-strategy-fields` 返回 `{items,total,target_type:"sector",unknown_policy}`；不是股票字段列表。
- `GET/POST /sector-strategies`、`GET/PATCH /sector-strategies/{id}`：name、period=D/W/M、sector_kind=all/industry/concept、definition、policy、archived；版本独立持久化。
- 板块条件仅 all/any 与白名单叶，逐叶周期；量额/MA/MACD/KDJ/新高/底部抬高可用，股票 ST、涨停、题材成员、换手和 history_event 不支持。固定日线 N 新高不变成 N 周/月。
- `POST /sector-strategies/{id}/runs {request_key?}` 读取本地板块证据实际求值，有可轮询真实 selection 任务；请求返回运行结果。只接受当前业务日，不从盘面历史日期入池。
- `GET /sector-strategy-runs?strategy_id=&limit=`、`GET /sector-strategy-runs/{id}?decision=`：返回冻结版本、目录签名、输入 SHA、cache_refs 及候选。候选含 target_type=sector、target_id/topic_code/topic_id、sector_kind/name、decision/close_price/metrics_by_period/checks/coverage。
- `POST /sector-tracking-rounds {run_id,target_ids}`、GET 列表/详情、POST `/review`、PATCH `/{id}/policy`、POST `/{id}/remove`、GET `/sector-tracking-events`：独立板块轮次，不写个股跟踪。
- 板块政策使用 alert_logic/exit_logic=`ANY|ALL`；其余期限、事件日开关与提醒/结束含义相同。锚点固定日线原始指数点位，不能套到成员股票。
- `GET /sector-tracking-rounds/{id}/members?as_of=&membership_scope=` 是带 round_id 的兼容入口；无需轮次可调用 `/sectors/{topic_code}/members`。latest 与 historical 含义见 [Analytics API](analytics-api.md)。

底部抬高在板块上复用上述严格日/完整周/完整月结构，含该字段的新定义使用`sector-index-dwm-higher-lows-v2`，只判断板块自身指数价，不推导成员信号。

板块指标摘要绑定固定来源及算法，命中仍核验 raw/规范化 SHA；缺本地 K 或预热为 unknown，不自动远程加载。板块没有回测或自动交易接口。

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

水平线返回 `line_type:reference|support|resistance`、`alert_modes:string[]`、`auto_watchlist:boolean`、`revision`、`effective_business_date`。012旧线在015增量迁移后固定为 reference/[]/false，不根据名称猜规则，不扫描历史自动启用。新建或修改可提交上述显式字段；价格、类型、模式或自动收藏改变才递增revision并从当前业务日重新生效，纯名称/颜色不产生新触发版本。`alert_modes:[]`暂停规则，移出自选不暂停规则。

支撑模式为 `support_reclaim`（严格前市场日收盘>=线、当日最低<=线、收盘>=线）与可选风险 `support_breakdown`（前收>=线、收盘<线）；压力模式为 `pressure_close_breakout`（前收<=线、收盘>线）和 `pressure_touch`（low<=线<=high，含等号，不需前收）。穿越模式不回退最近有报价日，缺少严格前市场日报价保持unknown。失守为 `state:invalidated`，单独提示风险，不视为回踩收回，不自动新增自选；其余命中为triggered。事件唯一键为annotation/revision/rule/trade_date，持续站上不重复产生收盘突破。

启用规则保存与排队在同一SQLite事务。`POST /watchlist/line-alerts/evaluate {request_key?}` 显式创建标准source_check job（operation=line_alert_check）；浏览和GET不创建任务、不扫描行情。新手动初始化/增量任务冻结 `line_alert_check_version:explicit-local-line-alert-v1`，主行情成功后接续本地检查；恢复只补该新marker已授权的遗漏，不追溯旧已完成同步任务。暂停/取消与每次领取lease隔离，旧attempt无权续进度或提交。每次任务冻结行情head、规则签名/单调clock及输入快照，改线、删线或切代后旧任务停止提交。

核验从规则生效日或最近已核验交易日起增量补跑至已发布业务日；新规则不看创建前日期，未知旧日期保留待核验并可在新代际重验。一次至多128条线涉及的股票，历史每次批读最多20个交易日及其前一交易日，逐日仍严格核对当日和前一市场交易日；另读取当前日及公司行动，不联网，不展开全市场Python历史。每条rule/day的结果持久化，可在中断后跳过已提交结果。离线多日补跑时event.trade_date保持真实历史触发日，若自动加入自选，added_business_date与独立收盘基准采用实际执行的当前业务日，不冒称历史已收藏。

固定前复权锚水平线使用同一event_affine算法，按已核验公司行动将原锚线价与前收映射到事件日raw价基准；事件同时保存 `line_price` 原锚价和实际判定 `comparison_line_price`，两者不能混称。缺少行动证据、无效/重复行动或锚后转换失败保持unknown。none原始线在除权日不直接混比前收，touch可单独使用当日raw范围。

`GET /watchlist/line-alerts?as_of=&limit=100` 返回所有股票（包括尚未自选）的已存事件、total、business_date、当前rules_total、latest_check和unknowns；历史只含trade_date<=as_of，不从当前行情反推旧事件。latest_check含job_id、checked_at_ms、market_snapshot_id、checked_rules（本次rule/day结果数）、trigger_count、unknown_count，以及 `is_current/stale/stale_reason`，行情日、head或规则改变后不能将旧完成回执冒充当前完成。

事件保存event_id、instrument_id/name/code、annotation_id/revision、line_type/rule、trade_date、原价与判定价、price_basis/anchor_date、raw OHLC、严格前收/转换前收、转换依据、state/title/reason、创建时间和冻结market_snapshot_id/input_snapshots；引用快照以manual retention pin保存，删除水平线不删除历史证据。`/watchlist`每行latest_line_alert通过按股票分组的批量SQLite摘要返回，不依赖提醒列表limit或逐股GET。

仅triggered且auto_watchlist启用时可自动收藏：事件与新自选同一短事务写入，已有自选保持原日期、来源与基准，新增source_kind=stock/source_name=划线提醒/source_id=annotation_id，无跟踪轮次时不伪造策略事件价。`added_to_watchlist/watchlist_add_state`分别记录added、already_present、disabled、not_eligible（失守）、capacity_reached、missing_current_quote；容量或当前收盘缺失仍保存可见事件和解释，不让整次核验失败。原事件重复核验不再次收藏，因此这些未入池事件可由用户手动收藏。unknowns只说明缺口，不伪造触发或价格。

## 上市身份与人工确认

`GET /data/listing-status` 返回 `business_date/market_snapshot_id/revision/summary_is_current/identities/confirmations`。identities含 `stock_count/catalog_count/listed_count/known_count/pending_listing_count/unknown_count/pending_listing/unknown/indices_excluded`，名录数量包括待上市，不将指数放入股票分母。已上市日期覆盖分母为listed+unknown，pending单列，不要求尚未上市证券已有历史K线。

有效来源list_date不晚于观察日为listed；来源日期在未来且本代已核验无报价记录为source pending，若已有报价则保留身份冲突unknown。来源日期缺失时，仅显式人工pending确认且本代无报价记录可标pending；没有确认的缺报仍unknown。来源仍缺日期但已有报价为quoted_unknown_date，计入unknown，不以首根报价伪造上市日。重复、无效或仅存在于原始文件的报价也阻止宣称无报价，原始文件不可核验时同样不能确认pending；quote_presence分开记录valid_quote_count、analysis_observed_rows、raw_observed_rows、observed_quote_rows、ambiguous_quote_keys与absence_verified。新来源日期已到达时自动采用来源listed，历史人工确认仍保留。每行有 `instrument_id/name/listing_status/source_list_date/evidence_kind/source/confirmed_date/confirmed_at_ms/effective_business_date/reason/reference/can_edit`；confirmed_date兼容表示确认观察业务日，confirmed_at_ms为实际审计创建时间。reference保存本代报价计数/首末日以及所依据的人工审计，不把用户说明标成官方证据。

`POST /data/listing-status/confirm {instrument_ids,status,reason,evidence?,observed_business_date?,request_key?}` 是明确人工操作。每次1至128只完整A股代码；status只接受pending或unknown，后者撤销人工pending判断；reason为1至1000字符，evidence最多4000字符。观察日期仅当前规范业务日，省略时由服务器冻结。禁止客户端填写上市日期；来源已提供有效上市日或本地出现唯一有效OHLCV/成交额报价（包括合法零量K线）的证券不能标pending。即使尚无唯一有效报价，只要原始包或分析序列留有异常、重复、非市场日等记录，也保持待核验；没有通过原始文件核验时不确认无报价。报价聚合在写锁外，提交核对head、业务日及人工revision，变化返回409，原始行情不改。请求key同参数重放返回原confirmation_ids，参数改变409；重复提交不添加新审计。响应为当前read view加confirmation_ids/already_exists。

016迁移仅新增append-only `listing_status_confirmation`、单调revision及派生摘要，不硬编码股票、不填来源日期、不改行情文件或快照quality。`source_kind`固定manual_user_confirmation，记录用户说明、确认业务日、真实创建时间、原始行情head和无报价证据；unknown更正也是新记录。`GET /data/listing-status/confirmations?instrument_id=&limit=100` 提供全部更正历史（上限1000），不删除旧确认。当前GET只读小名录与SQLite摘要，不扫描历史行情、不联网或排队。

派生摘要精确匹配primary daily_raw head、market generation、业务日、来源名录签名与人工revision。无匹配摘要时不复用旧代人工pending回执，分类保守待核验；人工确认变化使已有本地覆盖报告过期，需重新核验。显式本地覆盖检查可复用已有SQL报价聚合写该小摘要。新手动初始化/增量冻结 `listing_status_check_version:listing-evidence-v1`，成功后接续source_check(operation=listing_status_check)，仅对来源日期缺失/未来的证券分批SQL聚合本地报价，自动捕捉上市或首次报价后的转换；旧任务无marker不扩范围、不回溯自动执行。暂停/取消不自动恢复，领取nonce与head/day/revision共同阻止旧attempt提交。待上市不从远端行情采集股票范围排除，后续真实行情照常接纳。

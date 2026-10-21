# Workspace API v1

根路径 `/api/v1`；成功直接返回对象，列表通常为 `{items,total}`，错误为 HTTP 4xx/5xx `{detail}`。SQLite 是业务事实来源，浏览器不保存策略或跟踪状态。盘面日期不改变本页业务日；执行/入池只用当前已发布业务日，历史运行读取冻结结果。

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

## 个股策略与条件

`GET /strategy-fields` 返回字段 type/operators、周期、fixed_period、整数边界及场景限制；前端依白名单构建，服务端最终验证，不执行用户 Python/SQL/JS。

- `GET/POST /strategies`、`GET/PATCH /strategies/{id}`：name、period=D/W/M、definition、policy、trade_rules；修改追加版本，旧运行冻结。
- definition 为叶 `{field,op,value,period?}` / `{field,op,right_field,period?}`，或嵌套 `{all:[...]}` / `{any:[...]}`；兼容旧 logic/conditions 输入。
- 普通叶默认策略周期，也可逐叶 D/W/M；W/M 使用最近应闭合且有效周期，不能缺最新后退到旧柱。固定日线字段必须 D。右字段只能是兼容白名单数值，常量专用字段不能当右字段。
- 股票范围 board 包含/剔除与当前 is_st 在外层独立 AND。当前身份不是历史逐日 ST/上市档案；未知不能当 false。
- `close_breakout_high` 使用 `op=new_high,value=N`（整数 1–750），观察日日收盘严格大于前 N 个市场交易日 high，排除今日；缺报/零量/行动不可比为未知。
- `limit_up/prev_limit_up` 分别是当日收盘/前一市场交易日收盘来源事实，是/否均需证据。`limit_up_count_1y` 是截至昨日滚动自然一年次数，非负整数常量，不是年内次数；不完整 actual=null，有限上下界可证明的比较才成立。

历史条件节点为 `{history_event:{window:{from:1,to:30},match:"latest",where:条件树,then:条件树}}`；恰好 N 日设置 from=to=N，范围 1–750。latest/earliest 指按距离从近/远优先，不跳过优先位置的未知去伪造匹配。where 支持日 OHLCV/指标自定义 all/any，不能嵌套 history_event，来源涨停/当前题材等未支持字段拒绝。

then 可用 `{field:"close",op:">",right_event_field:"close"|"high"|"low",period:"D"}` 比较观察日收盘与本节点事件价；事件绑定互不串用。跨行动、缺中间有效日报价或来源覆盖不足保持未知。新编辑器历史内部固定 D；无显式 period 的旧 then 保留旧版本解释。checks 保存窗口、日期、偏移、原始价格与原因，不序列化完整历史上下文。

`trade_rules={entry:条件树|null,exit:条件树|null}` 与选股独立版本化：买入为选股 AND entry，exit 不要求仍满足选股。只接受已支持回测特征；不支持题材、未完整历史涨停或跟踪锚点，不因为 AST 能保存便保证可回测。

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

修改默认政策可显式 `apply_to_active_tracking=true`，仅迁移沿用默认的活动轮次，保留自定义与结束轮次。返回影响/跳过数量；缩短已到期不静默结束，需核对。历史事件和入池日/价不可改写。

普通周/月信号日期与入池日分开。入池始终用业务日 T 的真实日收盘，不能拿最近周/月价冒充。运行与核对有任务租约、真实进度和取消；同步 API 用例不支持后台任务式暂停/重试。

## 独立板块策略与跟踪

- `GET /sector-strategy-fields` 返回 `{items,total,target_type:"sector",unknown_policy}`；不是股票字段列表。
- `GET/POST /sector-strategies`、`GET/PATCH /sector-strategies/{id}`：name、period=D/W/M、sector_kind=all/industry/concept、definition、policy、archived；版本独立持久化。
- 板块条件仅 all/any 与白名单叶，逐叶周期；量额/MA/MACD/KDJ/新高可用，股票 ST、涨停、题材成员、换手和 history_event 不支持。固定日线 N 新高不变成 N 周/月。
- `POST /sector-strategies/{id}/runs {request_key?}` 读取本地板块证据实际求值，有可轮询真实 selection 任务；请求返回运行结果。只接受当前业务日，不从盘面历史日期入池。
- `GET /sector-strategy-runs?strategy_id=&limit=`、`GET /sector-strategy-runs/{id}?decision=`：返回冻结版本、目录签名、输入 SHA、cache_refs 及候选。候选含 target_type=sector、target_id/topic_code/topic_id、sector_kind/name、decision/close_price/metrics_by_period/checks/coverage。
- `POST /sector-tracking-rounds {run_id,target_ids}`、GET 列表/详情、POST `/review`、PATCH `/{id}/policy`、POST `/{id}/remove`、GET `/sector-tracking-events`：独立板块轮次，不写个股跟踪。
- 板块政策使用 alert_logic/exit_logic=`ANY|ALL`；其余期限、事件日开关与提醒/结束含义相同。锚点固定日线原始指数点位，不能套到成员股票。
- `GET /sector-tracking-rounds/{id}/members?as_of=&membership_scope=` 是带 round_id 的兼容入口；无需轮次可调用 `/sectors/{topic_code}/members`。latest 与 historical 含义见 [Analytics API](analytics-api.md)。

板块指标摘要绑定固定来源及算法，命中仍核验 raw/规范化 SHA；缺本地 K 或预热为 unknown，不自动远程加载。板块没有回测或自动交易接口。

## 研究与个股回测

`POST /research/jobs {instrument_id}` 手动创建任务，GET 列表及 `/research/jobs/{identifier}` 回读报告。研究限定标的和材料范围，主动访问后七天过期；列表轮询不续期。材料、来源日期、获取时间、单位和缺口分开，清理不影响行情或业务轮次。

`POST /backtests/availability {strategy_id}` 返回可用区间、预热、身份/条件限制；`POST /backtests` 冻结策略、规则、区间和配置，唯一 worker 执行；GET 列表/详情读取结果账本。七项 stats 为 total_return/annual_return/max_drawdown/win_rate/payoff_ratio/trade_count/final_equity，可无定义项保持 null。

撮合仅受限主板/创业板当前非 ST、有效上市日期、区间连续报价且无公司行动样本。收盘信号下一市场日开盘尝试；T+1、限价、费用、滑点、拒单均留记录，期末不强平。未知不成交；不完整上市身份可使回测在创建前不可用，不伪造权益。不是历史全市场无偏或完整复权账户回测。

精确校验入口见[工作区路由](../backend/app/routers/workspace.py)、[个股条件](../backend/app/domain/conditions.py)和[板块条件](../backend/app/domain/sector_conditions.py)。

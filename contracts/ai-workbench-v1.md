# AI研究与盘面复盘工作台 v1

版本：`ai-workbench-v1`，2026-11-02。本文对应当前源码，取代[旧草案](ai-research-v1-draft.md)，不代表已经发行或部署。[研究v2](research-v2.md)仍是无模型的来源报告；AI会话独立保存。本轮改为每轮查询与流式回答，原来只发送创建时证据的行为仅适用于历史消息，不能继续作为当前限制。

实现入口为`routers/ai.py`、`routers/ai_events.py`、`services/ai_service.py`、`ai_agent_protocol.py`、`ai_agent_runtime.py`、`ai_read_tools.py`、`ai_runtime.py`及`adapters/ai_agent.py`；前端DTO见`features/ai/aiTypes.ts`。

## API

路径相对`/api/v1/ai`。GET只读取或回放本地已存结果，不创建模型、行情或搜索请求，不续历史期限。请求采用严格类型并拒绝未知字段；除注明外写接口返回200。

| 方法与路径 | 请求/查询 | 作用 |
| --- | --- | --- |
| GET `/providers` | 无 | `{items,usage}`，配置、验证、本地模型目录与共享预算 |
| PUT `/providers/{provider_id}` | `ProviderUpdate` | 保存配置，不调用模型 |
| DELETE `/providers/{provider_id}/credential` | `expected_revision` | 清除凭据、关闭服务并使验证失效 |
| POST `/providers/{provider_id}/verify` | `{expected_revision,request_key}` | 202，排队主动验证 |
| GET `/context-preview` | `kind=stock|market,report_id?,as_of?` | 兼容的既存证据预览；不是新对话创建或续问的准入条件 |
| POST `/sessions` | `SessionCreate` | 202，`{session_id,task_id}`，保存页面锚点并排队首问 |
| GET `/sessions` | `kind?,instrument_id?,as_of?,cursor?,limit=20` | 历史分页，limit为1—50 |
| GET `/sessions/{id}` | 无 | `{session,messages}`，完整本地历史 |
| POST `/sessions/{id}/messages` | `{text,consent:true,request_key}` | 202，为本轮问题重新准备工具与协议 |
| PATCH `/sessions/{id}` | `{keep:boolean}` | 保留或取消保留 |
| DELETE `/sessions/{id}` | 无 | 删除会话正文与来源，保留独立费用记录 |
| GET `/tasks/{id}` | 无 | 状态、脱敏原因、usage与费用 |
| GET `/tasks/{id}/events` | `after>=0`，可带`Last-Event-ID` | SSE回放已有任务事件，不启动生成 |
| POST `/tasks/{id}/cancel` | 无 | 请求取消；发送后可能已计费 |

`provider_id`只允许`deepseek/openai`。模型发现属于主动验证，不存在任意URL、SQL、文件执行或通用任务AI接口。

## 配置、同意与会话绑定

`ProviderUpdate`形状：

```json
{
  "expected_revision": 0,
  "model": "deepseek-flash",
  "enabled": false,
  "api_key": "<仅写入，不回显>",
  "limits": {"daily_budget_usd":"0.50","daily_request_limit":20,"timeout_seconds":45}
}
```

`api_key`省略、null或空串保留旧Key；专门DELETE才清除。Key最长1000字符，只保存密文引用，主密钥外置；模型ID为1—100个合法标识字符。允许保存手工ID，单价未知时不出站。客户端不能设置Base URL、请求头或来源路径。

保存以`expected_revision`核对配置代际，不匹配409。替换Key或模型后重新验证并显式启用；关闭无需验证。验证签名覆盖服务、模型、凭据代际及超时，单纯预算/次数变化不要求重新验证。关闭或请求配置变化持久使在途任务失效，快速关闭再开启不能复活旧任务。

Provider含`provider_id,name,configured,enabled,model,config_revision,verification,models,discovered_models,limits`。configured只表示密文引用存在。验证访问官方模型目录，再发送不带业务资料的固定微型Schema请求，须返回`{"probe":"ok","missing":null}`；验证也计费，不证明金融解释、工具调用或所有网络环境均正确。GET目录不联网。

`SessionCreate`：

```text
{kind:'stock'|'market',provider_id,consent:true,request_key,
 text?,instrument_id?,report_id?,as_of?,context_digest?,
 expected_config_revision?,expected_model?,allow_web?}
```

首问最多2000字符；空白兼容使用入口默认问题。instrument/report/as_of只是页面对象锚点；无有效研究、未发布当天日线或没有可发送预览，不妨碍创建对话和查询可用性。报告存在时可以取得其证券与行情截止；这不表示先前完整报告已经发给模型。context_digest保留兼容字段，新链路不要求匹配预览哈希。`allow_web`为旧客户端兼容字段，不控制本轮工具或构成用户模式选项。

发送旁显示服务、资料查询与费用说明，点击发送对应本次`consent:true`；浏览、填建议问题或GET不产生授权调用。新前端提交看到的配置代际和模型，服务端短事务再次核对。

会话仍固定服务、模型和凭据代际。更换这些身份需显式新对话，不静默将旧消息交给其他服务。调整超时不把现有会话永久作废，但当前配置仍须有效验证；在途请求继续按配置失效规则处理。会话不固定查询日期、资料版本、联网模式或后续回答协议。

SessionSummary含`session_id,kind,report_id,instrument_id,as_of,provider_id,model,context_digest,keep,created_at_ms,updated_at_ms,expires_at_ms`及可选首问title；列表按更新时间和ID倒序分页，不读消息正文全集。详情另含context，新建为小型`kind:page_anchor`对象，旧会话可保留原完整证据。兼容返回的mode/allow_web只描述旧存储格式，不表示当前轮次是否可联网；实际结果见消息source_context.web。

## 每轮角色与资料查询

当前角色是A股盘面与个股研究助手，回答应自然、直接、独立判断。明显无关话题简短说明范围；普通招呼可简短回应。通用金融概念不要求为了形式调用行情工具。涉及实际行情、公司或资金事实时，应先查适合的本地工具，不能仅凭旧消息或旧证据声称没有资料。

首问和旧会话续问都生成当前轮协议`ai-agent-round-v4`、页面锚点、本次北京时间及有界近期历史。执行前刷新时钟，并与提交时刻分开留证，避免排队跨日后沿用昨日“现在”。旧消息和旧context保留原文；旧context仅可作为明确旧日期的个股资料回退，不限制新问题查今天或其他明确日期，也不代表每轮都发送整套原证据。

用户说“今天/现在/最新”时，数据工具使用`today`并由服务器实际`Asia/Shanghai`时钟解析。页面as_of不是今天的替代值。工具结果的日期、实际采集窗口、报告期、获取/披露时间分别保存；现在取得的资料不倒填为历史当时可知。

本地工具只由唯一worker内AI控制线程执行。固定三项：

| 工具 | 参数 | 范围 |
| --- | --- | --- |
| `read_market_data` | `date:'today'|ISO,section` | section为availability/auction/noon/close/overview/fund_flow |
| `check_data_availability` | `date:'today'|ISO` | 检查资料与阶段，不采集 |
| `read_stock_research` | `instrument_id`，空串沿用页面证券 | 当前实例已存未过期v2报告，不延长TTL；不存在时仅可回退同股会话旧材料并说明旧日期 |

新轮冻结`local_tool_budget`版本`ai-local-tool-budget-v2`：实质资料读取最多3次，可用性检查最多2次，总读取最多5次；`max_local_tools=5`仍表示总额。两种availability入口共用同一元数据额度。旧v3及更早缺少新预算的请求仍按其原`max_local_tools`总额（最多3）执行，不扩大冻结范围。预算耗尽返回`state:not_read,reason_code:tool_budget_exhausted`普通缺口，不代表资料不存在；处理完同批合法调用的call_id配对后，下一次仅总结且不新增联网。模型请求仍最多3次，日预算、次数、权限和取消仍独立硬校验。

Responses function schemas均strict，additionalProperties=false，不接受模型提供的路径或SQL。每次工具JSON最多24KiB，固定返回`{ok,state,tool_name,date,source,summary,evidence,metrics,gaps,source_bindings}`。身份由日期和来源指纹命名空间区分；原始数值与单位保留，重复日期/口径可通过`summary.metric_defaults`和对应证据的`metric_dates/metric_scope`继承，工具结果附语义说明。只有整条材料可以为容量排除，须披露范围；返回这些字段不等于每个自然语言数字都经过机器验真。

overview优先已发布日盘广度、四指数、成交额及前日队列反馈，并列出各时点可用性。没有所选日完成行情时，附该日最新已存时点摘要；不会拿旧日线代称今天。竞价、午间、午间至收盘的完整比较应分别读取auction/noon/close。小摘要保留来源、采集窗口、snapshot/review/revision及SHA，行业/概念有界取前3，容量不足整条收窄并明示。

日内记录优先部署端显式配置的`SHIXU_AI_READ_SQLITE_PATH`，缺项才使用当前实例同日已存记录。该额外源只用于竞价/午间/收盘摘要；其他行情和研究仍属于当前实例或保持未知。连接使用SQLite mode=ro、query_only、foreign_keys、recursive_triggers和busy_timeout，不创建库、不迁移、不写正式源或读取凭据。发布行情只读不可变读模型，不打开worker正在写的DuckDB，不联网、不建任务。读取前后核对小元数据指纹，变化时返回有界错误而不保存混合结果。

阶段结构含`phase_state,now,expected_phase_at,capture_window,source_ids`：

- `not_due`：今天尚未到竞价结束09:25、午盘11:30或收盘15:00，不能称漏采。
- `not_collected`：观察时点已到但所选日无保存记录；计划采集时间尚未到可单独说明，不能混同尚未午盘/收盘。
- `available`：有当日保存记录，正文还须通过日期、SHA及权限核验。
- `unknown`：资料或历史日历不足，不能推零、猜休市或自动补抓。
- `rest_day`：已有核验日历证据支持休市；不能用星期几兜底。

竞价候选的单股方向不推成全板块；重点名单涨停数不称整板块/全市场排名，行业概念可重叠，融资融券等宽标签不称主线。保留原成员日期，不用当前成员回填。午间到收盘累计成交额差不是净流入，也不是精确午后连续时序。fund_flow只接纳带实际日期的独立来源净流入材料，并保留其供应商分类与覆盖限制；缺失时不以成交额替代。

## 联网、回答与流式读取

用户没有本地/联网模式开关。模型按问题先调用本地只读工具，确需外部资料时可规划`request_web_search`；该函数本身不搜索，后续才启用一次官方原生搜索。仅当前已支持的OpenAI模型可联网，不能为其他模型自动切换服务。实际域名、来源、引用及费用见[联网契约](ai-web-chat-v1.md)。通用概念或本地资料已足够时可以不联网。

新assistant保存`response_type:'agent_answer'`，GET消息返回：

```text
{message_id,task_id,role:'assistant',text,state,created_at_ms,
 response_type:'agent_answer',interpretation:null,partial,
 source_context:{page_anchor,request_time,submitted_request_time?,tool_results,web},
 tool_traces,citations,sources,usage,cost}
```

正文为自然文本，无固定JSON段落或metric占位符要求。工具结果独立按本轮保存，后续来源变化不重写已经完成的答案；允许继续问新日期或重新查源。流中先显示纯文本，完成后用安全React节点排版段落、标题、粗体、列表与表格，原生网络引用单独渲染；不执行HTML、模型写的URL、代码或业务指令。旧JSON/指标占位符/web_answer历史按其原格式读取，不改写，不要求用户为了今天查询另建会话。

SSE为`text/event-stream`，类型`status/tool/delta/done/error`，事件data含task_id、session_id、seq、type及类型载荷；delta含text，tool含name/call_id/state/date/label等。事件ID为递增seq，after与Last-Event-ID取较大值续读；断开重连只读取已有事件，不重新付费生成。连接前先返回真实404；终态或临时事件已清理时done提示客户端GET最终消息。发送后离开页面不取消任务。

每任务事件最多2048条、每事件最多8KiB，累计文本最多64KiB，最终消息封套最多256KiB。完成事件保留15分钟后由worker有界清理，最终历史不依赖delta长期保存。流中错误、取消、超时或进程重启可保存partial正文及已取得来源，不能展示成完整成功；重启后已发送未完成的请求不自动重发。

## 容量、历史与计费

每会话同一时刻仅一项queued/running任务。本地最多200会话、每会话40条消息，用户和assistant各计一条，不是40轮。历史默认30天，创建、发送及完成等主动活动更新期限；GET和SSE不续期。keep=true免于自动到期；取消保留沿用原期限，原期限已过可立即不可见。运行中的会话不按普通到期清除。

本轮发送最近最多4条已完成消息、总历史最多8KiB，再附当前问题、页面锚点和实际工具结果；不发送全站历史，不做跨会话检索、向量记忆或静默摘要。每次完整Responses payload最多96KiB，包含提示词、工具定义、前序本轮结果、历史和模型参数。当前每轮最多3次模型请求、3次本地读取及1次搜索；OpenAI可在同个模型响应声明多个独立本地调用，控制线程仍逐项执行。总轮次有限，不承诺每个模型都在一轮完成任意多段查询；达到后基于已读资料结束并说明具体缺口，不无限循环。

旧证据预览builder仍可构建最多64KiB的context（内容48KiB、单条6KiB、最多64条），用于原历史核对和兼容预览；该大小不代表新会话必须先取得整包材料。更早32KiB/48KiB及JSON指标验证的数字仅描述对应历史版本，不是当前新轮次的固定资料范围。

共享默认限制为北京时间自然日0.50美元、20次；预算USD为0.01—100、次数1—200，配置超时30/45/60秒。新agent每个实际模型请求分别预留、再次核对并登记`ai_request_receipt`；一次用户问题可能有多次模型请求，不能把“一次发送”当一次供应商计费。父任务汇总子回执，不能重复计数；排队尚未发送的父任务也参与准入预留。验证按其独立任务计入预算。受控部署可显式配置`SHIXU_AI_AGENT_TIMEOUT_SECONDS=60|120`延长agent总截止，不能据此保证所有请求都完成。

每次请求冻结当时价格与版本，按完整请求UTF-8字节保守估输入及最大输出预留。有效usage才估算结算，未知、超时、取消或断线保留pending和预留，不能当零费用。缓存写入和命中分别计价，reasoning是output内部明细。联网另加保守搜索预留；估算不是供应商发票、账户余额或硬消费上限。

request_key全AI任务唯一，1—200字符；相同key、相同参数返回原任务，不同参数409。allow_web兼容值不改变新链路幂等身份。新key是新尝试，可能再次计费；没有自动补发、输出修复或换服务重试。

父研究仍按原七日缓存管理；读取工具不续期。已完成AI消息保存自足工具结果，父报告或原行情缓存清理后历史仍可阅读，不pin整套行情。后续若无当前研究，可明确使用同股旧材料，而不能把旧材料称最新。

删除会话前须结束running请求，queued项可取消。删除/到期清理会话、消息、来源及相关请求正文和流事件，保留任务及每次请求的价格、usage和费用凭证；费用不因删除或清Key归零。

## 运行、安全与恢复

AI沿用独立SQLite ai_task队列与唯一running约束，不占通用job running槽。唯一worker取得进程锁后启动AI控制线程；它执行短事务、只读工具与事件保存，网络线程通过有界Queue/handshake申请工具和逐次计费许可，不直接访问SQLite、行情文件或密钥存储。行情主循环不等待HTTP，两个AI线程不写行情DuckDB或发布指针。通用任务或业务锁会暂缓新AI任务，但不保证所有负载下完全无资源竞争；worker --once不启动常驻AI循环。

请求只去固定官方DeepSeek/OpenAI域，TLS校验，不跟随重定向，不接受中转API地址。默认不继承系统代理；部署显式开启`SHIXU_AI_SYSTEM_PROXY_ENABLED=1`时才继承已配置路由，不回显代理认证。Responses使用store:false，DeepSeek reasoning none、OpenAI low；store:false不等于供应商零日志。验证保留微型结构化输出，新问答采用流式自然文本与function tools。

schema35保存配置、会话和任务；schema36新增`ai_task_event/ai_request_receipt`以保存流与独立计费。完成任务清空临时请求和凭据引用；会话本身只保存必要身份绑定。定期仅清理无活动引用且超过缓冲的旧AI密文。备份包含业务AI记录，外置主密钥不入包。

恢复默认关闭AI、增加配置revision并把验证标为stale；未发送恢复任务取消，已发送未完成项保留费用并标记人工核对，清除可重发正文。需要重新验证并显式启用；不能因启动worker自动复用外发许可。取消关闭真实HTTP流，不将Future.cancel当网络已结束的证明。

服务错误通常为`detail:{reason_code,message,origin:'local'}`；标准Pydantic错误和兼容builder中文detail仍可能存在。常见冲突包括revision/request_key/configuration/session_busy/capacity，预算429，无效输入422，过期/不存在404。异步reason_code含authentication_failed/quota_exhausted/rate_limited/timeout/agent_tool_limit/invalid_tool_arguments/stream_limit等；不返回供应商原始body、Key或堆栈。

## 验收状态

历史原型38项交互、24组布局，以及此前冻结证据/JSON/可选联网版本的实测，只证明各自版本，不能充当当前agent、旧会话重新查源或流式端到端验收。受限读工具已有87项相关本地回归通过；真实只读来源证明today随北京时间解释、阶段状态随时点及新记录变化，未调用模型或新采集。新agent旧会话重新查询、真实流式和自动联网已经完成；当前298项后端相关检查、237/237隔离API检查通过。浏览器新双栏布局独立留证，不将历史原型计入。

| 当前集成项目 | 实际结果与依据 |
| --- | --- |
| 新agent后端/前端组合检查 | 298项后端AI、worker、恢复与部署相关检查通过；隔离API237/237、前端构建及AI/流式/导航检查通过 |
| 旧会话查今天、新资料、通用概念与专业范围 | 原用户盘面会话查今日竞价、午评与未到收盘通过；通用财经概念和无关问题实际不取数、不联网；原30条历史消息逐字保留 |
| SSE逐段显示、重连、取消与恢复 | 实际三业务回答首个delta均在任务running时到达；续接、取消、恢复与连续问答回归通过；8组实际GET布局及即时离返阅读位置通过 |
| 真实工具/网络引用与金融语义对账 | 今日时段数据范围/金额/方向人工抽查通过；原个股会话完成一次自动联网搜索，保存4个原生来源；年报PDF身份与5项金额抽样对账通过，不宣称所有数值和投资结论自动核验 |
| 发布状态 | 正式127.0.0.1:8788保持原release-v0118；隔离副本验收不表示已部署 |

独立主入口为`#ai`，`ai_chat`用于恢复已有会话，股票/日期/report参数只作启动锚点；快捷入口导航不创建模型任务。历史在左、正文在右，模型选择在输入下方。无标的新会话在成功且有权限的个股研究工具返回有效证据后，可以建立当前个股锚点及分类；旧冻结协议的会话元数据和历史不因此改写。

流式阅读：默认跟随实际消息高度增长，用户上翻、触摸或键盘阅读暂停，回到底部或“回到最新”恢复；ResizeObserver回调使用rAF，不积压smooth滚动。最终Markdown/来源重排也遵守跟随状态，切页前同步保存阅读位置。生成中复用安全Markdown渲染，尚未完成的来源不变成链接。

股票资源授权见`ai-stock-scope-v3`及逐轮agent契约。授权只采用原用户消息、受核验证券身份，不接受助手文本、默认会话类型或模型reason授信。request_web_search增加purpose、user_quote，保存的accepted plan须在实际hosted出站前重核；拒绝不产生搜索请求或搜索预留，已发模型usage仍保留。明确要求本系统/本地数据时禁止用网页另一口径补成内部结果。新增范围/身份/实际HTTP模拟回归纳入326项AI检查，不构成新真实付费业务验收。

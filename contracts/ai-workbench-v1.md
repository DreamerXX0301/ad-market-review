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
| GET `/sessions/{id}` | 无 | `{session,messages,active_task_id,config_matches}`，完整本地历史及同一读取快照中的活动任务和配置绑定状态 |
| POST `/sessions/{id}/messages` | `{text,consent:true,request_key,attachments?}` | 202，为本轮问题重新准备工具与协议 |
| PATCH `/sessions/{id}` | `{keep:boolean}` | 保留或取消保留 |
| DELETE `/sessions/{id}` | 无 | 删除会话正文与来源，保留独立费用记录 |
| GET `/sessions/{sid}/messages/{mid}/attachments/{aid}` | 无 | 读取同会话原附件；过期或删除后404，不续期 |
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

`api_key`省略、null或空串保留旧Key；专门DELETE才清除。Key最长1000字符，只保存密文引用，主密钥外置；模型ID为1—100个合法标识字符。OpenAI只允许[常用GPT目录](ai-openai-models-v1.md)的八项，拒绝其他新配置；原已启用旧模型仍可关闭，历史不自动改写。DeepSeek沿用手工ID兼容，单价未知时不出站。客户端不能设置Base URL、请求头或来源路径。

保存以`expected_revision`核对配置代际，不匹配409。替换Key或模型后重新验证并显式启用；关闭无需验证。验证签名覆盖服务、模型、凭据代际及超时，单纯预算/次数变化不要求重新验证。关闭或请求配置变化持久使在途任务失效，快速关闭再开启不能复活旧任务。

Provider含`provider_id,name,configured,enabled,model,config_revision,verification,models,discovered_models,limits`。configured只表示密文引用存在。验证访问官方模型目录，再发送不带业务资料的固定微型Schema请求，须返回`{"probe":"ok","missing":null}`；验证也计费，不证明金融解释、工具调用或所有网络环境均正确。GET目录不联网。

`SessionCreate`：

```text
{kind:'stock'|'market',provider_id,consent:true,request_key,
 text?,instrument_id?,report_id?,as_of?,context_digest?,
 expected_config_revision?,expected_model?,allow_web?,attachments?,news_context?}
```

首问最多2000字符；空白兼容使用入口默认问题。instrument/report/as_of只是页面对象锚点；无有效研究、未发布当天日线或没有可发送预览，不妨碍创建对话和查询可用性。报告存在时可以取得其证券与行情截止；这不表示先前完整报告已经发给模型。context_digest保留兼容字段，新链路不要求匹配预览哈希。`allow_web`为旧客户端兼容字段，不控制本轮工具或构成用户模式选项。

从「消息与事件」进入时使用 `kind:'market'` 和明确的 `as_of`。可选锚点 `news_context` 为 `{kind:'day'}`、`{kind:'news',source,id}` 或 `{kind:'event',source,id,days:7|30}`。日期按页面观察日解释，隔夜消息的发布时间仍按来源时间记录。消息和事件在服务端按锚点查找。打开对话后须主动发送才能开始解读。

盘面总览的「AI 完整复盘」新草稿使用 `kind:'market'`、明确的 `as_of` 和 `market_scope:'full_review'`。该范围与个股、研究报告及 `news_context` 互斥。首问通过盘面复盘领域校验后，由服务端先执行一次 `read_market_overview(scope=selected_day)`，再将各盘面研究栏目的同日摘要、样本、来源及缺口交给模型。该读取按现有单次工具和轮次预算约束，只读本地归档，不回退其他日期。已取得证据时，跳过首轮计划中同日、无查询条件的重复总览或全档盘中摘要，保留具体追问和其他日期的读取。完整复盘会话的后续轮次可再次规划全量同日读取；普通会话沿用原流程。

发送旁显示服务、资料查询与费用说明，点击发送对应本次`consent:true`；浏览、填建议问题或GET不产生授权调用。新前端提交看到的配置代际和模型，服务端短事务再次核对。

会话仍固定服务、模型和凭据代际。更换这些身份需显式新对话，不静默将旧消息交给其他服务。调整超时不把现有会话永久作废，但当前配置仍须有效验证；在途请求继续按配置失效规则处理。会话不固定查询日期、资料版本、联网模式或后续回答协议。

SessionSummary含`session_id,kind,report_id,instrument_id,as_of,provider_id,model,context_digest,keep,created_at_ms,updated_at_ms,expires_at_ms`及可选首问title；列表按更新时间和ID倒序分页，不读消息正文全集。详情另含context，新建为小型`kind:page_anchor`对象，旧会话可保留原完整证据。兼容返回的mode/allow_web只描述旧存储格式，不表示当前轮次是否可联网；实际结果见消息source_context.web。

## 用户附件与多模态输入

新建和续问可提交 `attachments:[{name,media_type,data_base64}]`，允许PNG/JPEG/WebP、PDF、UTF-8 TXT/MD/Markdown/CSV/TSV/JSON及DOCX。每次最多3个、单个5MiB、合计10MiB；每会话原附件合计20MiB。API在解析JSON前逐块限制实际请求体14MiB，拒绝伪装类型、非法路径名、损坏内容与重复附件。文件仅在主动发送后持久化；浏览器草稿只在组件内存，不放localStorage或导出。

PDF最多20页，PDF/DOCX只在本地有界提取正文，不执行宏、脚本或外部关系，不识别其中扫描页、图片或图表；没有可读正文时明确拒绝，提示上传页面图片。一次模型上下文内全部附件提取文字合计最多24KiB，不静默截断假装完整。DOCX解压量、条目、压缩比与XML实体，PDF过滤流、解压量、树深及解析工作量均有独立限制。

当前目录的八项OpenAI模型可接收图片；DeepSeek继续支持提取正文与文字文件，图片发送前后两端都拒绝，不能静默换模型。原图片限1200万像素并真实解码核验；送模型前仅在内存中按方向修正、最长边2048且不超过400万像素缩放，使用 `input_image`，不修改原件。各模型或账号的实际图片能力仍受供应商权限影响。

原附件只保存一次，位于用户 `ai_message.interpretation_json` 的 `user_attachments` 封套，含原字节、SHA、大小、类型和有界正文；没有新表/迁移或公共上传目录。任务只冻结消息引用、元数据和有界正文，worker发送前核对消息归属、SHA与大小，临时装配图片；任务、助手审计与历史GET均不重复回传base64。历史GET的用户消息 `interpretation:null`，额外 `attachments` 只含元数据及受控同源下载地址。下载响应不缓存并禁止MIME嗅探。

每轮仅从既有最近4条/8KiB历史窗口中优先带入较新的附件，总量仍受上述3个/10MiB/24KiB限制；附件不构成无限文件记忆。容量不足的旧附件明确标为未带入，必要时重新上传或另开对话。附件内容属于不可信待分析资料，不能提供新业务授权或改变系统角色，正常股票语义计划和只读工具约束继续执行。

独立招呼没有上传附件时，若近期上下文含附件，本轮省略这段历史，避免重传旧附件及可能引用其内容的回答。正常的股票资料追问仍可复用有界历史。规划请求将用户原话与附件资料分开。当前新附件的主题用于理解“看一下这个”等指代，简历、求职等域外用途简短说明服务范围。对当前附件已判为域外且无本地或联网申请的计划，控制层将可选上一问引用清空，仍逐字校验本轮引用。

图片每次实际模型请求额外按每张32768输入token作工程保守预留，以输入/缓存写入的较高价格估计；这不是官方图片换算公式或硬费用上限。历史图片再次发送也重新预留，供应商有效usage决定结算，未知仍待核。沿用原最多3次模型请求/轮和共享额度，不自动补发。附件随原会话删除/到期级联清理，账单独立；备份包含附件，已有静态备份不会随活库删除重写。依据见[OpenAI图片输入](https://developers.openai.com/api/docs/guides/images-vision)及[文件输入](https://developers.openai.com/api/docs/guides/file-inputs)，本项目PDF采用本地正文方案。

## 每轮角色与资料查询

当前角色是A股盘面与个股研究助手，回答应自然、直接、独立判断。明显无关话题简短说明范围；普通招呼可简短回应。通用金融概念不要求为了形式调用行情工具。涉及实际行情、公司或资金事实时，应先查适合的本地工具，不能仅凭旧消息或旧证据声称没有资料。

首问和旧会话续问都生成当前轮协议`ai-agent-round-v5`、页面锚点、本次北京时间及有界近期历史。执行前刷新时钟，并与提交时刻分开留证，避免排队跨日后沿用昨日“现在”。旧消息和旧context保留原文；旧context仅可作为明确旧日期的个股资料回退，不限制新问题查今天或其他明确日期，也不代表每轮都发送整套原证据。

用户说“今天/现在/最新”时，数据工具使用`today`并由服务器实际`Asia/Shanghai`时钟解析。页面as_of不是今天的替代值。工具结果的日期、实际采集窗口、报告期、获取/披露时间分别保存；现在取得的资料不倒填为历史当时可知。

本地工具只由唯一worker内AI控制线程执行。工具种类和完整参数以[逐轮执行流程](../docs/AI对话执行流程.md)和每轮冻结协议为准：

| 工具 | 参数 | 范围 |
| --- | --- | --- |
| `read_market_data` | `date:'today'|ISO,section` | section为availability/auction/noon/close/overview/fund_flow |
| `read_news_events` | `scope:'selection'` | 在消息与事件会话内读取所选日、消息或日程的本地归档 |
| `read_market_overview` | `scope:'selected_day'` | 在完整复盘首轮读取所选日的盘面研究摘要，包括历史监管名单和同日龙虎榜 |
| `check_data_availability` | `date:'today'|ISO` | 检查资料与阶段，不采集 |
| `read_stock_research` | `instrument_id`：完整代码、六位代码或准确证券名，最多80字符；空串沿用页面证券 | 名称与六位代码仅通过本地证券名录精确消歧，未命中或重名保持身份缺口，不借用页面其他证券、不联网；读取当前实例已存未过期v2报告，不延长TTL，不存在时仅可回退同股会话旧材料并说明旧日期 |

`read_stock_trend(date,instrument_id,period,count)` 读取同一已发布代际、未复权的个股日周月K线、有界技术指标、至多30个交易日的可核验收盘涨跌停及同日板块。最近默认 `latest,D,30`，周月默认12根，明确日期不回退，收盘K线不作实时分时。旧冻结工具清单不扩大。

`read_stock_quote(date,instrument_id)`读取名称或代码对应的指定日行情和成交额。`read_limit_up_data(date)`复用涨停梯队明细。`read_market_detail(date,section,query)`复用指数、板块、情绪、近30交易日涨幅榜、监管异动和炸板的本地读服务。涨幅榜题材与行业统计使用与页面相同的筛选前固定前1000样本，多标签合计可能超过100%。涨幅榜与炸板结构的 AI 证据只使用截至观察日可核验的归属，缺分类明确未知。公开页面单独披露的 `classification_reference` 不进入工具证据。

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

任务终止前的 `error` 事件携带 `reason_code`。客户端在后续 `done` 确认终态时保留该原因，并优先读取已保存的助手消息。

每任务事件最多2048条、每事件最多8KiB，累计文本最多64KiB，最终消息封套最多256KiB。完成事件保留15分钟后由worker有界清理，最终历史不依赖delta长期保存。流中错误、取消、超时或进程重启可保存partial正文及已取得来源，不能展示成完整成功；已发送未完成的请求不自动重发。错误事件与保存的助手消息携带可用的 `reason_code`，模型明确返回达到输出上限时记为 `output_truncated`。

供应商流的 SSE 单事件和最终响应对象分别限制为192KiB，按实际事件边界计量，单个 HTTP 数据块可以包含多个事件。每次请求的累计传输预算为768KiB加冻结输出上限每token 1024字节的协议开销（输出上限最多12288 tokens）。此预算独立于64KiB正文、任务事件与最终消息的保存限制，不能用大量小事件或超大单事件绕过保护。

## 容量、历史与计费

每会话同一时刻仅一项queued/running任务。本地最多200会话、每会话40条消息，用户和assistant各计一条，不是40轮。历史默认30天，创建、发送及完成等主动活动更新期限；GET和SSE不续期。keep=true免于自动到期；取消保留沿用原期限，原期限已过可立即不可见。运行中的会话不按普通到期清除。

本轮发送最近最多4条已完成消息、总历史最多8KiB，再附当前问题、页面锚点和实际工具结果；不发送全站历史，不做跨会话检索、向量记忆或静默摘要。每次Responses的文字封套最多96KiB，包含提示词、工具定义、前序本轮结果、历史、提取的附件正文和模型参数；已校验图片的base64传输不当作文字token，图片传输另限30MiB。当前每轮最多3次模型请求、3次实质资料加2次可用性检查（总5次）及1次搜索；OpenAI可在同个模型响应声明多个独立本地调用，控制线程仍逐项执行。总轮次有限，不承诺每个模型都在一轮完成任意多段查询；达到后基于已读资料结束并说明具体缺口，不无限循环。

旧证据预览builder仍可构建最多64KiB的context（内容48KiB、单条6KiB、最多64条），用于原历史核对和兼容预览；该大小不代表新会话必须先取得整包材料。更早32KiB/48KiB及JSON指标验证的数字仅描述对应历史版本，不是当前新轮次的固定资料范围。

共享默认限制为北京时间自然日0.50美元、20次；预算USD为0.01—100、次数1—200，配置超时30/45/60秒。新agent每个实际模型请求分别预留、再次核对并登记`ai_request_receipt`；一次用户问题可能有多次模型请求，不能把“一次发送”当一次供应商计费。父任务汇总子回执，不能重复计数；排队尚未发送的父任务也参与准入预留。验证按其独立任务计入预算。受控部署可显式配置`SHIXU_AI_AGENT_TIMEOUT_SECONDS=60|120`延长普通agent总截止。新提交的完整复盘会话轮次冻结 `detailed-market-review-v1`，总时限为240秒，连接和无数据等待仍受普通超时约束。OpenAI 的正文子请求最多12288输出tokens，规划子请求和普通问答沿用原限制。队列与实际子请求均按对应上限预留，旧冻结轮次不改变。

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
| 本轮检查 | 2026-11-06相关后端2806项、前端192个测试文件通过，构建通过；附件专项26项是其中子集与后续边界补充，不重复累计。独立只读审核与浏览器附件/滚动核查完成，浏览器附件传输采用拦截模拟，未新增真实付费模型调用 |
| 发布状态 | 源码、镜像发行及实例部署分别验收；实际本地提交和恢复包状态见实例私有部署回执，不以本文替代部署结果 |

独立主入口为`#ai`，`ai_chat`用于恢复已有会话，股票/日期/report参数只作启动锚点；快捷入口导航不创建模型任务。历史在左、正文在右，模型选择在输入下方。无标的新会话在成功且有权限的个股研究工具返回有效证据后，可以建立当前个股锚点及分类；旧冻结协议的会话元数据和历史不因此改写。

流式阅读：默认跟随实际消息高度增长，用户上翻、触摸或键盘阅读暂停，回到底部或“回到最新”恢复；ResizeObserver回调使用rAF，不积压smooth滚动。最终Markdown/来源重排也遵守跟随状态，切页前同步保存阅读位置。生成中复用安全Markdown渲染，尚未完成的来源不变成链接。

新轮股票资源授权采用`ai-stock-scope-v4`，详见[逐轮语义任务计划](ai-agent-round-v1.md)和[完整v5角色](prompts/ai-agent-round-v5.md)。第一次模型请求只作真实用户语义计划及读数计划；不要求用户命中股票关键词，也不预先发送本地行情/报告或旧助手正文。控制线程绑定实际用户引用及冻结能力摘要，计划接受后才取数；真正资源边界仍为受限只读函数、日期和证券参数、官方域、预算及取消。概念/招呼计划后自然流式；明确域外/澄清计划不取数，首响应正文不外泄。保存的web plan仍在hosted出站前重核；只要本地数据时不拿网页替代。旧冻结请求继续原门控，不重写历史。


### 同一实例的会话共享

会话详情的 `active_task_id` 为 queued/running 任务 ID 或 null。`config_matches` 表示当前模型和凭据代际是否与会话绑定相容。两者均为只读状态。同会话提交在短事务中串行检查，重用相同请求键时返回原任务，另一项独立提交返回 `session_busy`。客户端保留未受理的草稿。SSE 按任务 ID 和序号续读。

已终止的助手消息在有任务原因时返回 `reason_code`，用于重开历史后说明超时、取消或中断等状态。已收到的部分正文单独标明未完成，任务原因与是否结清费用分别展示。

供应商更新中的 `limits` 支持部分字段。未提交的实例限制保留事务内当前值，保存响应绑定本次提交的配置修订。实例会话、配置和预算由访问该实例的人员共享。账户和租户归属属于独立设计范围。

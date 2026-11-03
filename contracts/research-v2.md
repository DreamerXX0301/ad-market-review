# 个股调研报告 v2

本契约扩展 `POST /research/jobs` 与原有 GET 列表/详情。JSON `schema_version=2`、`research_version=evidence-research-v2`，所有 v1 的 `financials/valuation/market_cap/shareholders/announcements/events/trends/materials/gaps/cache` 字段继续保留。没有 `schema_version` 的原报告按 v1 展示，不补抓、不伪装成 v2。

## 冻结计划与执行

仅新建手动研究在 job.parameters.research_plan 冻结：版本、完整证券、行情快照、目标业务日、`manual_latest`、`price_basis=none`、`indicator_continuity=market_sessions`、上证基准、公告365日/事件30日/研报入口180日窗口、来源清单、外链来源、公开HTTP预算32次/90秒/每次最多6秒/单页4MiB、最多12页公告和200项主营构成。已有无marker任务沿用 v1 采集和报告，不新增来源。

唯一 worker 实际执行，API仅创建任务和读取报告。公开源预算在公开材料阶段开始共享，包含重试；逐响应流块检查冻结字节限额、截止及暂停取消，不先把整页装入内存。容量/截止/取消触发时立即关闭流且不重试；401/403/429后本任务不再请求该来源。90秒在请求及流块边界协作检查，当前在途I/O最多仍受该次6秒超时约束；不是所有来源查询或整个任务耗时承诺。扶摇沿用现有有界客户端，最多五个既有端点，各自原传输重试；单项源故障为 gap，成功材料保留。发布前再次核对任务状态、业务日和行情代际。

不请求雪球、韭研、收费研报接口，不调用模型。当前研究与材料日期分开，不提供任意历史时点的资料回填。七日访问缓存不改：GET详情续留存但不更新资料，列表/轮询不续期；没有长期收藏或新增迁移。

## 新增字段

| 字段 | 类型与内容 |
| --- | --- |
| `frozen_plan` | 实际执行的冻结计划，不由浏览器图表偏好决定 |
| `report_sections` | 固定六项 `{id,title}`：overview/company/valuation/news/technical/evidence |
| `overview` | `{summary,highlights:[{text,evidence_ids}],evidence_count,event_count,model_used:false,historical_research:false,notice}` |
| `company_profile` | `{state,name,main_business,business_scope,industry,items,evidence_ids,reason,report_period?,source?,fetched_at_ms?,published_at_ms?}`；无业务原文不从公告标题或题材生成主营事实 |
| `industry_chain` | `{state,reason,items:[{relation_type,subject,object,description,state,report_period,evidence_ids}]}`；首版只把实际主营构成的产品列为`product/source_statement`，不推断客户/供应商 |
| `institution_views` | `{items:[],state:requires_license,reason,links:[ExternalLink]}`；当前估值倍数、机构预测、机构目标价与技术距离分开 |
| `news` | `{state,scope,window,items:[NewsItem]}`；当前仅实际取得近30自然日公告线索，不冒充媒体新闻或全量风险 |
| `evidence` | `Evidence[]`，每个事实或线索均能引用 |
| `claims` | `{claim_id,text,kind:fact|source_statement|title_clue,evidence_ids}[]`；当前不产出模型解释或未经证实利好利空 |
| `source_coverage` | `{source_id,title,state,reason,accepted_count}[]`；state为available/partial/unknown/unavailable/requires_license |
| `external_links` | `ExternalLink[]`；网页可见不等于抓取、缓存或AI处理许可 |
| `unresolved_questions` | `{question,reason,evidence_ids}[]`，是核查清单而非宣称存在风险 |
| `technical_assessment` | 见下节，确定性本地计算，没有取数或自动交易 |
| `public_request_usage` | `{http_attempts,max_http_attempts,deadline_seconds,elapsed_ms,blocked_sources,scope:public_research_http_only}`，不包含扶摇既有传输 |

`Evidence`：`{evidence_id,instrument_id,title,source,kind,tier,url,published_at_ms,fetched_at_ms,report_period,content_hash,response_json_sha256,identity_basis,official_document,publication_date_known,historical_usable:false,data}`。内容哈希与所对应来源响应JSON哈希分开，缺失/非有限值保持null；官方公告为A层，聚合财报/估值/主营为B层。`url` 仅允许无用户信息的 https，未取得不编造。原始披露日期与抓取日不同。即便有来源披露日也没有证明最初可见/修订版本的完整PIT链；首版所有材料一律`historical_usable=false`，`publication_date_known`仅表明取得来源披露日期，不能用于历史回测资料可见性。

`NewsItem`：`{id,title,source,url,published_at_ms,published_date,event_type,summary,evidence_ids,impact:unknown,impact_label,status,positive_evidence_ids:[],negative_evidence_ids:[],classification_basis,questions}`。同一证券的相同公告ID去重；缺ID以核验URL或证据ID归并。不会仅因标题近似把不同公告合并，更正/澄清与进展公告保留为各自事件。标题关键词只给线索分类，未读取全文时不能确认实施结果或正负经营影响。

`ExternalLink`：`{source_id,title,url,state,kind,reason?}`。`state=external_only/requires_license`是入口状态，不代表取得材料，不计入证据数量。雪球链接绑定代码/市场；韭研为公开入口，请用户按股票代码检索，不冒充已取得对应股票文章。

## 主营构成来源

来源合同核验于2026-10-23：[AKShare官方主营构成实现](https://github.com/akfamily/akshare/blob/main/akshare/stock_fundamental/stock_zygc.py)、[官方股票接口文档](https://akshare.akfamily.xyz/data/stock/stock.html)。只请求东方财富固定域 `https://emweb.securities.eastmoney.com/PC_HSF10/BusinessAnalysis/PageAjax?code=SHxxxxxx`，读取 `zygcfx`，要求SECUCODE完整证券/市场及SECURITY_CODE一致。BJ映射未验证，不请求。

仅保留报告期不晚于查询日的最新一期；按行业/产品/地区分类分开，不混加收入。返回 `items` 含 `{instrument_id,report_period,category_code,category,name,revenue_cny,revenue_ratio,cost_cny,profit_cny,gross_margin_ratio,source,published_at_ms:null,historical_usable:false,unit,evidence_ids}`，比率按来源原始比率（1代表100%）。同分类同项目冲突拒绝；未知分类、错证券、未来期隔离，保留数量说明。

该来源没有可核验原披露时间，视为当前取得的快照并始终partial；不承诺历史时点可见或供应链完整。`main_business`若有产品项目，只给明确标注“来源主营构成中的产品项目”的派生列表，不冒充公司主营业务原文。`business_scope/industry`没有来源时为null。

## 技术评估集成

函数 `app.services.research_technical.build_research_technical(market,symbol,as_of,price_basis='none',benchmark='000001.SH')` 返回实际算法版本及冻结参数：`version/as_of/anchor_date/price_basis/indicator_continuity/parameters/analysis_contract/periods/relative_strength/space_assessment/summary/warnings/frozen_charts_by_period`。

只读同一行情代际，严格市场交易日和完整周月，不使用浏览器图表偏好。按应有最近完整周期评价，不退回旧完整周期冒充最新；已确认转折保存确认时间，参考位不能用未来K线确认；技术距离不是券商目标价或预测收益。冻结图先用全历史计算再裁剪用于显示，报告图与报告数值一致。详细字段以该纯技术模块返回结构和对应测试为准。

## 展示兼容与限制

前端检测 `schema_version===2` 后展示六区，原财报和估值组件继续复用；v1走原布局。证据ID作为内部锚点、原文外链可打开。缺失不填零，材料权限/来源说明按对应区域集中展示。报告通常为partial，因为客户/供应商、机构许可及媒体新闻能力仍有明确缺口；不能据报告结束状态宣称全覆盖。

财务颜色在v1明细与v2快览共用：同口径上年同期的数值增加为红、减少为绿、持平或未知为灰；利润、EPS与净现金流的金额另按正红负绿表示盈亏或净流入流出，收入/资产负债/市值/估值倍数等绝对值不据正负解释增长。同期比较只用该份冻结报告，严格核对证券、来源、币种、单位、`period`、财年及同报告期；不拿相邻季度代替同期，不自行拆单季，重复期或证据缺失保留未知。上年同期大于零才自算百分比，零或负基数仅给差额；利润另标亏损收窄/扩大、扭亏/转亏，现金流不据流入流出判断经营好坏。来源直接返回的同比指标保留原数，未核对基数时注明并保持中性，不和派生同比混用。鼠标停在同比说明可核对基期日期、差额和缺口原因；页面派生展示不改报告或自动取数。

首版不包含收益回测、自动策略、自动加入自选、长期收藏、PDF全文抓取、社区RAG或模型调用。趋势参数是可解释观察规则，不代表已验证的未来收益效果。

手动新建研究的冻结来源可含 `tencent-market-cap`。东方财富快照未取得完整市值时，worker 在同一公开请求预算内尝试腾讯行情快照。备用响应须匹配证券及市场、人民币、来源时刻和总市值/流通市值口径，亿人民币换算为人民币元。单次报告以一份市值快照为准，并显示独立来源及时间；盘中快照不代表技术图的收盘观察日。历史截止不得使用后续快照，旧冻结计划不扩大来源，既有报告按原证据保留。

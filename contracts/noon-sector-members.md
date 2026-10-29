# 午间行业与概念详情

午评的行业/概念名称仍打开板块图，成员入口打开成分股。两者使用同一份午评快照身份，和盘后行业与概念详情分开。页面读取不取数，不新增实时轮询或自动任务。

成员页并列展示午间报价日与成员名单留存日；旧名单日期不表示股票价格停留在旧日。上市板块使用全部、主板、创业板、科创板、北交所、待核验六个快捷按钮，切换回第一页，保留搜索、排序及同一快照身份；它只筛选本份成员，不改变来源事实或上方总体汇总。

`GET /api/v1/market/noon/{snapshot_id}/sector-members?topic_code=884001.TI&board=all&query=&sort=change_pct&direction=desc&page=1&limit=50`：本地只读，严格核验正文SHA与来源代码；`board`接受all/main/chinext/star/beijing/unknown。排序为name/instrument_id/change_pct/latest_price/amount/volume；缺值双向排末尾。服务端先筛选排序全部已存成员，再分页；默认50、最大100。返回`version=noon-sector-view-v1,snapshot_id,content_sha256,date,collected_start_at,collected_end_at,quote_as_of,sector_quote,quote_scope=noon_snapshot,formal_chart_as_of,membership_as_of,membership_source_sha256,members_complete,state,unavailable_reason,total,quote_coverage,board_counts,matched,page,pages,limit,items,notice`。

`items`中的latest_price/change_pct/open/high/low/amount/volume/quote_at仅来自同份`payload.stocks`，缺报保留身份、板别及缺口，绝不退回昨日或今天收盘。成员名单日、股票行情日、实际采集区间与逐条报价时刻分开：来源无逐条时刻则仍未知，不能声称同秒或精确11:30行情。`current_events`只引用本份已核验三池，不以涨跌幅猜涨跌停。

新采集可加`payload.sector_memberships={version:'noon-sector-memberships-v1',source_snapshot_id,items,omitted_count,reason}`。`items[topic_code]`冻结采集所用本地名单的kind/source/members_observed_date/members_complete/instrument_ids及规范JSON的SHA；须核验同来源代码/分类/名单日、成员唯一身份，并与午评冻结成员数量、有效报价数及上涨/下跌汇总对账。它是采集时已有成员，不是午间再次远程采集成员。固定2MiB与100000成员关联上限，并受正文8MiB总上限约束；优先工作台行业/概念各前三强与前三弱，再纳入其他已核验名单，超出项保持明确缺口；不增加迁移或业务表。

旧午评缺该字段时，只能读取其明确原采集代际、核验manifest/reference/CAS SHA并对账的名单。新字段已存在但未纳入、损坏或未知，不跟随当前发布头补齐。GET不改写正文或SHA。正文未留存/已清理404，SHA不符409，筛选或身份参数无效422。

详情复用行业与概念弹窗的布局、元信息、筛选按钮和表格样式。板块图上方以紧凑摘要单独展示同份“午间形成中”报价、采集区间及已知OHLC，不另放大烛形卡片；下方正式历史日/周/月K只读缓存，截止严格前一交易日。两组数据不拼接、不将午间临时bar写入日线或用于指标、策略、跟踪、回测。午间缺OHLC保持未知，缺前一交易日则不打开正式历史背景；不自动创建板块K线取数任务。成员名称打开正式个股历史时同样截止前一交易日。

成本：复用午评已有全市场1000条分页股票snapshot和每50板块一批的指数snapshot，关注10个板块不新增远程请求，也不逐板块逐股票N+1取数。只增加有界名单正文与本地哈希/关联；额外远程费用为0。容量与耗时只能由注明“合成样本”的基准说明，不能当作供应商真实请求延迟。

来源能力对照：[扶摇指数API](https://fuyao.aicubes.cn/docs/api-reference/a-share-index/)区分批量指数最新快照、单指数当前成分和单指数日线历史。此次复用已采最新快照，不额外请求当前成分，也不将日线历史接口当分钟或午盘K线来源。

新增字段在现有不可变午评正文内，随28个自然日自动清理；标准备份复制该正文与`raw/noon`精确来源闭包，不新增旁路文件或额外Key。原始响应、正文和运行回执仍不进入Git。

-- 市序分析事实DDL设计草案 2026-10-20；未接生产。
-- 用DuckDB 1.5.6验证；仅worker私有暂存数据库执行本DDL。
-- 发布为不可变Parquet+manifest；API读发布文件，不跨进程读写同一个活跃DuckDB文件。
-- snapshot_id与SQLite元数据的跨库引用由manifest验收，不伪造跨数据库FOREIGN KEY。
-- 未知值保留NULL/unknown；缺失原始材料与无事件必须区别。
CREATE SCHEMA fact;

CREATE TABLE fact.trading_calendar (
 snapshot_id VARCHAR NOT NULL,
 exchange VARCHAR NOT NULL,
 trade_date DATE NOT NULL,
 session_index BIGINT NOT NULL,
 is_final BOOLEAN NOT NULL, -- 开市日不等于已收盘日，2026-10-20早间不得算完成
 source_id VARCHAR NOT NULL,
 available_at TIMESTAMPTZ NOT NULL,
 PRIMARY KEY(snapshot_id,exchange,trade_date),
 UNIQUE(snapshot_id,exchange,session_index)
);

CREATE TABLE fact.security_identity_history (
 snapshot_id VARCHAR NOT NULL,
 instrument_id VARCHAR NOT NULL, -- 内部稳定ID，代码变更不能自动当新公司或直接覆盖旧身份
 source_id VARCHAR NOT NULL,
 provider_symbol VARCHAR NOT NULL, -- 完整代码，保留.SH/.SZ/.BJ/.TI等身份
 asset_type VARCHAR NOT NULL,
 exchange VARCHAR,
 display_name VARCHAR,
 valid_from DATE NOT NULL,
 valid_to DATE,
 listing_date DATE,
 delisting_date DATE,
 status_quality VARCHAR NOT NULL CHECK(status_quality IN ('confirmed','partial','unknown')),
 available_at TIMESTAMPTZ NOT NULL,
 fetched_at TIMESTAMPTZ NOT NULL,
 evidence_sha256 VARCHAR NOT NULL,
 PRIMARY KEY(snapshot_id,instrument_id,source_id,provider_symbol,valid_from),
 CHECK(valid_to IS NULL OR valid_to>=valid_from)
);

CREATE TABLE fact.trading_status_daily (
 snapshot_id VARCHAR NOT NULL,
 instrument_id VARCHAR NOT NULL,
 trade_date DATE NOT NULL,
 trade_state VARCHAR NOT NULL CHECK(trade_state IN ('tradable','suspended','not_listed','delisted','unknown')),
 board_code VARCHAR,
 risk_identity VARCHAR, -- 当日ST/退市整理等身份，不能拿当前名字推断历史
 limit_up_price DECIMAL(20,8),
 limit_down_price DECIMAL(20,8),
 limit_rule_version VARCHAR,
 source_id VARCHAR NOT NULL,
 available_at TIMESTAMPTZ NOT NULL,
 evidence_sha256 VARCHAR NOT NULL,
 PRIMARY KEY(snapshot_id,instrument_id,trade_date),
 CHECK(limit_up_price IS NULL OR limit_up_price>0),
 CHECK(limit_down_price IS NULL OR limit_down_price>0)
);

CREATE TABLE fact.daily_bar_raw (
 snapshot_id VARCHAR NOT NULL,
 instrument_id VARCHAR NOT NULL,
 source_id VARCHAR NOT NULL,
 provider_symbol VARCHAR NOT NULL,
 trade_date DATE NOT NULL,
 open_price DECIMAL(20,8), high_price DECIMAL(20,8),
 low_price DECIMAL(20,8), close_price DECIMAL(20,8),
 volume_shares DECIMAL(24,4), -- 统一股；TickFlow手×100，保留原值和比例
 turnover_cny DECIMAL(24,4), -- 成交额，不是换手率
 raw_volume DOUBLE, raw_volume_unit VARCHAR NOT NULL,
 raw_turnover DOUBLE, raw_turnover_unit VARCHAR NOT NULL,
 volume_multiplier DECIMAL(18,8),
 currency VARCHAR NOT NULL DEFAULT 'CNY' CHECK(currency='CNY'),
 adjustment VARCHAR NOT NULL DEFAULT 'none' CHECK(adjustment='none'),
 value_state VARCHAR NOT NULL CHECK(value_state IN ('valid','partial','unknown')),
 source_object_sha256 VARCHAR NOT NULL,
 source_row_no BIGINT NOT NULL,
 available_at TIMESTAMPTZ NOT NULL,
 fetched_at TIMESTAMPTZ NOT NULL,
 PRIMARY KEY(snapshot_id,source_id,instrument_id,trade_date),
 CHECK(open_price IS NULL OR open_price>0), CHECK(close_price IS NULL OR close_price>0),
 CHECK(high_price IS NULL OR high_price>0), CHECK(low_price IS NULL OR low_price>0),
 CHECK(high_price IS NULL OR open_price IS NULL OR high_price>=open_price),
 CHECK(high_price IS NULL OR close_price IS NULL OR high_price>=close_price),
 CHECK(low_price IS NULL OR open_price IS NULL OR low_price<=open_price),
 CHECK(low_price IS NULL OR close_price IS NULL OR low_price<=close_price),
 CHECK(high_price IS NULL OR low_price IS NULL OR high_price>=low_price),
 CHECK(volume_shares IS NULL OR volume_shares>=0),
 CHECK(turnover_cny IS NULL OR turnover_cny>=0)
);

CREATE TABLE fact.corporate_action_raw (
 snapshot_id VARCHAR NOT NULL,
 source_id VARCHAR NOT NULL,
 instrument_id VARCHAR, -- 未解析身份仍保留原始记录，在发布质量中标识
 provider_symbol VARCHAR NOT NULL,
 source_event_id VARCHAR,
 source_object_sha256 VARCHAR NOT NULL,
 source_row_no BIGINT NOT NULL, -- 原始对象内行号；同股同ex-date多行不被UPSERT吞掉
 ex_date DATE NOT NULL,
 announced_at TIMESTAMPTZ,
 available_at TIMESTAMPTZ NOT NULL,
 fetched_at TIMESTAMPTZ NOT NULL,
 dividend_per_share DECIMAL(24,10),
 bonus_ratio DECIMAL(24,10),
 allotment_ratio DECIMAL(24,10),
 allotment_price DECIMAL(20,8),
 semantic_state VARCHAR NOT NULL CHECK(semantic_state IN ('confirmed','needs_review','unknown')),
 raw_payload JSON NOT NULL,
 PRIMARY KEY(snapshot_id,source_object_sha256,source_row_no)
 -- 故意不UNIQUE(instrument_id,ex_date)，也不武断CHECK bonus_ratio>=0。
);

CREATE TABLE fact.analysis_bar (
 snapshot_id VARCHAR NOT NULL,
 instrument_id VARCHAR NOT NULL,
 raw_snapshot_id VARCHAR NOT NULL,
 price_basis VARCHAR NOT NULL CHECK(price_basis IN ('none','forward','backward')),
 adjustment_anchor DATE, -- 前后复权锚和事件/供应商算法版本都在manifest中冻结
 adjustment_version VARCHAR NOT NULL,
 period VARCHAR NOT NULL CHECK(period IN ('D','W')),
 period_start DATE NOT NULL,
 period_end DATE NOT NULL,
 observed_through DATE NOT NULL,
 is_complete BOOLEAN NOT NULL,
 session_count INTEGER NOT NULL CHECK(session_count>0),
 open_price DECIMAL(20,8), high_price DECIMAL(20,8), low_price DECIMAL(20,8), close_price DECIMAL(20,8),
 volume_shares DECIMAL(24,4), turnover_cny DECIMAL(24,4),
 quality VARCHAR NOT NULL CHECK(quality IN ('ready','partial','unknown')),
 PRIMARY KEY(snapshot_id,instrument_id,period,period_end),
 CHECK(period_start<=period_end), CHECK(observed_through<=period_end),
 CHECK(price_basis='none' OR adjustment_anchor IS NOT NULL)
);

CREATE TABLE fact.indicator_daily (
 snapshot_id VARCHAR NOT NULL,
 analysis_snapshot_id VARCHAR NOT NULL,
 instrument_id VARCHAR NOT NULL,
 period VARCHAR NOT NULL CHECK(period IN ('D','W')),
 observed_date DATE NOT NULL,
 algorithm_version VARCHAR NOT NULL,
 parameters_sha256 VARCHAR NOT NULL,
 warmup_state VARCHAR NOT NULL CHECK(warmup_state IN ('ready','insufficient','missing')),
 ma5 DOUBLE, ma10 DOUBLE, ma20 DOUBLE, ma30 DOUBLE, ma60 DOUBLE, ma250 DOUBLE,
 macd_dif DOUBLE, macd_dea DOUBLE, macd_hist DOUBLE,
 kdj_k DOUBLE, kdj_d DOUBLE, kdj_j DOUBLE,
 volume_multiple_previous DOUBLE,
 turnover_ratio_pct DOUBLE, -- 原始字段未覆盖或历史流通股本未知则NULL，不拿成交额替代
 limit_up BOOLEAN, -- 依据已验收当日身份/限价；未知NULL
 PRIMARY KEY(snapshot_id,instrument_id,period,observed_date)
);

CREATE TABLE fact.topic_membership_pit (
 snapshot_id VARCHAR NOT NULL,
 topic_id VARCHAR NOT NULL,
 instrument_id VARCHAR NOT NULL,
 origin VARCHAR NOT NULL CHECK(origin IN ('provider','manual')),
 action VARCHAR NOT NULL CHECK(action IN ('include','exclude')),
 valid_from DATE NOT NULL,
 valid_to DATE,
 known_at TIMESTAMPTZ NOT NULL, -- 当时可得时点，不等于声称的生效日
 source_id VARCHAR NOT NULL,
 source_record_key VARCHAR NOT NULL, -- 供应商快照或SQLite人工overrideID
 evidence_sha256 VARCHAR NOT NULL,
 PRIMARY KEY(snapshot_id,topic_id,instrument_id,source_record_key),
 CHECK(valid_to IS NULL OR valid_to>=valid_from)
);

CREATE TABLE fact.market_daily (
 snapshot_id VARCHAR NOT NULL,
 trade_date DATE NOT NULL,
 scope_key VARCHAR NOT NULL, -- 沪深A与沪深京A显式分开
 universe_snapshot_id VARCHAR NOT NULL,
 advancing INTEGER, declining INTEGER, unchanged INTEGER, suspended INTEGER, unknown INTEGER,
 sh_turnover_cny DECIMAL(24,4), sz_turnover_cny DECIMAL(24,4),
 limit_up_count INTEGER, limit_down_count INTEGER, broken_count INTEGER,
 emotion_model_version VARCHAR,
 emotion_score DOUBLE,
 observed_symbols INTEGER NOT NULL CHECK(observed_symbols>=0),
 expected_symbols INTEGER,
 quality VARCHAR NOT NULL CHECK(quality IN ('ready','partial','unknown')),
 basis_json JSON NOT NULL, -- 首封/封板等口径、来源和历史覆盖，不反推盘中路径
 PRIMARY KEY(snapshot_id,trade_date,scope_key),
 CHECK(advancing IS NULL OR advancing>=0), CHECK(declining IS NULL OR declining>=0),
 CHECK(sh_turnover_cny IS NULL OR sh_turnover_cny>=0), CHECK(sz_turnover_cny IS NULL OR sz_turnover_cny>=0)
);

CREATE TABLE fact.market_stock_event (
 snapshot_id VARCHAR NOT NULL,
 source_id VARCHAR NOT NULL,
 source_event_key VARCHAR NOT NULL,
 instrument_id VARCHAR,
 provider_symbol VARCHAR,
 trade_date DATE NOT NULL,
 event_type VARCHAR NOT NULL CHECK(event_type IN ('limit_up','limit_down','break','monitoring','anomaly','disclosure')),
 event_at TIMESTAMPTZ,
 known_at TIMESTAMPTZ NOT NULL,
 board_count INTEGER,
 open_times INTEGER,
 seal_amount_cny DECIMAL(24,4),
 evidence_kind VARCHAR NOT NULL CHECK(evidence_kind IN ('provider_tag','rule_warning','formal_disclosure','unverified')),
 payload JSON NOT NULL,
 PRIMARY KEY(snapshot_id,source_id,source_event_key)
 -- 未知代码的监管记录不丢弃；正式披露与规则预警是不同来源语义。
);

-- P1 回测结果事实；与真实跟踪池没有写路径或共同状态表。
CREATE TABLE fact.backtest_order (
 result_snapshot_id VARCHAR NOT NULL,
 backtest_id VARCHAR NOT NULL,
 order_id VARCHAR NOT NULL,
 signal_id VARCHAR NOT NULL, -- 独立信号ID；入场或退出，不要求每个信号都产生委托
 instrument_id VARCHAR NOT NULL,
 signal_date DATE NOT NULL,
 attempted_date DATE,
 side VARCHAR NOT NULL CHECK(side IN ('buy','sell')),
 state VARCHAR NOT NULL CHECK(state IN ('filled','partially_filled','rejected','unexecuted','cancelled')),
 requested_quantity BIGINT,
 filled_quantity BIGINT NOT NULL DEFAULT 0 CHECK(filled_quantity>=0),
 execution_price DECIMAL(20,8),
 commission_cny DECIMAL(20,4), tax_cny DECIMAL(20,4), slippage_cny DECIMAL(20,4),
 cash_after_cny DECIMAL(24,4),
 reason VARCHAR,
 PRIMARY KEY(result_snapshot_id,backtest_id,order_id),
 CHECK(attempted_date IS NULL OR attempted_date>signal_date),
 CHECK(state NOT IN ('filled','partially_filled') OR (filled_quantity>0 AND execution_price IS NOT NULL AND execution_price>0 AND attempted_date IS NOT NULL)),
 CHECK(state<>'partially_filled' OR (requested_quantity IS NOT NULL AND filled_quantity<requested_quantity)),
 CHECK(requested_quantity IS NULL OR (requested_quantity>0 AND filled_quantity<=requested_quantity))
);
CREATE TABLE fact.backtest_roundtrip (
 result_snapshot_id VARCHAR NOT NULL,
 backtest_id VARCHAR NOT NULL,
 trade_id VARCHAR NOT NULL,
 instrument_id VARCHAR NOT NULL,
 entry_order_id VARCHAR NOT NULL,
 exit_order_id VARCHAR NOT NULL,
 buy_date DATE NOT NULL,
 sell_date DATE NOT NULL,
 quantity BIGINT NOT NULL CHECK(quantity>0),
 entry_cost_cny DECIMAL(24,4) NOT NULL,
 exit_net_proceeds_cny DECIMAL(24,4) NOT NULL,
 net_pnl_cny DECIMAL(24,4) NOT NULL,
 return_pct DOUBLE,
 holding_sessions INTEGER NOT NULL CHECK(holding_sessions>=1),
 PRIMARY KEY(result_snapshot_id,backtest_id,trade_id),
 CHECK(sell_date>buy_date),
 CHECK(net_pnl_cny=exit_net_proceeds_cny-entry_cost_cny)
);
CREATE TABLE fact.backtest_equity (
 result_snapshot_id VARCHAR NOT NULL,
 backtest_id VARCHAR NOT NULL,
 observed_date DATE NOT NULL,
 baseline BOOLEAN NOT NULL,
 cash_cny DECIMAL(24,4) NOT NULL,
 market_value_cny DECIMAL(24,4) NOT NULL,
 equity_cny DECIMAL(24,4) NOT NULL,
 drawdown_pct DOUBLE NOT NULL CHECK(drawdown_pct BETWEEN 0 AND 100),
 positions_json JSON NOT NULL, -- UI结果快照缓存；逐股事实在backtest_position，发布时核对两者
 valuation_quality VARCHAR NOT NULL CHECK(valuation_quality IN ('ready','stale_mark','unknown')),
 PRIMARY KEY(result_snapshot_id,backtest_id,observed_date),
 CHECK(equity_cny=cash_cny+market_value_cny),
 CHECK(cash_cny>=0 AND market_value_cny>=0)
);

-- P1：下列契约支持完整账本；首版撮合仍可限定整单全成/拒单，不宣称已实现部分成交。
CREATE TABLE fact.backtest_signal (
 result_snapshot_id VARCHAR NOT NULL,
 backtest_id VARCHAR NOT NULL,
 signal_id VARCHAR NOT NULL,
 instrument_id VARCHAR NOT NULL,
 signal_date DATE NOT NULL,
 decision_at TIMESTAMPTZ NOT NULL,
 signal_type VARCHAR NOT NULL CHECK(signal_type IN ('entry','exit')),
 decision VARCHAR NOT NULL CHECK(decision IN ('matched','unmatched','unknown')),
 checks_json JSON NOT NULL,
 input_snapshots_json JSON NOT NULL,
 PRIMARY KEY(result_snapshot_id,backtest_id,signal_id)
);
CREATE TABLE fact.backtest_fill (
 result_snapshot_id VARCHAR NOT NULL,
 backtest_id VARCHAR NOT NULL,
 fill_id VARCHAR NOT NULL,
 order_id VARCHAR NOT NULL,
 instrument_id VARCHAR NOT NULL,
 executed_at TIMESTAMPTZ NOT NULL,
 quantity BIGINT NOT NULL CHECK(quantity>0),
 price DECIMAL(20,8) NOT NULL CHECK(price>0),
 gross_cny DECIMAL(24,4) NOT NULL,
 commission_cny DECIMAL(20,4) NOT NULL CHECK(commission_cny>=0),
 tax_cny DECIMAL(20,4) NOT NULL CHECK(tax_cny>=0),
 slippage_cny DECIMAL(20,4) NOT NULL CHECK(slippage_cny>=0),
 PRIMARY KEY(result_snapshot_id,backtest_id,fill_id)
 -- 同一订单多fill合法；总量/均价/费用与order对账由发布验收SQL执行。
);
CREATE TABLE fact.backtest_position (
 result_snapshot_id VARCHAR NOT NULL,
 backtest_id VARCHAR NOT NULL,
 observed_date DATE NOT NULL,
 instrument_id VARCHAR NOT NULL,
 quantity BIGINT NOT NULL CHECK(quantity>=0),
 sellable_quantity BIGINT NOT NULL CHECK(sellable_quantity>=0 AND sellable_quantity<=quantity),
 cost_cny DECIMAL(24,4) NOT NULL CHECK(cost_cny>=0),
 mark_price DECIMAL(20,8),
 market_value_cny DECIMAL(24,4),
 unrealized_pnl_cny DECIMAL(24,4),
 valuation_quality VARCHAR NOT NULL CHECK(valuation_quality IN ('ready','stale_mark','unknown')),
 exit_pending BOOLEAN NOT NULL,
 PRIMARY KEY(result_snapshot_id,backtest_id,observed_date,instrument_id),
 CHECK(mark_price IS NULL OR mark_price>0),
 CHECK(market_value_cny IS NULL OR market_value_cny>=0),
 CHECK(valuation_quality<>'ready' OR (mark_price IS NOT NULL AND market_value_cny IS NOT NULL AND unrealized_pnl_cny IS NOT NULL))
);

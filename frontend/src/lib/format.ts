import {
  isHiddenIndicatorField,
  visibleIndicatorData,
} from "./indicatorVisibility";

export const num = (v: any, digits = 2) =>
  v === null || v === undefined || v === "" || !Number.isFinite(Number(v))
    ? "—"
    : Number(v).toLocaleString("zh-CN", {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      });
export const integer = (v: any) => num(v, 0);
export const percent = (v: any, digits = 2) =>
  v === null || v === undefined || v === "" || !Number.isFinite(Number(v))
    ? "—"
    : `${Number(v) > 0 ? "+" : ""}${num(v, digits)}%`;
/** v is an already-normalized base-unit amount, never a provider 万元 field. */
export const compactQuantity = (v: any, unit = "") => {
  if (v === null || v === undefined || v === "" || !Number.isFinite(Number(v)))
    return "—";
  const n = Number(v);
  const result =
    Math.abs(n) >= 1e12
      ? `${num(n / 1e12)}万亿`
      : Math.abs(n) >= 1e8
        ? `${num(n / 1e8)}亿`
        : Math.abs(n) >= 1e4
          ? `${num(n / 1e4)}万`
          : num(n);
  return result + unit;
};
export const amount = (v: any) => compactQuantity(v, "元");
export const bytes = (v: any) => {
  if (v == null) return "—";
  const n = Number(v);
  return n >= 2 ** 30
    ? `${num(n / 2 ** 30)} GiB`
    : n >= 2 ** 20
      ? `${num(n / 2 ** 20)} MiB`
      : `${num(n / 1024)} KiB`;
};
export const tone = (v: any) =>
  v == null ? "muted" : Number(v) > 0 ? "up" : Number(v) < 0 ? "down" : "muted";
export const dateTime = (v: any) =>
  !v
    ? "—"
    : typeof v === "number"
      ? new Date(v < 1e12 ? v * 1000 : v).toLocaleString("zh-CN", {
          hour12: false,
        })
      : String(v)
          .replace("T", " ")
          .replace(/\.\d+Z$/, " UTC");
export const textValue = (input: any): string => {
  if (isHiddenIndicatorField(input)) return "已隐藏的指标";
  const v = visibleIndicatorData(input);
  return v == null
    ? "—"
    : typeof v === "boolean"
      ? v
        ? "是"
        : "否"
      : Array.isArray(v)
        ? v
            .map((x) =>
              typeof x === "object"
                ? x.name ||
                  x.label ||
                  x.topic_name ||
                  x.topic_id ||
                  JSON.stringify(x)
                : x,
            )
            .join(" · ")
        : typeof v === "object"
          ? v.name || v.label || JSON.stringify(v)
          : String(v);
};
export const label: Record<string, string> = {
  start: "区间起点",
  end: "区间终点",
  unknown_count: "待核验标的",
  universe_count: "名录标的",
  with_intermediate_gaps: "区间缺日报价",
  net_inflow_pct: "净流入占比",
  history_observed_days: "资金历史天数",
  history_kind: "历史序列",
  basis: "统计口径",
  instrument_id: "标的",
  code: "代码",
  name: "名称",
  close: "收盘价",
  change_pct: "今日涨幅",
  change_30d_pct: "30日涨幅",
  return_30d_pct: "30日涨幅",
  pct_change: "涨跌幅",
  amount: "成交额",
  volume: "成交量",
  turnover_rate: "换手率",
  turnover_pct: "换手率",
  topics: "题材",
  topic_names: "题材",
  board_count: "连板",
  consecutive_boards: "连板",
  first_limit_time: "首次封板",
  last_limit_time: "最后封板",
  break_count: "开板次数",
  limit_up_count: "涨停家数",
  member_count: "成员数",
  limit_up_ytd: "年内涨停",
  volume_ratio: "量能倍数",
  technical_observation: "技术观察",
  return_30d: "30日涨幅",
  temperature: "广度温度",
  cycle: "盘面状态",
  model: "计算模型",
  covered_stocks: "覆盖标的",
  window: "统计区间",
  concept_count: "概念目录",
  industry_count: "行业目录",
  members_synced: "已存成员组",
  rank_basis: "排名依据",
  limit_time: "来源涨停时间",
  seal_money: "封单规模",
  core_role: "核心地位",
  calculation: "计算口径",
  observed_count: "实际有行情成员",
  membership_observed_date: "成员采集日",
  kind: "分类",
  unknown: "未知涨跌样本",
  event_type: "事件",
  reason: "原因",
  start_date: "开始日期",
  end_date: "结束日期",
  net_inflow: "净流入",
  status: "状态",
  date: "日期",
  business_date: "数据日期",
  entry_date: "买入日",
  exit_date: "卖出日",
  entry_price: "买入价",
  exit_price: "卖出价",
  quantity: "数量",
  net_pnl: "净盈亏",
  pnl: "盈亏",
  return_pct: "收益率",
  exit_reason: "离场原因",
  side: "方向",
  price: "成交价",
  fee: "费用",
  fees: "费用",
  filled_quantity: "成交量",
  order_id: "委托编号",
  created_at: "创建时间",
  trade_count: "交易数",
  total_return: "总收益",
  annual_return: "年化收益",
  max_drawdown: "最大回撤",
  win_rate: "胜率",
  payoff_ratio: "盈亏比",
  final_equity: "最终权益",
  total_fees: "交易费用",
  up: "上涨",
  down: "下跌",
  flat: "平盘",
  suspended: "停牌",
  missing: "待核验",
  total: "总计",
  count: "数量",
  latest_price: "最新价",
  tracking_change_pct: "跟踪涨幅",
  trading_age: "跟踪天数",
  entry_business_date: "入池日",
  source: "来源",
  origin: "来源",
  risk_labels: "风险提示",
  limit_up: "涨停",
  limit_down: "跌停",
  current_topics: "当前题材 / 行业",
  topics_snapshot: "归属快照",
  strategy_name: "所属策略",
  policy_version: "政策版本",
  duration_sessions: "跟踪期限",
  remaining_sessions: "剩余交易日",
  today_change_pct: "今日涨幅",
  recorded_at_ms: "记录时间",
  finished_at_ms: "完成时间",
  end_business_date: "结束日",
  end_reason: "结束原因",
  valuation_date: "价格日期",
  published_at: "发布时间",
  report_period: "报告期",
  title: "标题",
  content: "内容",
  outcome: "判断结果",
  signal_date: "信号日期",
  close_price: "收盘价",
  data_cutoff: "行情截止",
  captured_at: "采集时间",
  observed_at_ms: "观察时间",
  return_ytd_pct: "年内涨幅",
  last_limit_up_date: "最近涨停日期",
  sessions_since_limit_up: "距今交易日",
  equity: "账户权益",
  initial_cash: "初始资金",
};
export const statusText: Record<string, string> = {
  success: "完成",
  succeeded: "已完成",
  no_change: "无新增",
  partial: "部分完成",
  completed: "已完成",
  running: "运行中",
  queued: "等待中",
  pending: "待处理",
  failed: "失败",
  cancelled: "已取消",
  paused: "已暂停",
  tracking: "跟踪中",
  expired: "期限结束",
  condition_ended: "条件结束",
  manual_removed: "手动移出",
  matched: "满足",
  unmatched: "未满足",
  unknown: "待核验",
  ready: "可用",
  available: "可用",
  unavailable: "不可用",
  unsupported: "不支持",
  unverified: "待验证",
  permission_denied: "无权限",
  configured: "已配置",
  not_configured: "未配置",
  free: "免费",
  registered_free: "注册免费",
  paid: "付费",
  filled: "已成交",
  rejected: "已拒绝",
  unexecuted: "未执行",
  not_filled: "未成交",
  locked: "无权限",
  needs_key: "需要Key",
  verified: "已核验",
  buy: "买入",
  sell: "卖出",
  BUY: "买入",
  SELL: "卖出",
  D: "日线",
  W: "周线",
  alert: "提醒",
  entry: "加入跟踪",
  review: "每日核对",
  condition_end: "条件结束",
  expire: "到期结束",
  manual_remove: "手动移出",
  policy_change: "政策变更",
  correction: "更正",
  satisfied: "满足",
  unsatisfied: "未满足",
  not_applicable: "不适用",
  policy_review_pending: "政策待核对",
};

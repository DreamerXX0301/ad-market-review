export const relations = {
  name: "名称直接关联",
  homophone: "谐音",
  branch: "地支",
  culture: "文化 / IP",
  business: "实际业务",
  custom: "自定义",
};
export const groups = [
  "综合",
  "涨幅表现",
  "涨停活跃",
  "量价规模",
  "技术趋势",
  "跟踪过程",
] as const;
export type Column = {
  key: string;
  label: string;
  type?: "percent" | "number" | "date" | "limit" | "recent" | "topics" | "text";
  reason?: string;
  unit?: string;
};
export const columns: Column[] = [
  {
    key: "change_pct",
    label: "当日涨幅",
    type: "percent",
    reason: "quote_reason",
  },
  {
    key: "return_ytd_pct",
    label: "年内涨幅",
    type: "percent",
    reason: "return_ytd_reason",
  },
  {
    key: "change_30d_pct",
    label: "近30日涨幅",
    type: "percent",
    reason: "change_30d_reason",
  },
  { key: "limit_up_ytd", label: "年内涨停次数", type: "limit" },
  { key: "last_limit_up_date", label: "最近确认涨停", type: "recent" },
  { key: "current_topics", label: "当前行业 / 概念", type: "topics" },
  {
    key: "since_entry_pct",
    label: "加入后涨幅",
    type: "percent",
    reason: "since_entry_reason",
  },
  {
    key: "return_mtd_pct",
    label: "本月涨幅",
    type: "percent",
    reason: "return_mtd_reason",
  },
  ...[5, 10, 20, 60].map((n) => ({
    key: `return_${n}d_pct`,
    label: `近${n}日涨幅`,
    type: "percent" as const,
    reason: `return_${n}d_reason`,
  })),
  {
    key: "limit_up_30d",
    label: "近30日涨停",
    type: "number",
    reason: "limit_up_30d_reason",
  },
  {
    key: "sessions_since_limit_up",
    label: "距确认涨停",
    type: "number",
    unit: "交易日",
    reason: "last_limit_up_reason",
  },
  { key: "today_limit_up", label: "当日涨停核验", type: "text" },
  { key: "today_limit_down", label: "当日跌停核验", type: "text" },
  {
    key: "board_height",
    label: "同日确认连板",
    type: "number",
    reason: "board_height_reason",
  },
  {
    key: "board_types",
    label: "同日确认板型",
    type: "text",
    reason: "board_types_reason",
  },
  ...["open", "high", "low", "close"].map((key, i) => ({
    key,
    label: ["开盘", "最高", "最低", "收盘"][i],
    type: "number" as const,
    unit: "元",
    reason: "quote_reason",
  })),
  {
    key: "volume",
    label: "成交量",
    type: "number",
    unit: "股",
    reason: "quote_reason",
  },
  {
    key: "amount",
    label: "成交额",
    type: "number",
    unit: "亿元",
    reason: "amount_reason",
  },
  {
    key: "turnover_rate",
    label: "换手率",
    type: "percent",
    reason: "turnover_rate_reason",
  },
  {
    key: "volume_vs_5d",
    label: "日量 / 前5日均量",
    type: "number",
    unit: "倍",
    reason: "volume_vs_5d_reason",
  },
  {
    key: "float_market_cap",
    label: "流通市值",
    type: "number",
    unit: "亿元",
    reason: "market_cap_reason",
  },
  {
    key: "total_market_cap",
    label: "总市值",
    type: "number",
    unit: "亿元",
    reason: "market_cap_reason",
  },
  ...[5, 10, 20, 30, 60, 250].map((n) => ({
    key: `ma${n}`,
    label: `MA${n}`,
    type: "number" as const,
    reason: "technical_reason",
  })),
  ...[
    "macd_dif",
    "macd_dea",
    "macd_hist",
    "kdj_k",
    "kdj_d",
    "kdj_j",
    "boll_upper",
    "boll_mid",
    "boll_lower",
  ].map((key, i) => ({
    key,
    label: [
      "DIF",
      "DEA",
      "MACD",
      "K",
      "D",
      "J",
      "BOLL上轨",
      "BOLL中轨",
      "BOLL下轨",
    ][i],
    type: "number" as const,
    reason: "technical_reason",
  })),
  ...[20, 60].map((n) => ({
    key: `distance_${n}d_high_pct`,
    label: `距${n}日最高价`,
    type: "percent" as const,
    reason: "technical_reason",
  })),
  { key: "added_business_date", label: "加入基准日", type: "date" },
  { key: "entry_price", label: "基准收盘", type: "number", unit: "元" },
  { key: "holding_sessions", label: "已过交易日", type: "number" },
  {
    key: "peak_return_pct",
    label: "最高收盘涨幅",
    type: "percent",
    reason: "path_reason",
  },
  {
    key: "drawdown_pct",
    label: "距最高收盘回撤",
    type: "percent",
    reason: "path_reason",
  },
  {
    key: "max_drawdown_pct",
    label: "期间最大收盘回撤",
    type: "percent",
    reason: "path_reason",
  },
  {
    key: "relative_benchmark_pp",
    label: "相对上证表现",
    type: "number",
    unit: "百分点",
    reason: "benchmark_reason",
  },
  { key: "relation_label", label: "生肖关联", type: "text" },
  { key: "end_business_date", label: "移出截止日", type: "date" },
];
export const groupKeys: Record<string, string[]> = {
  综合: [
    "change_pct",
    "return_ytd_pct",
    "change_30d_pct",
    "limit_up_ytd",
    "last_limit_up_date",
    "current_topics",
    "since_entry_pct",
  ],
  涨幅表现: [
    "change_pct",
    "return_ytd_pct",
    "change_30d_pct",
    "return_mtd_pct",
    "return_5d_pct",
    "return_10d_pct",
    "return_20d_pct",
    "return_60d_pct",
    "since_entry_pct",
  ],
  涨停活跃: [
    "limit_up_ytd",
    "limit_up_30d",
    "last_limit_up_date",
    "sessions_since_limit_up",
    "today_limit_up",
    "today_limit_down",
    "board_height",
    "board_types",
  ],
  量价规模: [
    "close",
    "open",
    "high",
    "low",
    "volume",
    "amount",
    "turnover_rate",
    "volume_vs_5d",
    "float_market_cap",
    "total_market_cap",
  ],
  技术趋势: [
    "ma5",
    "ma10",
    "ma20",
    "ma30",
    "ma60",
    "ma250",
    "macd_dif",
    "macd_dea",
    "macd_hist",
    "kdj_k",
    "kdj_d",
    "kdj_j",
    "boll_upper",
    "boll_mid",
    "boll_lower",
    "distance_20d_high_pct",
    "distance_60d_high_pct",
  ],
  跟踪过程: [
    "added_business_date",
    "entry_price",
    "since_entry_pct",
    "holding_sessions",
    "peak_return_pct",
    "drawdown_pct",
    "max_drawdown_pct",
    "relative_benchmark_pp",
    "relation_label",
    "end_business_date",
  ],
};
export function shanghaiYear(now = new Date()) {
  return Number(
    new Intl.DateTimeFormat("en", {
      timeZone: "Asia/Shanghai",
      year: "numeric",
    }).format(now),
  );
}
export function yearLabel(year: number) {
  return `${year}年观察 · ${year + 1}${"鼠牛虎兔龙蛇马羊猴鸡狗猪"[(((year + 1 - 2020) % 12) + 12) % 12]}年`;
}
export function safePreference(raw: string | null) {
  try {
    const value = JSON.parse(raw || "{}");
    return {
      follow: value.follow !== false,
      year:
        Number.isInteger(value.year) && value.year >= 1900 && value.year <= 2199
          ? value.year
          : shanghaiYear(),
    };
  } catch {
    return { follow: true, year: shanghaiYear() };
  }
}
export function selectedColumns(group: string, saved: unknown): Column[] {
  const keys = Array.isArray(saved)
    ? saved.filter(
        (k): k is string =>
          typeof k === "string" && columns.some((c) => c.key === k),
      )
    : groupKeys[group] || groupKeys.综合;
  return [...new Set(keys)].map((key) => columns.find((c) => c.key === key)!);
}

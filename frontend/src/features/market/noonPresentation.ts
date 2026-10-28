/** Noon snapshots are dated retained facts; incomplete or missing values stay unknown. */
type NoonRow = Record<string, any>;

export const noonNumber = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;

/** Only an explicit noon deep link chooses a date; ordinary entry keeps the published default. */
export function noonInitialDate(hash: string): string {
  const [route, search = ""] = hash.replace(/^#/, "").split("?", 2);
  if (route !== "noon") return "";
  const value = new URLSearchParams(search).get("date") || "";
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return "";
  const parsed = new Date(value + "T00:00:00Z");
  return Number.isFinite(parsed.getTime()) &&
    parsed.toISOString().slice(0, 10) === value
    ? value
    : "";
}

export function noonRows(value: unknown): NoonRow[] {
  const items = Array.isArray(value)
    ? value
    : value && typeof value === "object" && "items" in value
      ? (value as NoonRow).items
      : [];
  return Array.isArray(items)
    ? items.filter(
        (item) => item && typeof item === "object" && !Array.isArray(item),
      )
    : [];
}

export function noonLines(value: unknown): string[] {
  const values = Array.isArray(value) ? value : value == null ? [] : [value];
  return values.flatMap((item) => {
    if (typeof item === "string") return item.trim() ? [item] : [];
    if (item && typeof item === "object") {
      const line = item.message || item.reason || item.text || item.summary;
      return typeof line === "string" && line.trim() ? [line] : [];
    }
    return [];
  });
}

export function noonPayload(
  view: NoonRow | null,
  date: string,
): NoonRow | null {
  if (!view || view.date !== date || view.state !== "available") return null;
  const snapshot = view.snapshot;
  const payload = snapshot?.payload;
  if (
    !snapshot ||
    snapshot.date !== date ||
    !payload ||
    typeof payload !== "object" ||
    Array.isArray(payload) ||
    payload.date !== date
  )
    return null;
  return payload;
}

export const noonStateText: Record<string, string> = {
  not_collected: "当日未采集午评",
  queued: "午评采集已排队",
  running: "正在采集午评",
  paused: "午评采集已暂停",
  failed: "本日午评采集失败",
  cancelled: "本日午评采集已取消",
  available: "今日已采集",
  purged: "午评已到期清理",
};

export function noonCaptureState(
  view: NoonRow | null,
  date: string,
  pending = false,
) {
  if (pending)
    return {
      disabled: true,
      label: "正在提交…",
      reason: "同一请求不会重复创建任务。",
    };
  if (!view || view.date !== date)
    return {
      disabled: true,
      label: "手动采集午评",
      reason: "正在核验所选日期的采集状态。",
    };
  const locked = view.locked_for_day === true || view.state !== "not_collected";
  if (locked)
    return {
      disabled: true,
      label:
        view.state === "available" && date !== view.today
          ? "该日已采集"
          : noonStateText[view.state] || "本日已提交采集",
      reason: "每天只允许提交一次，失败、取消或到期清理均不释放当天名额。",
    };
  const allowed =
    view.can_capture === true &&
    view.capture_window?.allowed === true &&
    date === view.today;
  return {
    disabled: !allowed,
    label: "手动采集午评",
    reason:
      typeof view.capture_reason === "string"
        ? view.capture_reason
        : typeof view.capture_window?.reason === "string"
          ? view.capture_window.reason
          : allowed
            ? "本交易日11:31至13:00前可手动提交一次。"
            : "该日期当前不能创建午评采集。",
  };
}

export function noonShouldPoll(view: NoonRow | null, date: string): boolean {
  return view?.date === date && ["queued", "running"].includes(view.state);
}

export function noonCanResume(view: NoonRow | null, date: string): boolean {
  return (
    view?.date === date &&
    view.today === date &&
    view.state === "paused" &&
    view.capture_window?.allowed === true &&
    typeof (view.task?.task_id || view.task?.job_id) === "string"
  );
}

export function noonCalendarDates(
  published: string[],
  response: NoonRow | null,
): string[] {
  const dates = Array.isArray(response?.dates)
    ? response.dates
    : noonRows(response?.items).map((row) => row.date);
  return [
    ...new Set(
      [...published, ...dates, response?.today].filter(
        (value): value is string =>
          typeof value === "string" &&
          /^\d{4}-\d{2}-\d{2}$/.test(value) &&
          Number.isFinite(new Date(value + "T00:00:00Z").getTime()) &&
          new Date(value + "T00:00:00Z").toISOString().slice(0, 10) === value,
      ),
    ),
  ].sort();
}

/** Noon charts may only show completed sessions strictly before the captured day. */
export function noonChartDate(
  published: string[],
  capturedDay: string,
): string | null {
  return (
    [...published]
      .filter((day) => day < capturedDay)
      .sort()
      .at(-1) || null
  );
}

/** Open an explicit snapshot sector with completed-session charts, never a same-name substitute. */
export function noonSectorSelection(
  row: NoonRow,
  capturedDay: string,
  published: string[],
  view: "chart" | "members" = "members",
) {
  if (noonInitialDate(`#noon?date=${capturedDay}`) !== capturedDay) return null;
  const code = row.topic_code || row.instrument_id;
  if (
    typeof code !== "string" ||
    !/^\d{6}\.(TI|SH|SZ)$/.test(code) ||
    !["industry", "concept"].includes(row.kind) ||
    (row.topic_code &&
      row.instrument_id &&
      row.topic_code !== row.instrument_id)
  )
    return null;
  const date = noonChartDate(
    published.filter((day) => noonInitialDate(`#noon?date=${day}`) === day),
    capturedDay,
  );
  if (!date) return null;
  return {
    topic: {
      ...row,
      name: typeof row.name === "string" ? row.name : code,
      kind: row.kind,
      topic_code: code,
      topic_id: `fuyao-${code}`,
      noon_snapshot_date: capturedDay,
      noon_chart_as_of: date,
      noon_scope_notice: `来自${capturedDay}午评；详情使用最晚截至${date}的已发布盘后行情，午间报价与成员汇总仍以午评快照为准，成员名单按指定日独立核验`,
    },
    date,
    view,
  };
}

/** A source batch timestamp is deliberately not returned as an individual quote time. */
export function noonClock(value: unknown): string {
  if (typeof value !== "string" && typeof value !== "number") return "未提供";
  if (typeof value === "number" && (!Number.isFinite(value) || value <= 0))
    return "未提供";
  const parsed = new Date(value);
  if (!Number.isFinite(parsed.getTime())) return "未提供";
  return parsed.toLocaleString("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

export function noonSourceRows(value: unknown): NoonRow[] {
  if (Array.isArray(value)) return noonRows(value);
  if (!value || typeof value !== "object") return [];
  return Object.entries(value).map(([name, row]) => ({
    name,
    ...(row && typeof row === "object"
      ? row
      : { reason: typeof row === "string" ? row : "来源信息未提供" }),
  }));
}

export function noonSourceCaption(row: NoonRow): string {
  const key = String(row.name || row.domain || "");
  const captions: Record<string, string> = {
    stocks: "全市场个股",
    indices: "主要指数",
    sectors: "板块指数",
    calendar: "交易日历",
    catalog_industry: "行业目录",
    catalog_concept: "概念目录",
    catalog_cn_concept: "概念目录",
    limit_up: "涨停池",
    limit_down: "跌停池",
    limit_break: "炸板池",
    statistics: "涨停汇总统计",
    hot_hour: "小时热榜",
    hot_stocks: "小时热榜",
    manifest: "来源证据清单",
  };
  const page = key.match(
    /^(stocks|limit_up|limit_down|limit_break)_page_(\d+)$/,
  );
  if (page) return `${captions[page[1]]} · 第${page[2]}页`;
  const batch = key.match(/^sectors_(\d+)$/);
  if (batch) return `板块指数 · 第${batch[1]}批`;
  return typeof row.label === "string"
    ? row.label
    : captions[key] || captions[row.domain] || "来源域待核验";
}

export function noonSortedRows(
  value: unknown,
  key = "change_pct",
  limit = 10,
): NoonRow[] {
  return [...noonRows(value)]
    .sort((a, b) => {
      const left = noonNumber(a[key]),
        right = noonNumber(b[key]);
      return left == null
        ? right == null
          ? 0
          : 1
        : right == null
          ? -1
          : right - left;
    })
    .slice(0, limit);
}

export function noonPreviousText(previous: NoonRow): string[] {
  if (Array.isArray(previous.summary)) return noonLines(previous.summary);
  const payload = previous.payload || {};
  return [
    ...noonLines(payload.summary),
    ...noonLines(payload.description),
    ...noonLines(payload.stage?.reasons),
    ...noonLines(payload.plan?.actions),
    ...noonLines(payload.plan?.scenarios),
    ...noonLines(payload.insights?.risks),
  ].slice(0, 12);
}

export function noonPreviousFacts(previous: NoonRow): NoonRow | null {
  const value =
    previous.state === "background_only"
      ? previous.payload
      : previous.background;
  return value &&
    typeof value === "object" &&
    value.mode === "previous_session_price_facts" &&
    value.date === previous.date
    ? value
    : null;
}

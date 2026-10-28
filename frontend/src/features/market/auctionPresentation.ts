/** Auction observations are dated, manually captured samples; missing data stays unknown. */
type AuctionRow = Record<string, any>;

export const auctionNumber = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;

export function auctionInitialDate(hash: string): string {
  const [route, search = ""] = hash.replace(/^#/, "").split("?", 2);
  if (route !== "auction") return "";
  const day = new URLSearchParams(search).get("date") || "";
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return "";
  const parsed = new Date(day + "T00:00:00Z");
  return Number.isFinite(parsed.getTime()) &&
    parsed.toISOString().slice(0, 10) === day
    ? day
    : "";
}

export function auctionCodes(text: string): { ids: string[]; error: string } {
  const tokens = text
    .trim()
    .split(/[\s,，;；]+/)
    .filter(Boolean);
  if (tokens.length > 100)
    return { ids: [], error: "每次最多填写100个完整股票代码。" };
  const ids = tokens.map((value) => value.toUpperCase());
  const invalid = ids.find((value) => !/^\d{6}\.(SH|SZ|BJ)$/.test(value));
  if (invalid)
    return {
      ids: [],
      error: `代码“${invalid}”无效，请使用600519.SH等完整代码。`,
    };
  return { ids: [...new Set(ids)], error: "" };
}

export function auctionPayload(
  view: AuctionRow | null,
  date: string,
): AuctionRow | null {
  if (
    view?.date !== date ||
    view.state !== "available" ||
    view.snapshot?.date !== date
  )
    return null;
  const value = view.snapshot.payload;
  return value &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    value.date === date &&
    value.version === "auction-snapshot-v1"
    ? value
    : null;
}

export const auctionStates: Record<string, string> = {
  not_collected: "当日尚未采集",
  queued: "等待工作进程",
  running: "正在核验竞价",
  paused: "采集已暂停",
  failed: "本次采集未完成",
  cancelled: "本日采集已取消",
  available: "已留存竞价",
};

export function auctionCaptureState(
  view: AuctionRow | null,
  date: string,
  pending = false,
) {
  if (pending)
    return {
      disabled: true,
      label: "正在提交…",
      reason: "正在确认本日任务，请稍候。",
    };
  if (!view || view.date !== date)
    return {
      disabled: true,
      label: "手动采集竞价",
      reason: "正在读取所选日期的采集状态。",
    };
  if (view.state !== "not_collected" || view.locked_for_day === true)
    return {
      disabled: true,
      label: auctionStates[view.state] || "本日已提交",
      reason:
        view.state === "failed"
          ? "窗口内可手动重试原任务；候选名单保持原样。"
          : view.state === "available"
            ? "本日竞价已冻结，历史查看只读取这份留存。"
            : "沿用本日原任务，暂停和取消不会自动继续。",
    };
  const allowed =
    date === view.today &&
    view.can_capture === true &&
    view.capture_window?.allowed === true;
  return {
    disabled: !allowed,
    label: "手动采集竞价",
    reason:
      view.capture_reason ||
      view.capture_window?.reason ||
      (allowed
        ? "可在09:25至09:30前手动冻结一次终态竞价。"
        : "仅在今天09:25至09:30前手动采集。"),
  };
}

export function auctionCanAct(
  view: AuctionRow | null,
  date: string,
  action: "retry" | "resume" | "cancel" | "pause",
): boolean {
  if (view?.date !== date || !(view.task?.task_id || view.task?.job_id))
    return false;
  if (action === "cancel")
    return ["queued", "running", "paused"].includes(view.state);
  if (action === "pause") return ["queued", "running"].includes(view.state);
  return (
    date === view.today &&
    view.capture_window?.allowed === true &&
    view[action === "retry" ? "can_retry" : "can_resume"] === true &&
    view.state === (action === "retry" ? "failed" : "paused")
  );
}

export function auctionShouldPoll(
  view: AuctionRow | null,
  date: string,
): boolean {
  return view?.date === date && ["queued", "running"].includes(view.state);
}

/** Numeric unknowns always follow all known values in either direction. */
export function auctionSortRows(
  rows: AuctionRow[],
  key: string,
  direction: "asc" | "desc",
): AuctionRow[] {
  return rows
    .map((row, index) => ({ row, index }))
    .sort((a, b) => {
      const av = auctionNumber(a.row[key]),
        bv = auctionNumber(b.row[key]);
      if (av == null && bv == null) return a.index - b.index;
      if (av == null) return 1;
      if (bv == null) return -1;
      return (av - bv) * (direction === "asc" ? 1 : -1) || a.index - b.index;
    })
    .map(({ row }) => row);
}

/** Join only the frozen previous-session notes to this snapshot's quotes. */
export function auctionPreviousFocus(payload: AuctionRow | null) {
  const empty = {
    previous: null as AuctionRow | null,
    retained: false,
    lines: [] as string[],
    items: [] as AuctionRow[],
  };
  const previous = payload?.previous_review;
  if (
    !previous ||
    typeof previous !== "object" ||
    !payload ||
    previous.date !== payload.previous_date ||
    typeof previous.date !== "string" ||
    previous.date >= payload.date
  )
    return empty;
  const records = (value: unknown): AuctionRow[] =>
    Array.isArray(value)
      ? value.filter(
          (row) => row && typeof row === "object" && !Array.isArray(row),
        )
      : [];
  const strings = (value: unknown): string[] =>
    Array.isArray(value)
      ? value.filter(
          (line): line is string => typeof line === "string" && !!line.trim(),
        )
      : typeof value === "string" && value.trim()
        ? [value]
        : [];
  const retained = previous.state === "retained";
  if (!retained) return { ...empty, previous };
  const start = Date.parse(payload.collected_start_at || "");
  const stamp = auctionNumber(previous.recorded_at_ms);
  if (stamp != null && Number.isFinite(start) && stamp > start) return empty;
  const body =
    previous.payload && typeof previous.payload === "object"
      ? previous.payload
      : {};
  const lines = [
    ...new Set([
      ...strings(body.atmosphere?.summary),
      ...strings(previous.summary),
    ]),
  ];
  const quotes = new Map(
    records(payload.quotes?.items).map((row) => [row.instrument_id, row]),
  );
  const focus = new Map<string, AuctionRow>();
  const ensure = (row: AuctionRow) => {
    const id = row.instrument_id || row.thscode;
    if (typeof id !== "string" || !/^\d{6}\.(SH|SZ|BJ)$/.test(id)) return null;
    if (!focus.has(id))
      focus.set(id, {
        instrument_id: id,
        name: row.instrument_name || row.name || id,
        mainline: null,
        notes: [],
        triggers: [],
        invalidations: [],
        quote: quotes.get(id) || null,
        recorded_at_ms: null,
      });
    return focus.get(id)!;
  };
  for (const row of records(previous.observations)) {
    const target = ensure(row);
    if (target && typeof row.mainline === "string")
      target.mainline = row.mainline;
  }
  for (const row of records(previous.mainlines)) {
    if (row.date && row.date !== previous.date) continue;
    if (
      auctionNumber(row.recorded_at_ms) != null &&
      Number.isFinite(start) &&
      row.recorded_at_ms > start
    )
      continue;
    const target = ensure(row);
    if (!target) continue;
    target.mainline = row.display_mainline || target.mainline;
    target.recorded_at_ms = row.recorded_at_ms;
    for (const entry of records(row.attributions)) {
      for (const [field, label] of [
        ["user_note", "我的判断"],
        ["source_reason", "来源转述"],
        ["catalyst_note", "催化补充"],
      ]) {
        if (typeof entry[field] === "string" && entry[field].trim())
          target.notes.push({
            label,
            text: entry[field],
            source: entry.source_name || null,
          });
      }
    }
  }
  for (const row of records(body.watchlist?.stocks)) {
    const target = ensure(row);
    if (!target) continue;
    target.triggers.push(...strings(row.trigger));
    target.invalidations.push(...strings(row.invalidations));
    if (typeof row.reason === "string" && row.reason.trim())
      target.notes.push({ label: "原关注依据", text: row.reason });
  }
  return { previous, retained, lines, items: [...focus.values()] };
}

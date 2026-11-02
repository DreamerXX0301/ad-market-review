/** Auction observations are dated, automatically captured samples; missing data stays unknown. */
type AuctionRow = Record<string, any>;

export const auctionNumber = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;

/** Compact display labels keep the original evidence wording in their tooltip. */
export function auctionBackgroundTags(value: unknown) {
  const labels: Record<string, { label: string; tone: string }> = {
    严格前日已核验涨停: { label: "昨日涨停", tone: "limit-up" },
    严格前日已核验炸板: { label: "昨日炸板", tone: "break" },
    采集开始前已加入自选: { label: "自选", tone: "watch" },
    用户明确指定: { label: "自定义", tone: "watch" },
    严格前日已留存复盘关注股: { label: "昨日关注", tone: "focus" },
    严格前日已留存观察: { label: "昨日观察", tone: "focus" },
    严格前日已留存逐股主线: { label: "昨日主线", tone: "focus" },
  };
  const reasons = Array.isArray(value)
    ? [
        ...new Set(
          value
            .filter(
              (item): item is string =>
                typeof item === "string" && !!item.trim(),
            )
            .map((item) => item.trim()),
        ),
      ]
    : [];
  return reasons.map((source) => ({
    ...(labels[source] || { label: source, tone: "neutral" }),
    source,
  }));
}

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
  purged: "已到期清理",
};

export function auctionCaptureState(
  view: AuctionRow | null,
  date: string,
  pending = false,
  automation?: AuctionRow | null,
) {
  if (pending)
    return {
      disabled: true,
      label: "正在提交操作…",
      reason: "正在确认原任务操作，请稍候。",
    };
  if (!view || view.date !== date)
    return {
      disabled: true,
      label: "读取竞价状态",
      reason: "正在读取所选日期的采集状态。",
    };
  if (view.state !== "not_collected" || view.locked_for_day === true)
    return {
      disabled: true,
      label: auctionStates[view.state] || "本日已提交",
      reason:
        view.state === "purged"
          ? "该日竞价已到期清理，保留日台账，不重新采集历史竞价。"
          : view.state === "available"
            ? "本日竞价已冻结，历史查看只读取这份留存。"
            : "沿用本日原任务；仅来源暂未就绪时可在窗口内有限自动重试，显式暂停、取消或最终失败不自动继续。",
    };
  if (date !== view.today)
    return {
      disabled: true,
      label: "该日未留存竞价",
      reason: "没有留存的历史竞价保持空白，不从当前报价补造。",
    };
  if (auctionAutomaticToday(automation || null) && date === automation?.today)
    return {
      disabled: true,
      label: "等待服务自动采集",
      reason:
        "已开启每日自动竞价，服务在北京时间09:25:30起、09:30前采集系统名单；无需手动提交。",
    };
  return {
    disabled: true,
    label: "自动竞价未开启",
    reason:
      "请在数据中心开启每日自动竞价；采集时刻固定为北京时间09:25:30，不接受手动采集或手填名单。",
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
  return false;
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

/** Restrict existing event groups to a selected frozen membership intersection. */
export function auctionFilterFeedbackGroups(
  groups: AuctionRow[],
  ids: ReadonlySet<string> | null,
): AuctionRow[] {
  if (!ids) return groups;
  return groups.map((group) => {
    // Missing limit-price evidence cannot become a verified empty subgroup.
    if (
      group.key === "limit_up_open" &&
      !["observed", "reference"].includes(group.state)
    )
      return group;
    const source: AuctionRow[] = Array.isArray(group.items)
      ? group.items.filter(
          (row: unknown) =>
            row !== null && typeof row === "object" && !Array.isArray(row),
        )
      : [];
    const items = source.filter((row) => ids.has(row.instrument_id));
    const values = items
      .map((row) => auctionNumber(row.auction_pct))
      .filter((value): value is number => value !== null)
      .sort((a, b) => a - b);
    const middle = Math.floor(values.length / 2);
    return {
      ...group,
      items,
      count: items.length,
      valid_count: values.length,
      missing_count: items.length - values.length,
      average_auction_pct: values.length
        ? values.reduce((sum, value) => sum + value / values.length, 0)
        : null,
      median_auction_pct: values.length
        ? values.length % 2
          ? values[middle]
          : values[middle - 1] / 2 + values[middle] / 2
        : null,
    };
  });
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

/** Global service authorization is separate from the shared historical view date. */
export function auctionAutomationView(value: AuctionRow | null) {
  if (
    !value ||
    value.version !== "auction-automation-v1" ||
    !Number.isSafeInteger(value.revision) ||
    value.revision < 0 ||
    !["off", "once", "daily"].includes(value.mode) ||
    typeof value.today !== "string" ||
    !/^\d{4}-\d{2}-\d{2}$/.test(value.today)
  )
    return null;
  return value;
}

export function auctionAutomaticToday(value: AuctionRow | null): boolean {
  const automation = auctionAutomationView(value);
  return (
    !!automation && automation.enabled === true && automation.mode === "daily"
  );
}

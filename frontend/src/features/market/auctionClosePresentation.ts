/** Close facts stay in the original pre-open groups; missing values never become zero. */
type CloseRow = Record<string, any>;

export const auctionCloseNumber = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;

export const AUCTION_CLOSE_LABELS: Record<
  string,
  { title: string; tone: string }
> = {
  limit_up_open: { title: "一字涨停开", tone: "limit" },
  repair: { title: "修复观察", tone: "positive" },
  weaken: { title: "转弱观察", tone: "negative" },
  continuation: { title: "高开延续", tone: "positive" },
  weak_continuation: { title: "低开延续", tone: "negative" },
  flat_limit_up: { title: "涨停后平开", tone: "neutral" },
  flat_limit_break: { title: "炸板后平开", tone: "neutral" },
  unknown_event: { title: "前日事件未知", tone: "neutral" },
  unknown_quote: { title: "竞价报价未知", tone: "neutral" },
  event_conflict: { title: "前日事件冲突", tone: "neutral" },
};
const GROUP_ORDER = [
  "limit_up_open",
  "repair",
  "weaken",
  "continuation",
  "weak_continuation",
  "flat_limit_up",
  "flat_limit_break",
];

function records(value: unknown): CloseRow[] {
  return Array.isArray(value)
    ? value.filter(
        (row) => row && typeof row === "object" && !Array.isArray(row),
      )
    : [];
}

function mean(values: number[]): number | null {
  if (!values.length) return null;
  const value = values.reduce((sum, item) => sum + item / values.length, 0);
  return Number.isFinite(value) ? value : null;
}

function comparable(row: CloseRow, field: string): number | null {
  return row.state === "observed" ? auctionCloseNumber(row[field]) : null;
}

/** Pure projection of one date-matched packet; never reassign a stock's group. */
export function auctionCloseGroupsView(
  date: string,
  view: CloseRow | null,
  selectedIds: ReadonlySet<string> | null = null,
): CloseRow[] | null {
  if (
    !view ||
    view.date !== date ||
    view.version !== "auction-close-review-v1" ||
    !Array.isArray(view.groups)
  )
    return null;
  const seen = new Set<string>();
  const groups = records(view.groups).flatMap<CloseRow>((group, index) => {
    if (typeof group.key !== "string" || seen.has(group.key)) return [];
    seen.add(group.key);
    const pendingLimit =
      group.key === "limit_up_open" &&
      !["reference", "observed"].includes(group.state);
    const items: CloseRow[] = pendingLimit
      ? []
      : records(group.items)
          .filter((row) => !selectedIds || selectedIds.has(row.instrument_id))
          .map((row): CloseRow => ({
            ...row,
            display_auction_pct:
              row.auction_state !== undefined &&
              row.auction_state !== "observed"
                ? null
                : auctionCloseNumber(row.auction_pct),
            display_close_pct:
              view.state === "pending" ? null : comparable(row, "close_pct"),
            display_change_pct:
              view.state === "pending"
                ? null
                : comparable(row, "auction_to_close_pct"),
            display_close_price:
              view.state === "pending" ? null : auctionCloseNumber(row.close),
          }));
    const values = (field: string): number[] =>
      items
        .map((row) => auctionCloseNumber(row[field]))
        .filter((value): value is number => value !== null);
    const changes = values("display_change_pct");
    const closes = values("display_close_pct");
    const auction = values("display_auction_pct");
    const metadata = AUCTION_CLOSE_LABELS[group.key];
    return [
      {
        ...group,
        items,
        label: metadata?.title || group.title || group.key,
        tone: metadata?.tone || "neutral",
        unknown_event:
          !metadata ||
          ["unknown_event", "unknown_quote", "event_conflict"].includes(
            group.key,
          ),
        pending_limit: pendingLimit,
        pending_close: view.state === "pending",
        count: pendingLimit ? null : items.length,
        valid_count: changes.length,
        missing_count: pendingLimit ? 0 : items.length - changes.length,
        close_valid_count: closes.length,
        close_missing_count: pendingLimit ? 0 : items.length - closes.length,
        open_mismatch: items.filter(
          (row) => row.state === "mismatch" || row.open_check === "mismatch",
        ).length,
        average_auction_pct: pendingLimit ? null : mean(auction),
        average_close_pct: pendingLimit ? null : mean(closes),
        average_auction_to_close_pct: pendingLimit ? null : mean(changes),
        original_index: index,
      },
    ];
  });
  return groups
    .filter((group) => group.key === "limit_up_open" || group.items.length > 0)
    .sort((a, b) => {
      if (a.key === "limit_up_open") return b.key === "limit_up_open" ? 0 : -1;
      if (b.key === "limit_up_open") return 1;
      if (a.unknown_event !== b.unknown_event) return a.unknown_event ? 1 : -1;
      if (a.unknown_event && b.unknown_event)
        return a.original_index - b.original_index;
      const av = auctionCloseNumber(a.average_auction_pct);
      const bv = auctionCloseNumber(b.average_auction_pct);
      if (av === null && bv !== null) return 1;
      if (bv === null && av !== null) return -1;
      return (
        (av !== null && bv !== null ? bv - av : 0) ||
        GROUP_ORDER.indexOf(a.key) - GROUP_ORDER.indexOf(b.key)
      );
    });
}

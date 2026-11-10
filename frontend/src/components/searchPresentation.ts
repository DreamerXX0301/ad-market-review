type Row = Record<string, any>;
export function instrumentSearchRequest(
  query: string,
  includeSectors = false,
  stocksOnly = false,
): string | null {
  const text = query.trim();
  return text
    ? `${includeSectors || stocksOnly ? "/search" : "/instruments"}?query=${encodeURIComponent(text)}&limit=12${stocksOnly ? "&stocks_only=true" : includeSectors ? "" : "&fuzzy=true"}`
    : null;
}
export function searchResultView(row: Row) {
  const kind = row.kind || row.sector_kind;
  const sector =
    row.target_type === "sector" || kind === "industry" || kind === "concept";
  const label =
    (
      {
        stock: "股票",
        index: "指数",
        industry: "行业",
        concept: "概念",
      } as Record<string, string>
    )[kind] || (sector ? "板块" : "标的");
  const code = sector ? row.topic_code : row.instrument_id;
  const selectable =
    typeof code === "string" &&
    !!code.trim() &&
    (sector ? !!row.topic_code : /^\d{6}\.(SH|SZ|BJ)$/.test(code));
  return {
    sector,
    kind,
    label,
    code,
    selectable,
    matchLabel:
      (
        {
          pinyin: "拼音",
          initials: "首字母",
          homophone: "同音建议",
          typo: "近似建议",
        } as Record<string, string>
      )[row.match_type] || null,
    key: `${sector ? "sector" : "security"}:${kind}:${code}`,
    title: `${row.is_suggestion === true ? "可能想找 · " : ""}${label} · ${row.name || code}（${row.code || code}）`,
  };
}

const recentStocksKey = "workbench-global-recent-stocks-v1";
const recentStocksLimit = 12;
const hourMs = 60 * 60 * 1000;
const dayMs = 24 * hourMs;
const beijingDay = (time: number) => Math.floor((time + 8 * hourMs) / dayMs);

type RecentStock = {
  instrument_id: string;
  code: string;
  name: string;
  kind: "stock";
  selectedAt: number;
};

function recentStock(row: Row, selectedAt: number): RecentStock | null {
  const view = searchResultView(row);
  if (view.sector || view.kind !== "stock" || !view.selectable) return null;
  const id = row.instrument_id;
  return {
    instrument_id: id,
    code: id.slice(0, 6),
    name:
      typeof row.name === "string" ? row.name.trim().slice(0, 100) || id : id,
    kind: "stock",
    selectedAt,
  };
}

function saveRecentStocks(stocks: RecentStock[]) {
  try {
    if (stocks.length)
      localStorage.setItem(recentStocksKey, JSON.stringify(stocks));
    else localStorage.removeItem(recentStocksKey);
  } catch {
    // Browsers may disable storage without disabling search.
  }
}

export function loadRecentStocks(now = Date.now()): RecentStock[] {
  let raw: string | null;
  try {
    raw = localStorage.getItem(recentStocksKey);
  } catch {
    return [];
  }
  if (!raw) return [];
  let values: unknown;
  try {
    if (raw.length <= 8192) values = JSON.parse(raw);
  } catch {
    // Invalid browser storage is discarded below.
  }
  const earliest = beijingDay(now) - 6;
  const stocks = new Map<string, RecentStock>();
  if (Array.isArray(values))
    for (const row of values) {
      if (
        !row ||
        !Number.isSafeInteger(row.selectedAt) ||
        row.selectedAt > now ||
        beijingDay(row.selectedAt) < earliest
      )
        continue;
      const stock = recentStock(row, row.selectedAt);
      if (
        stock &&
        (stocks.get(stock.instrument_id)?.selectedAt ?? -Infinity) <
          stock.selectedAt
      )
        stocks.set(stock.instrument_id, stock);
    }
  const result = [...stocks.values()]
    .sort((left, right) => right.selectedAt - left.selectedAt)
    .slice(0, recentStocksLimit);
  if (JSON.stringify(result) !== raw) saveRecentStocks(result);
  return result;
}

export function rememberRecentStock(row: Row, now = Date.now()) {
  const stock = recentStock(row, now);
  if (!stock) return;
  const result = [
    stock,
    ...loadRecentStocks(now).filter(
      (previous) => previous.instrument_id !== stock.instrument_id,
    ),
  ].slice(0, recentStocksLimit);
  saveRecentStocks(result);
  return result;
}

export function globalSectorSelection(
  row: Row,
  viewDate: string | undefined,
  latestDate: string | undefined,
  useMarketDate: boolean,
  today?: string,
) {
  const view = searchResultView(row);
  const requestedDate = useMarketDate ? viewDate : latestDate;
  if (
    !view.sector ||
    !view.selectable ||
    typeof requestedDate !== "string" ||
    !/^\d{4}-\d{2}-\d{2}$/.test(requestedDate)
  )
    return null;
  const usePublishedClose =
    useMarketDate &&
    requestedDate === today &&
    typeof latestDate === "string" &&
    /^\d{4}-\d{2}-\d{2}$/.test(latestDate) &&
    latestDate < requestedDate;
  return {
    topic: {
      ...row,
      name: row.name || view.code,
      topic_code: view.code,
      topic_id: row.topic_id || `fuyao-${view.code}`,
      kind: row.kind || row.sector_kind,
    },
    date: usePublishedClose ? latestDate : requestedDate,
    ...(usePublishedClose ? { pendingCloseDay: requestedDate } : {}),
    view: "chart" as "chart" | "members",
  };
}

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
export function globalSectorSelection(
  row: Row,
  viewDate: string | undefined,
  latestDate: string | undefined,
  useMarketDate: boolean,
) {
  const view = searchResultView(row);
  const date = useMarketDate ? viewDate : latestDate;
  if (
    !view.sector ||
    !view.selectable ||
    typeof date !== "string" ||
    !/^\d{4}-\d{2}-\d{2}$/.test(date)
  )
    return null;
  return {
    topic: {
      ...row,
      name: row.name || view.code,
      topic_code: view.code,
      topic_id: row.topic_id || `fuyao-${view.code}`,
      kind: row.kind || row.sector_kind,
    },
    date,
    view: "chart" as "chart" | "members",
  };
}

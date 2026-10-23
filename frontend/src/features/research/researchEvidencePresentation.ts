/** Evidence metadata is display-only; no fetching, inference or source upgrading. */
export const researchRows = (value: unknown): Record<string, any>[] =>
  Array.isArray(value)
    ? value.filter(
        (row) => row && typeof row === "object" && !Array.isArray(row),
      )
    : [];

export const researchEvidenceKinds = [
  ["all", "全部类型"],
  ["business", "公司业务"],
  ["financial", "财务报表"],
  ["valuation", "估值与市值"],
  ["holders", "股东资料"],
  ["announcement", "公告事件"],
  ["other", "其他材料"],
] as const;

export function researchEvidenceKind(kind: unknown): string {
  if (typeof kind !== "string") return "other";
  if (kind.startsWith("financial_")) return "financial";
  if (["valuation_snapshot", "market_cap"].includes(kind)) return "valuation";
  if (kind === "business_composition") return "business";
  if (kind === "shareholders") return "holders";
  return kind === "announcement" ? "announcement" : "other";
}

/** Filter only presentation rows. Original evidence indices remain stable for citations. */
export function researchEvidencePage(
  evidence: Record<string, any>[],
  options: {
    query?: string;
    kind?: string;
    dateState?: string;
    page?: number;
    pageSize?: number;
  } = {},
) {
  const query = (options.query || "").trim().toLocaleLowerCase();
  const filtered = evidence
    .map((item, index) => ({ item, index }))
    .filter(({ item }) => {
      const dated =
        typeof item.published_at_ms === "number" &&
        Number.isFinite(item.published_at_ms) &&
        item.published_at_ms > 0;
      if (
        options.kind &&
        options.kind !== "all" &&
        researchEvidenceKind(item.kind) !== options.kind
      )
        return false;
      if (options.dateState === "dated" && !dated) return false;
      if (options.dateState === "undated" && dated) return false;
      return (
        !query ||
        [item.title, item.source, item.report_period, item.kind].some(
          (value) =>
            typeof value === "string" &&
            value.toLocaleLowerCase().includes(query),
        )
      );
    });
  const pageSize = Math.max(1, Math.floor(options.pageSize || 8));
  const pages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const page = Math.max(0, Math.min(pages - 1, Math.floor(options.page || 0)));
  return {
    rows: filtered.slice(page * pageSize, (page + 1) * pageSize),
    total: filtered.length,
    page,
    pages,
  };
}

/** Exact repeated questions share a display row; all cited evidence and originals survive. */
export function groupResearchQuestions(value: unknown) {
  const groups = new Map<string, Record<string, any>>();
  for (const question of researchRows(value)) {
    const key = JSON.stringify([question.question, question.reason]);
    const existing = groups.get(key);
    if (existing) {
      existing.count += 1;
      existing.evidence_ids = [
        ...new Set([
          ...existing.evidence_ids,
          ...(Array.isArray(question.evidence_ids)
            ? question.evidence_ids
            : []),
        ]),
      ];
    } else {
      groups.set(key, {
        ...question,
        count: 1,
        evidence_ids: [
          ...new Set(
            Array.isArray(question.evidence_ids) ? question.evidence_ids : [],
          ),
        ],
      });
    }
  }
  return [...groups.values()];
}
export function researchUrl(value: unknown): string | null {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password
      ? url.href
      : null;
  } catch {
    return null;
  }
}
export const researchState = (state: unknown): string =>
  (
    ({
      available: "已取得",
      partial: "部分材料",
      unknown: "待核验",
      unavailable: "本次未取得",
      requires_license: "需授权来源",
      external_only: "原文入口",
      source_statement: "来源表述",
      strong: "条件偏强",
      weak: "条件偏弱",
      mixed: "条件分歧",
      not_calculated: "尚未计算",
      no_reference: "暂无可核验参考位",
    }) as Record<string, string>
  )[String(state)] || "待核验";
export const researchClaim = (kind: unknown): string =>
  (
    ({
      fact: "可核验事实",
      source_statement: "来源表述",
      title_clue: "标题线索",
    }) as Record<string, string>
  )[String(kind)] || "待核验表述";
export function researchEvidenceRefs(
  ids: unknown,
  evidence: Record<string, any>[],
) {
  const values = Array.isArray(ids) ? ids : [];
  return [...new Set(values.filter((id) => typeof id === "string"))]
    .map((id) => ({
      id,
      index: evidence.findIndex((row) => row.evidence_id === id),
    }))
    .filter((row) => row.index >= 0);
}

/** In-page research links must not replace the application's hash route. */
export function scrollResearchSection(
  event: { preventDefault: () => void },
  id: string,
  root: Pick<Document, "getElementById"> = document,
) {
  event.preventDefault();
  root
    .getElementById(id)
    ?.scrollIntoView({ behavior: "smooth", block: "start" });
}

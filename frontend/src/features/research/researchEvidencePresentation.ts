/** Evidence metadata is display-only; no fetching, inference or source upgrading. */
export const researchRows = (value: unknown): Record<string, any>[] =>
  Array.isArray(value)
    ? value.filter(
        (row) => row && typeof row === "object" && !Array.isArray(row),
      )
    : [];
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

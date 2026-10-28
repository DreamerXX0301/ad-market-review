import type { Row } from "../../lib/api";

/** Copy six-digit stock codes only, preserving the complete displayed order. */
export function noonLadderCopy(rows: Row[]): string {
  const codes = new Set<string>();
  for (const row of rows) {
    const identifier = row?.instrument_id;
    if (
      typeof identifier === "string" &&
      identifier.length === 9 &&
      /^\d{6}\.(SH|SZ|BJ)$/.test(identifier)
    )
      codes.add(identifier.slice(0, 6));
  }
  return [...codes].join("\n");
}

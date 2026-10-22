import type { Row } from "../../lib/api";

export type MainlineAttribution = {
  mainline_name: string;
  is_display_primary: boolean;
  role: "unknown" | "core_candidate" | "followup";
  evidence_status: "source_claim" | "user_judgment" | "unverified" | "conflict";
  source_name: string;
  source_reason: string;
  evidence_ref: string;
  source_published_at: string | null;
  evidence_sha256: string | null;
  catalyst_note: string;
  user_note: string;
  assertion_id?: string;
  first_known_at_ms?: number;
};
export type MainlineRecord = {
  date: string;
  instrument_id: string;
  instrument_name: string;
  revision: number;
  revision_id: string;
  action: "replace" | "revoke";
  source_kind: "manual_user_entry";
  recorded_at_ms: number;
  change_reason: string;
  attributions: MainlineAttribution[];
  display_state: "attributed" | "unattributed" | "primary_pending" | "conflict";
  display_mainline: string | null;
  secondary_mainlines: string[];
};
export const mainlineStatusLabels = {
  source_claim: "来源转述",
  user_judgment: "用户判断",
  unverified: "待核验",
  conflict: "来源冲突",
};
export const mainlineRoleLabels = {
  unknown: "未评级",
  core_candidate: "核心候选（用户标记）",
  followup: "后排（用户标记）",
};
export const mainlineStateLabels = {
  attributed: "已录入归因",
  unattributed: "未归因",
  primary_pending: "主组待定",
  conflict: "来源冲突",
};
export function emptyMainlineAttribution(): MainlineAttribution {
  return {
    mainline_name: "",
    is_display_primary: false,
    role: "unknown",
    evidence_status: "unverified",
    source_name: "",
    source_reason: "",
    evidence_ref: "",
    source_published_at: null,
    evidence_sha256: null,
    catalyst_note: "",
    user_note: "",
  };
}
export function editableAttribution(
  value: MainlineAttribution,
): MainlineAttribution {
  const result = emptyMainlineAttribution();
  // Audit IDs/timestamps never leave through the manual write payload.
  for (const key of Object.keys(result))
    (result as Record<string, unknown>)[key] =
      value[key as keyof MainlineAttribution];
  return result;
}
export function mainlineWritePayload(
  date: string,
  instrumentId: string,
  expectedRevision: number,
  action: "replace" | "revoke",
  changeReason: string,
  values: MainlineAttribution[],
) {
  return {
    date,
    instrument_id: instrumentId,
    expected_revision: expectedRevision,
    action,
    change_reason: changeReason,
    attributions: action === "revoke" ? [] : values.map(editableAttribution),
  };
}
export function mainlineEvidenceUrl(value: string): string | null {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}
export function buildDailyMainlineGroups(
  date: string,
  confirmedRows: Row[],
  records: MainlineRecord[],
) {
  const identities = new Map<string, Row>();
  for (const row of confirmedRows) {
    if (
      typeof row.instrument_id === "string" &&
      !identities.has(row.instrument_id)
    )
      identities.set(row.instrument_id, row);
  }
  const indexed = new Map<string, MainlineRecord>();
  for (const record of records) {
    if (record.date !== date) continue;
    const before = indexed.get(record.instrument_id);
    if (!before || before.revision < record.revision)
      indexed.set(record.instrument_id, record);
  }
  const groups = new Map<
    string,
    {
      key: string;
      name: string;
      items: { stock: Row; record?: MainlineRecord }[];
    }
  >();
  for (const [id, stock] of identities) {
    const record = indexed.get(id);
    const state = record?.display_state || "unattributed";
    const named = state === "attributed" && !!record?.display_mainline;
    const key = named
      ? "mainline:" + record.display_mainline
      : "state:" + state;
    const name = named
      ? record.display_mainline!
      : mainlineStateLabels[state] || "未归因";
    if (!groups.has(key)) groups.set(key, { key, name, items: [] });
    groups.get(key)!.items.push({ stock, record });
  }
  return {
    // Evidence-backed names are visible before the unresolved working queue.
    groups: [...groups.values()].sort((left, right) => {
      const rank = (key: string) =>
        key.startsWith("mainline:")
          ? 0
          : key === "state:primary_pending"
            ? 1
            : key === "state:conflict"
              ? 2
              : 3;
      return rank(left.key) - rank(right.key);
    }),
    total: identities.size,
    attributed: [...identities.keys()].filter(
      (id) => indexed.get(id)?.display_state === "attributed",
    ).length,
    // Never count stored non-pool notes as confirmed limit-up stocks.
    outside: [...indexed.values()].filter(
      (record) => !identities.has(record.instrument_id),
    ),
  };
}

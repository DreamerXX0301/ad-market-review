export type SectorRow = Record<string, any>;
export const sectorPeriodLabel = (value: string) =>
  (({ D: "日K", W: "周K", M: "月K" }) as Record<string, string>)[value] ||
  value;
export const sectorKindLabel = (value: string) =>
  (
    ({ all: "行业与概念", industry: "行业", concept: "概念" }) as Record<
      string,
      string
    >
  )[value] || "分类待核验";
export const sectorFieldLabel = (field: string, fields: SectorRow[]) =>
  fields.find((item) => item.field === field)?.label || field;
export function sectorDefaultLeaf(
  fields: SectorRow[],
  period = "D",
): SectorRow {
  const spec =
    fields.find((field) => field.field === "close_breakout_high") || fields[0];
  if (!spec) return {};
  return {
    field: spec.field,
    ...(spec.fixed_period ? { period: spec.fixed_period } : {}),
    op: spec.operators[0],
    value: spec.type === "boolean" ? true : (spec.default ?? 3),
  };
}
export function sectorStrategyBody({
  name,
  period,
  sectorKind,
  definition,
  duration,
  existingPolicy,
}: {
  name: string;
  period: string;
  sectorKind: string;
  definition: SectorRow;
  duration: number;
  existingPolicy?: SectorRow;
}) {
  return {
    name: name.trim(),
    period,
    sector_kind: sectorKind,
    definition,
    policy: {
      ...(existingPolicy || {
        alerts: [],
        exits: [],
        alert_logic: "ANY",
        exit_logic: "ANY",
        allow_entry_day_alerts: false,
        allow_entry_day_exits: false,
      }),
      duration_sessions: duration,
    },
  };
}
export function validateSectorRule(
  node: SectorRow,
  fields: SectorRow[],
  defaultPeriod = "D",
  depth = 0,
): string | null {
  if (depth > 8) return "条件组最多支持8层嵌套";
  if (node?.history_event || (node?.all && node?.any))
    return "板块条件只支持独立的全部/任一条件组";
  const children = node?.all || node?.any;
  if (children) {
    if (!Array.isArray(children) || !children.length)
      return "条件组至少需要一项条件";
    for (const child of children) {
      const error = validateSectorRule(child, fields, defaultPeriod, depth + 1);
      if (error) return error;
    }
    return null;
  }
  const field = fields.find((item) => item.field === node?.field);
  if (!field) return `此板块场景不支持字段：${node?.field || "空条件"}`;
  const period = node.period || field.fixed_period || defaultPeriod;
  if (
    !(field.periods || ["D", "W", "M"]).includes(period) ||
    (field.fixed_period && period !== field.fixed_period)
  )
    return `${field.label}的周期不受支持`;
  if (!field.operators.includes(node.op))
    return `${field.label}的比较方式不受支持`;
  if (node.right_field != null) {
    const right = fields.find((item) => item.field === node.right_field);
    if (
      Object.hasOwn(node, "value") ||
      field.type !== "number" ||
      field.constant_only ||
      !right ||
      right.type !== "number" ||
      right.constant_only ||
      (right.fixed_period && right.fixed_period !== period) ||
      !(right.periods || ["D", "W", "M"]).includes(period)
    )
      return "比较对象必须是同周期可用的板块数值字段";
  } else if (field.type === "boolean") {
    if (typeof node.value !== "boolean") return `${field.label}请选择是或否`;
  } else {
    if (typeof node.value !== "number" || !Number.isFinite(node.value))
      return `${field.label}请输入有效数值`;
    if (field.integer && !Number.isInteger(node.value))
      return `${field.label}请输入整数`;
    if (
      (field.min != null && node.value < field.min) ||
      (field.max != null && node.value > field.max)
    )
      return `${field.label}超出允许范围`;
  }
  return null;
}
export function sectorCheckLeaves(value: any): SectorRow[] {
  if (Array.isArray(value)) return value.flatMap(sectorCheckLeaves);
  if (!value || typeof value !== "object") return [];
  if (value.field) return [value];
  return sectorCheckLeaves(value.checks || value.all || value.any || []);
}
export function sectorLeafEditorKind(field: SectorRow | undefined): string {
  return field?.parameter_kind === "lookback_sessions" ||
    field?.field === "close_breakout_high"
    ? "lookback"
    : field?.type || "number";
}
export function sectorRuleText(
  value: SectorRow,
  fields: SectorRow[],
  defaultPeriod = "D",
): string {
  const children = value?.all || value?.any;
  if (Array.isArray(children))
    return children
      .map((child) => `(${sectorRuleText(child, fields, defaultPeriod)})`)
      .join(value.any ? " 或 " : " 且 ");
  if (!value?.field) return "尚未配置条件";
  const spec = fields.find((field) => field.field === value.field);
  const period = value.period || spec?.fixed_period || defaultPeriod;
  if (value.field === "close_breakout_high")
    return `日K收盘价突破前${value.value ?? "N"}个交易日最高价`;
  const right = value.right_field
    ? sectorFieldLabel(value.right_field, fields)
    : typeof value.value === "boolean"
      ? value.value
        ? "是"
        : "否"
      : String(value.value ?? "待配置");
  return `${sectorPeriodLabel(period)} · ${sectorFieldLabel(value.field, fields)} ${value.op || ""} ${right}`;
}
export function memberQuoteDate(
  value: SectorRow | null | undefined,
): string | null {
  return typeof value?.quote_as_of === "string" &&
    /^\d{4}-\d{2}-\d{2}$/.test(value.quote_as_of)
    ? value.quote_as_of
    : null;
}
export function sectorMemberNavigationRows(rows: SectorRow[]): SectorRow[] {
  // Never forward a board's frozen entry baseline into a member stock chart.
  return rows.map(({ instrument_id, name, code }) => ({
    instrument_id,
    name,
    code,
  }));
}
export function sectorMemberBoard(row: SectorRow): SectorRow {
  const labels: Record<string, string> = {
    main: "主板",
    chinext: "创业板",
    star: "科创板",
    beijing: "北交所",
  };
  const reported = row.board_label || row.board_name;
  const board =
    row.board ??
    Object.keys(labels).find((key) => labels[key] === reported) ??
    "unknown";
  return {
    ...row,
    board,
    board_label: labels[board] ? reported || labels[board] : "板块待核验",
  };
}
export function sectorMembersEndpoint({
  roundId,
  topicCode,
  asOf,
  scope,
}: {
  roundId?: string;
  topicCode?: string;
  asOf: string;
  scope: string;
}): string | null {
  if (
    !/^\d{4}-\d{2}-\d{2}$/.test(asOf) ||
    !["latest", "historical"].includes(scope)
  )
    return null;
  const base = roundId
    ? `/sector-tracking-rounds/${encodeURIComponent(roundId)}/members`
    : topicCode
      ? `/sectors/${encodeURIComponent(topicCode)}/members`
      : null;
  return base
    ? `${base}?${new URLSearchParams({ membership_scope: scope, as_of: asOf })}`
    : null;
}
/** Only unwrap the policy's outer group; nested OR/AND groups keep their meaning. */
export function sectorPolicyTree(
  conditions: unknown,
  logic: string,
): SectorRow {
  return {
    [logic === "ALL" ? "all" : "any"]: Array.isArray(conditions)
      ? conditions
      : [],
  };
}
export function sectorPolicyFromTrees(
  alerts: SectorRow,
  exits: SectorRow,
): SectorRow {
  const unwrap = (tree: SectorRow) =>
    Array.isArray(tree.all)
      ? { logic: "ALL", conditions: tree.all }
      : Array.isArray(tree.any)
        ? { logic: "ANY", conditions: tree.any }
        : { logic: "ALL", conditions: [tree] };
  const alert = unwrap(alerts),
    exit = unwrap(exits);
  return {
    alerts: alert.conditions,
    alert_logic: alert.logic,
    exits: exit.conditions,
    exit_logic: exit.logic,
  };
}

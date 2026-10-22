export type EquityCurveMode = "return" | "equity";
export type EquityCurveRow = {
  date?: unknown;
  equity?: unknown;
  drawdown?: unknown;
};
export type EquityCurvePoint = {
  date: string;
  equity: number | null;
  cumulativeReturn: number | null;
  drawdown: number | null;
  equityIssue: "missing" | "invalid" | null;
};

const finite = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);
export function validInitialCapital(value: unknown): value is number {
  return finite(value) && value > 0;
}

/** Returns are measured against the frozen report's initial capital, never its first row. */
export function equityCurvePoints(
  rows: EquityCurveRow[],
  initialCapital: unknown,
): EquityCurvePoint[] {
  const usableCapital = validInitialCapital(initialCapital);
  return rows.map((row) => {
    const equity = finite(row.equity) && row.equity >= 0 ? row.equity : null;
    const value =
      equity !== null && usableCapital
        ? (equity / initialCapital - 1) * 100
        : null;
    return {
      date: typeof row.date === "string" ? row.date : "—",
      equity,
      equityIssue:
        equity !== null ? null : row.equity == null ? "missing" : "invalid",
      cumulativeReturn: finite(value) ? value : null,
      drawdown:
        finite(row.drawdown) && Math.abs(row.drawdown) <= 100
          ? -Math.abs(row.drawdown)
          : null,
    };
  });
}

const twoDecimals = new Intl.NumberFormat("zh-CN", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
export function curvePercent(value: unknown, signed = false): string {
  return finite(value)
    ? `${signed && value > 0 ? "+" : ""}${twoDecimals.format(Object.is(value, -0) ? 0 : value)}%`
    : "—";
}
export function curveAmount(value: unknown): string {
  return finite(value) ? `${twoDecimals.format(value)} 元` : "—";
}
export function curveAxisAmount(value: number): string {
  if (!finite(value)) return "—";
  if (Math.abs(value) >= 10000) return `${twoDecimals.format(value / 10000)}万`;
  return twoDecimals.format(value);
}
const escapeHtml = (text: string) =>
  text.replace(/[&<>"']/g, (character) => {
    return {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[character]!;
  });

/** Both linked panes use the same dated row, avoiding duplicate dates and raw float tooltips. */
export function equityCurveTooltip(
  parameters: unknown,
  points: EquityCurvePoint[],
  mode: EquityCurveMode,
): string {
  const values = Array.isArray(parameters) ? parameters : [parameters];
  const index = values.find(
    (item) => item && Number.isInteger(item.dataIndex),
  )?.dataIndex;
  if (!Number.isInteger(index) || index < 0 || index >= points.length)
    return "";
  const point = points[index];
  const valuesByMode =
    mode === "return"
      ? [
          ["累计收益率", curvePercent(point.cumulativeReturn, true)],
          ["账户权益", curveAmount(point.equity)],
        ]
      : [
          ["账户权益", curveAmount(point.equity)],
          ["累计收益率", curvePercent(point.cumulativeReturn, true)],
        ];
  return `<div class="equity-tooltip"><strong>${escapeHtml(point.date)}</strong>${[...valuesByMode, ["回撤", curvePercent(point.drawdown)]].map(([label, value]) => `<div><span>${label}</span><b>${value}</b></div>`).join("")}${point.equityIssue === "invalid" ? "<small>权益数值异常，收益率未计算</small>" : ""}</div>`;
}

/** Same decimal ROUND_HALF_UP-to-cent rule as the simulation, for old reports without stats capital. */
export function roundedInitialCapital(value: unknown): number | null {
  if (!validInitialCapital(value)) return null;
  const [mantissa, exponentText = "0"] = value
    .toString()
    .toLowerCase()
    .split("e");
  const pieces = mantissa.split(".");
  const digits = BigInt(pieces.join(""));
  const shift = Number(exponentText) - (pieces[1]?.length || 0) + 2;
  const cents =
    shift >= 0
      ? digits * 10n ** BigInt(shift)
      : (digits + 10n ** BigInt(-shift) / 2n) / 10n ** BigInt(-shift);
  const result = Number(`${cents}e-2`);
  return validInitialCapital(result) ? result : null;
}
export function frozenInitialCapital(
  report: Record<string, any> | null | undefined,
): {
  value: number | null;
  source: "stats" | "config" | null;
  configMismatch: boolean;
} {
  const configured = roundedInitialCapital(report?.config?.initial_capital);
  if (report?.stats && Object.hasOwn(report.stats, "initial_capital")) {
    const value = report.stats.initial_capital;
    return {
      value: validInitialCapital(value) ? value : null,
      source: "stats",
      configMismatch:
        validInitialCapital(value) &&
        configured !== null &&
        value !== configured,
    };
  }
  return {
    value: configured,
    source: configured === null ? null : "config",
    configMismatch: false,
  };
}

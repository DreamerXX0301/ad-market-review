import type { Row } from "../lib/api";

export type WaveMode = "latest" | "up" | "down" | "turning";
export type WavePoint = {
  date: string;
  price: number;
  kind: "high" | "low";
  confirmed_at: string;
  label: string;
  phase?: "impulse" | "correction";
  segment_id?: string;
};
export type WavePattern = {
  pattern_id: string;
  direction: "up" | "down";
  label: string;
  start_date: string;
  end_date: string;
  confirmed_at: string;
  has_correction: boolean;
  points: WavePoint[];
  rules: string[];
  scope: string;
};

function validDate(value: unknown): value is string {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value))
    return false;
  const date = new Date(value + "T00:00:00Z");
  return (
    Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value
  );
}

function validPoint(
  point: unknown,
  dates: Set<string>,
  asOf: string,
): point is WavePoint {
  if (!point || typeof point !== "object") return false;
  const value = point as Row;
  return (
    validDate(value.date) &&
    dates.has(value.date) &&
    validDate(value.confirmed_at) &&
    value.date <= value.confirmed_at &&
    value.confirmed_at <= asOf &&
    typeof value.price === "number" &&
    Number.isFinite(value.price) &&
    value.price > 0 &&
    ["high", "low"].includes(value.kind)
  );
}

/** Accept only one complete backend-labelled skeleton; never number generic swings. */
export function wavePatterns(
  input: unknown,
  barDates: string[],
  asOf: string,
): WavePattern[] {
  if (!input || typeof input !== "object" || !validDate(asOf)) return [];
  const source = (input as Row).patterns;
  if (!Array.isArray(source)) return [];
  const dates = new Set(barDates),
    found = new Map<string, WavePattern>();
  for (const raw of source) {
    if (
      !raw ||
      typeof raw !== "object" ||
      typeof raw.pattern_id !== "string" ||
      !raw.pattern_id ||
      !["up", "down"].includes(raw.direction) ||
      !Array.isArray(raw.points) ||
      ![6, 9].includes(raw.points.length) ||
      !validDate(raw.confirmed_at) ||
      raw.confirmed_at > asOf
    )
      continue;
    const labels =
      raw.points.length === 9
        ? ["0", "1?", "2?", "3?", "4?", "5?", "A?", "B?", "C?"]
        : ["0", "1?", "2?", "3?", "4?", "5?"];
    if (
      raw.has_correction !== (raw.points.length === 9) ||
      new Set(raw.points.map((point: WavePoint) => point?.segment_id)).size !==
        1 ||
      !raw.points.every(
        (point: unknown, index: number) =>
          validPoint(point, dates, asOf) &&
          point.label === labels[index] &&
          point.phase === (index < 6 ? "impulse" : "correction") &&
          point.confirmed_at <= raw.confirmed_at &&
          (index === 0 || point.date > raw.points[index - 1].date),
      ) ||
      raw.start_date !== raw.points[0].date ||
      raw.end_date !== raw.points.at(-1).date
    )
      continue;
    found.set(raw.pattern_id, {
      pattern_id: raw.pattern_id,
      direction: raw.direction,
      label: raw.direction === "up" ? "上涨五浪候选" : "下跌五浪候选",
      start_date: raw.start_date,
      end_date: raw.end_date,
      confirmed_at: raw.confirmed_at,
      has_correction: raw.has_correction,
      points: raw.points.map((point: WavePoint) => ({ ...point })),
      rules: Array.isArray(raw.rules)
        ? raw.rules.filter(
            (rule: unknown): rule is string => typeof rule === "string",
          )
        : [],
      scope:
        typeof raw.scope === "string"
          ? raw.scope
          : "仅检验转折价格骨架，不是完整艾略特认定",
    });
  }
  return [...found.values()].sort(
    (left, right) =>
      right.confirmed_at.localeCompare(left.confirmed_at) ||
      right.end_date.localeCompare(left.end_date) ||
      left.pattern_id.localeCompare(right.pattern_id),
  );
}

export function waveCandidates(
  patterns: WavePattern[],
  mode: WaveMode,
): WavePattern[] {
  return mode === "turning"
    ? []
    : patterns.filter(
        (pattern) => mode === "latest" || pattern.direction === mode,
      );
}

export function selectedWavePatterns(
  candidates: WavePattern[],
  key: string,
  mode: WaveMode,
): WavePattern[] {
  if (mode === "turning") return [];
  if (key === "all" && mode !== "latest") return candidates;
  const specific = candidates.find((pattern) => pattern.pattern_id === key);
  return specific ? [specific] : candidates.slice(0, 1);
}

/** Explicit navigation only; callers must not apply this on load or selection. */
export function wavePatternWindow(
  pattern: WavePattern,
  barDates: string[],
): { start: number; end: number } | null {
  const first = barDates.indexOf(pattern.start_date),
    last = barDates.indexOf(pattern.end_date);
  if (first < 0 || last < first || barDates.length < 2) return null;
  const padding = Math.max(2, Math.ceil((last - first + 1) * 0.08));
  return {
    start: (Math.max(0, first - padding) / (barDates.length - 1)) * 100,
    end:
      (Math.min(barDates.length - 1, last + padding) / (barDates.length - 1)) *
      100,
  };
}

export function waveTurningPoints(
  input: unknown,
  barDates: string[],
  asOf: string,
): WavePoint[] {
  if (!input || typeof input !== "object" || !validDate(asOf)) return [];
  const source = (input as Row).points;
  if (!Array.isArray(source)) return [];
  const dates = new Set(barDates);
  return source
    .filter((point: unknown): point is WavePoint =>
      validPoint(point, dates, asOf),
    )
    .map((point) => ({
      ...point,
      label: point.kind === "high" ? "高点" : "低点",
    }));
}

/** Each structure owns its own impulse/correction series; nothing bridges structures. */
export function wavePlotSeries(
  patterns: WavePattern[],
  turns: WavePoint[],
  mode: WaveMode,
  palette: { up: string; down: string; correction: string; turning: string },
): Row[] {
  function line(
    id: string,
    name: string,
    points: WavePoint[],
    color: string,
    dashed: boolean,
    hideAnchor = false,
  ) {
    return {
      id,
      name,
      type: "line",
      xAxisIndex: 0,
      yAxisIndex: 0,
      data: points.map((point, index) => ({
        value: [point.date, point.price],
        name: point.label,
        label: {
          position: point.kind === "high" ? "top" : "bottom",
          ...(hideAnchor && index === 0 ? { show: false } : {}),
        },
      })),
      symbol: "circle",
      symbolSize: 5,
      connectNulls: false,
      lineStyle: { color, width: 1.7, type: dashed ? "dashed" : "solid" },
      itemStyle: { color },
      label: {
        show: true,
        color,
        fontSize: 10,
        formatter: (point: Row) => point.name,
      },
      labelLayout: { hideOverlap: true },
      tooltip: { show: false },
      clip: false,
      z: 6,
    };
  }
  if (mode === "turning") {
    const runs: WavePoint[][] = [];
    for (const point of turns) {
      const run = runs.at(-1);
      if (run && run[0].segment_id === point.segment_id) run.push(point);
      else runs.push([point]);
    }
    return runs.map((run, index) =>
      line(
        `wave-turning-points-${index}`,
        "高低点转折候选",
        run,
        palette.turning,
        true,
      ),
    );
  }
  return patterns.flatMap((pattern) => {
    const arrow = pattern.direction === "up" ? "↑" : "↓";
    const result = [
      line(
        `wave-${pattern.pattern_id}-impulse`,
        `${arrow} ${pattern.label} ${pattern.start_date}`,
        pattern.points.slice(0, 6),
        pattern.direction === "up" ? palette.up : palette.down,
        false,
      ),
    ];
    if (pattern.has_correction)
      result.push(
        line(
          `wave-${pattern.pattern_id}-correction`,
          `${arrow} ${pattern.label}后的ABC候选 ${pattern.start_date}`,
          pattern.points.slice(5),
          palette.correction,
          true,
          true,
        ),
      );
    return result;
  });
}

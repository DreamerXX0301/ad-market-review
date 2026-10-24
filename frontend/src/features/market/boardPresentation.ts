import type { Row } from "../../lib/api";
import { ladderPatternTags } from "./ladderPresentation";

export type BoardTag = {
  key: string;
  label: string;
  dimension: "height" | "type" | "feedback" | "volume";
  tone: string;
  detail: string;
};
export type BoardTagOptions = {
  height?: boolean;
  previousHeight?: boolean;
  types?: boolean;
  feedback?: boolean;
  volume?: boolean;
};
const version = "dated-stock-board-presentation-v1";
const types = new Set([
  "earth_to_sky",
  "one_price_limit_up",
  "one_price_limit_down",
  "limit_down",
  "non_one_price_limit_up",
  "open_count",
  "repeated_break",
  "pullback_reclaim",
  "broken_pool_source",
]);
const feedbackLabels: Record<string, string> = {
  promoted: "晋级",
  limit_down: "跌停反馈",
  large_loss: "大幅回落",
  negative: "回落",
  positive: "正反馈",
  flat: "平盘",
};
const volumeLabels: Record<string, string> = {
  expanded: "放量",
  contracted: "缩量",
  flat: "平量",
};
const isoDay = (value: unknown): value is string => {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value))
    return false;
  const parsed = new Date(value + "T00:00:00Z");
  return (
    Number.isFinite(parsed.getTime()) &&
    parsed.toISOString().slice(0, 10) === value
  );
};
const positive = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value) && value > 0;
const text = (value: unknown): value is string =>
  typeof value === "string" && !!value.trim();
const owns = (object: Record<string, string>, key: unknown): key is string =>
  typeof key === "string" && Object.prototype.hasOwnProperty.call(object, key);

function boundBoardFacts(
  row: Row,
  date: string,
  snapshotId: unknown,
): Row | null {
  const facts = row.board_presentation;
  if (
    !/^\d{6}\.(SH|SZ|BJ)$/.test(String(row.instrument_id || "")) ||
    !isoDay(date) ||
    typeof snapshotId !== "string" ||
    !snapshotId ||
    !facts ||
    typeof facts !== "object" ||
    Array.isArray(facts) ||
    facts.version !== version ||
    facts.date !== date ||
    facts.snapshot_id !== snapshotId ||
    facts.instrument_id !== row.instrument_id ||
    (row.date != null && row.date !== date) ||
    (row.evidence_date != null && row.evidence_date !== date) ||
    (row.snapshot_id != null && row.snapshot_id !== snapshotId)
  )
    return null;
  return facts;
}

/** The table and height summary use the same dated fact that the tag displays. */
export function previousBoardHeight(
  row: Row,
  date: string,
  snapshotId: unknown,
): number | null {
  if (!Object.prototype.hasOwnProperty.call(row, "board_presentation")) {
    const raw = row.previous_board_count;
    return positive(raw) && Number.isInteger(raw) ? raw : null;
  }
  const value = boundBoardFacts(row, date, snapshotId)?.previous_height;
  return value?.state === "confirmed" &&
    positive(value.value) &&
    Number.isInteger(value.value) &&
    text(value.label) &&
    isoDay(value.source_date) &&
    value.source_date < date &&
    value.source_date === row.previous_board_date
    ? value.value
    : null;
}

/** Height, day type, next-day feedback and volume remain independent dated facts. */
export function boardTags(
  row: Row,
  date: string,
  snapshotId: unknown,
  options: BoardTagOptions = {},
): BoardTag[] {
  const flags = {
    height: true,
    previousHeight: false,
    types: true,
    feedback: false,
    volume: true,
    ...options,
  };
  if (!/^\d{6}\.(SH|SZ|BJ)$/.test(String(row.instrument_id || ""))) return [];
  const facts = row.board_presentation;
  // Older payloads may retain the chart's already verified three shape events.
  // A present but invalid new object never falls back across a generation/date.
  if (!Object.prototype.hasOwnProperty.call(row, "board_presentation")) {
    const tags: BoardTag[] = flags.types
      ? ladderPatternTags(row, date, snapshotId).map((tag) => ({
          key: "type:" + tag.kind,
          dimension: "type" as const,
          label: tag.label,
          tone: tag.kind.includes("down") ? "down" : "up",
          detail: tag.detail,
        }))
      : [];
    const previous = previousBoardHeight(row, date, snapshotId);
    if (flags.previousHeight && previous !== null)
      tags.unshift({
        key: "previous_height",
        dimension: "height",
        tone: "height",
        label: `昨${previous}板`,
        detail: "旧版载荷留存的前日连板高度",
      });
    return tags;
  }
  if (!boundBoardFacts(row, date, snapshotId)) return [];
  const tags: BoardTag[] = [];
  for (const [key, enabled] of [
    ["height", flags.height],
    ["previous_height", flags.previousHeight],
  ] as const) {
    const value = facts[key];
    if (
      !enabled ||
      value?.state !== "confirmed" ||
      !positive(value.value) ||
      !Number.isInteger(value.value) ||
      !text(value.label) ||
      !isoDay(value.source_date) ||
      (key === "height"
        ? value.source_date !== date
        : previousBoardHeight(row, date, snapshotId) === null)
    )
      continue;
    tags.push({
      key,
      dimension: "height",
      tone: "height",
      label: (key === "previous_height" ? "昨" : "") + value.label,
      detail: `${value.source_date} · ${key === "previous_height" ? "前一交易日高度" : "当日高度"}：${value.reason || "有据来源高度"}`,
    });
  }
  if (flags.types)
    for (const item of Array.isArray(facts.types) ? facts.types : []) {
      if (
        !item ||
        !types.has(item.code) ||
        item.state !== "confirmed" ||
        item.source_date !== date ||
        !text(item.label)
      )
        continue;
      if (tags.some((tag) => tag.key === "type:" + item.code)) continue;
      tags.push({
        key: "type:" + item.code,
        dimension: "type",
        label: item.label,
        tone: ["limit_down", "one_price_limit_down"].includes(item.code)
          ? "down"
          : [
                "one_price_limit_up",
                "non_one_price_limit_up",
                "earth_to_sky",
              ].includes(item.code)
            ? "up"
            : "neutral",
        detail: `${date} · 当日板型：${item.reason || item.label}`,
      });
    }
  const feedback = facts.feedback;
  if (
    flags.feedback &&
    feedback?.state === "confirmed" &&
    feedback.source_date === date &&
    isoDay(feedback.cohort_date) &&
    feedback.cohort_date < date &&
    (row.previous_board_date == null ||
      row.previous_board_date === feedback.cohort_date) &&
    owns(feedbackLabels, feedback.code)
  )
    tags.push({
      key: "feedback:" + feedback.code,
      dimension: "feedback",
      label: feedbackLabels[feedback.code],
      tone: ["promoted", "positive"].includes(feedback.code)
        ? "up"
        : ["limit_down", "large_loss", "negative"].includes(feedback.code)
          ? "down"
          : "neutral",
      detail: `${feedback.cohort_date}样本 → ${date}反馈：${feedback.reason || "已核验的前日样本今日反馈"}`,
    });
  const volume = facts.volume;
  if (
    flags.volume &&
    owns(volumeLabels, volume?.state) &&
    positive(volume.ratio) &&
    isoDay(volume.previous_date) &&
    volume.previous_date < date &&
    (volume.state === "expanded"
      ? volume.ratio > 1
      : volume.state === "contracted"
        ? volume.ratio < 1
        : volume.ratio === 1)
  )
    tags.push({
      key: "volume:" + volume.state,
      dimension: "volume",
      tone: "volume",
      label: volumeLabels[volume.state],
      detail: `${date} / ${volume.previous_date} 成交量 ${volume.ratio.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}倍；${volume.reason || "严格相邻交易日的可比正成交量"}`,
    });
  return tags;
}

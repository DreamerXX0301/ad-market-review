export type NoonTextTone = "up" | "down" | "neutral";
export type NoonTextPart = { text: string; tone: NoonTextTone };

// Color only explicit direction. Unsigned percentages may be thresholds or
// coverage, so they must not be interpreted as an increase.
const count = String.raw`(?<direction>上涨|下跌|平盘)\s*(?:\d{1,3}(?:,\d{3})+|\d+)(?:\s*(?:只|家)|(?=[\s/，、。；;·|）)]|,(?!\d)|$))`;
const percentage = String.raw`(?<![\d.,])(?<percentage>[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%)`;

/** Plain-text parts only: preserves saved prose and never emits HTML markup. */
export function noonTextParts(text: string): NoonTextPart[] {
  const pattern = new RegExp(`${count}|${percentage}`, "gu");
  const parts: NoonTextPart[] = [];
  const append = (value: string, tone: NoonTextTone) => {
    if (!value) return;
    const last = parts.at(-1);
    if (last?.tone === tone) last.text += value;
    else parts.push({ text: value, tone });
  };
  let cursor = 0;
  for (const match of text.matchAll(pattern)) {
    const start = match.index;
    append(text.slice(cursor, start), "neutral");
    const direction = match.groups?.direction;
    const percent = match.groups?.percentage;
    let tone: NoonTextTone = "neutral";
    if (direction === "上涨") tone = "up";
    else if (direction === "下跌") tone = "down";
    else if (percent && /^[+-]/.test(percent)) {
      const value = Number(percent.slice(0, -1).replaceAll(",", ""));
      if (Number.isFinite(value) && value !== 0)
        tone = value > 0 ? "up" : "down";
    }
    append(match[0], tone);
    cursor = start + match[0].length;
  }
  append(text.slice(cursor), "neutral");
  return parts;
}

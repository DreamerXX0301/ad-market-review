/** Calendar-only helpers: dates are strict ISO strings, never timezone shifted. */
export function isCalendarDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const parsed = new Date(value + "T00:00:00Z");
  return (
    Number.isFinite(parsed.getTime()) &&
    parsed.toISOString().slice(0, 10) === value
  );
}

export function retainedMarketDates(values: string[]): string[] {
  return [...new Set(values.filter(isCalendarDate))].sort();
}

export function availableMonths(dates: string[]): string[] {
  return [...new Set(dates.map((day) => day.slice(0, 7)))].sort();
}

export function adjacentMarketDate(
  dates: string[],
  value: string,
  direction: -1 | 1,
): string | null {
  const index = dates.indexOf(value);
  return index >= 0 ? dates[index + direction] || null : null;
}

export function adjacentMonth(
  months: string[],
  month: string,
  direction: -1 | 1,
): string | null {
  const index = months.indexOf(month);
  return index >= 0 ? months[index + direction] || null : null;
}

export function monthForYear(
  months: string[],
  year: string,
  preferredMonth: string,
): string | null {
  const choices = months.filter((month) => month.slice(0, 4) === year);
  choices.sort(
    (left, right) =>
      Math.abs(Number(left.slice(5)) - Number(preferredMonth.slice(5))) -
        Math.abs(Number(right.slice(5)) - Number(preferredMonth.slice(5))) ||
      left.localeCompare(right),
  );
  return choices[0] || null;
}

export function calendarMonthCells(
  month: string,
): { date: string; day: number; inMonth: boolean }[] {
  if (!/^\d{4}-\d{2}$/.test(month) || !isCalendarDate(month + "-01")) return [];
  const first = new Date(month + "-01T00:00:00Z");
  const mondayOffset = (first.getUTCDay() + 6) % 7;
  return Array.from({ length: 42 }, (_, index) => {
    const current = new Date(
      first.getTime() + (index - mondayOffset) * 86400000,
    );
    const date = current.toISOString().slice(0, 10);
    return {
      date,
      day: current.getUTCDate(),
      inMonth: date.slice(0, 7) === month,
    };
  });
}

export function validateMarketDateJump(
  value: string,
  dates: string[],
): { date: string | null; error: string } {
  const candidate = value.trim();
  if (!isCalendarDate(candidate))
    return { date: null, error: "请输入有效日期，格式为 YYYY-MM-DD。" };
  if (!dates.length)
    return { date: null, error: "暂无已留存交易日，请先完成行情初始化。" };
  if (candidate < dates[0] || candidate > dates[dates.length - 1])
    return {
      date: null,
      error:
        "仅可查看已留存区间：" +
        dates[0] +
        " 至 " +
        dates[dates.length - 1] +
        "。",
    };
  if (!dates.includes(candidate))
    return {
      date: null,
      error: "该日没有已留存的完成交易日行情，请选择日历中可选的日期。",
    };
  return { date: candidate, error: "" };
}

export type DatePickerModel = {
  min: string;
  max: string;
  allowedDates: string[] | null;
  months: string[];
  initialDate: string;
};

/** Only explicitly provided allowedDates constrain the picker to trading days. */
export function datePickerModel({
  value,
  min,
  max,
  allowedDates,
  fallbackDate,
}: {
  value: string;
  min?: string;
  max?: string;
  allowedDates?: string[];
  fallbackDate?: string;
}): DatePickerModel {
  const selected = isCalendarDate(value) ? value : "";
  const lower =
    min && isCalendarDate(min)
      ? min
      : `${Math.min(1900, Number(selected.slice(0, 4)) || 1900)}-01-01`;
  const upper =
    max && isCalendarDate(max)
      ? max
      : `${Math.max(2100, Number(selected.slice(0, 4)) || 2100)}-12-31`;
  const allowed =
    allowedDates === undefined
      ? null
      : retainedMarketDates(allowedDates).filter(
          (day) => day >= lower && day <= upper,
        );
  const first = allowed ? allowed[0] || lower : lower,
    last = allowed ? allowed.at(-1) || upper : upper;
  const months: string[] = [];
  if (first <= last && (!allowed || allowed.length)) {
    if (allowed) months.push(...availableMonths(allowed));
    else {
      let year = Number(first.slice(0, 4)),
        month = Number(first.slice(5, 7));
      while (
        `${String(year).padStart(4, "0")}-${String(month).padStart(2, "0")}` <=
        last.slice(0, 7)
      ) {
        months.push(
          `${String(year).padStart(4, "0")}-${String(month).padStart(2, "0")}`,
        );
        month += 1;
        if (month === 13) {
          year += 1;
          month = 1;
        }
      }
    }
  }
  const preferred =
    selected ||
    allowed?.at(-1) ||
    (max && isCalendarDate(max)
      ? max
      : fallbackDate && isCalendarDate(fallbackDate)
        ? fallbackDate
        : first);
  const bounded =
    preferred < first ? first : preferred > last ? last : preferred;
  const initialDate = allowed
    ? allowed.includes(bounded)
      ? bounded
      : allowed.find((day) => day >= bounded) || allowed.at(-1) || ""
    : bounded;
  return { min: first, max: last, allowedDates: allowed, months, initialDate };
}

export function datePickerSelectable(value: string, model: DatePickerModel) {
  return (
    isCalendarDate(value) &&
    value >= model.min &&
    value <= model.max &&
    (model.allowedDates === null || model.allowedDates.includes(value))
  );
}

export function validateDatePickerChoice(
  value: string,
  model: DatePickerModel,
): { date: string | null; error: string } {
  const date = value.trim();
  if (!isCalendarDate(date))
    return { date: null, error: "请输入有效日期，格式为 YYYY-MM-DD。" };
  if (!model.months.length)
    return { date: null, error: "当前没有可选择的日期。" };
  if (date < model.min || date > model.max)
    return { date: null, error: `可选范围为 ${model.min} 至 ${model.max}。` };
  if (!datePickerSelectable(date, model))
    return {
      date: null,
      error: "该日不在已提供的可选日期中，请选择日历中可用的日期。",
    };
  return { date, error: "" };
}

/** Roving focus uses UTC date-only arithmetic and never invents trading days. */
export function moveDatePickerFocus(
  value: string,
  key: string,
  model: DatePickerModel,
  shiftKey = false,
): string | null {
  if (!isCalendarDate(value) || !model.months.length) return null;
  const source = new Date(value + "T00:00:00Z");
  let direction: -1 | 1 = 1;
  if (key === "ArrowLeft") {
    source.setUTCDate(source.getUTCDate() - 1);
    direction = -1;
  } else if (key === "ArrowRight") source.setUTCDate(source.getUTCDate() + 1);
  else if (key === "ArrowUp") {
    source.setUTCDate(source.getUTCDate() - 7);
    direction = -1;
  } else if (key === "ArrowDown") source.setUTCDate(source.getUTCDate() + 7);
  else if (key === "Home")
    source.setUTCDate(source.getUTCDate() - ((source.getUTCDay() + 6) % 7));
  else if (key === "End") {
    source.setUTCDate(source.getUTCDate() + 6 - ((source.getUTCDay() + 6) % 7));
    direction = -1;
  } else if (key === "PageUp" || key === "PageDown") {
    direction = key === "PageUp" ? -1 : 1;
    const day = source.getUTCDate();
    source.setUTCDate(1);
    source.setUTCMonth(source.getUTCMonth() + direction * (shiftKey ? 12 : 1));
    const last = new Date(source.getTime());
    last.setUTCMonth(last.getUTCMonth() + 1);
    last.setUTCDate(0);
    source.setUTCDate(Math.min(day, last.getUTCDate()));
  } else return null;
  const target = source.toISOString().slice(0, 10),
    bounded =
      target < model.min ? model.min : target > model.max ? model.max : target;
  if (model.allowedDates)
    return direction === 1
      ? model.allowedDates.find((day) => day >= bounded) ||
          model.allowedDates.at(-1) ||
          null
      : [...model.allowedDates].reverse().find((day) => day <= bounded) ||
          model.allowedDates[0] ||
          null;
  return bounded;
}

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

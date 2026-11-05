type Selection = {
  slot: string;
  snapshotId?: string;
  closingDate?: string;
};

const validDay = (value: string) => {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const parsed = new Date(value + "T00:00:00Z");
  return (
    Number.isFinite(parsed.getTime()) &&
    parsed.toISOString().slice(0, 10) === value
  );
};

/** Tables retain the selected observation; charts prefer its published close. */
export function intradayChartSelection(
  day: string,
  published: string[],
  previousClose: string | null,
  selection?: Selection,
) {
  if (!validDay(day)) return null;
  const closed =
    published.includes(day) ||
    selection?.closingDate === day ||
    selection?.slot === "close";
  if (closed) return { asOf: day };
  if (!previousClose || !validDay(previousClose) || previousClose >= day)
    return null;
  const snapshot = selection?.snapshotId;
  return {
    asOf: previousClose,
    ...(selection?.slot === "noon" && snapshot
      ? { noonSnapshotId: snapshot }
      : {}),
    ...(snapshot?.startsWith("intraday_")
      ? { intradaySnapshotId: snapshot }
      : {}),
  };
}

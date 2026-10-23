export type IndexPriceProfileOptions = {
  asOf: string;
  lookback: number;
  bins?: number;
};

export type IndexPriceProfileRow = {
  index: number;
  low: number;
  high: number;
  price: number;
  count: number;
};

export type IndexPriceProfile = {
  state: "ready" | "partial" | "unavailable";
  reason: string;
  asOf: string;
  requested: number;
  observed: number;
  startDate: string | null;
  endDate: string | null;
  rows: IndexPriceProfileRow[];
  center: number | null;
  centerIndex: number | null;
  val: number | null;
  vah: number | null;
  valueAreaStartIndex: number | null;
  valueAreaEndIndex: number | null;
  totalVisits: number;
  valueAreaVisits: number;
  valueAreaFraction: number;
  latestClose: number | null;
};

type DailyBar = {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
};

export const INDEX_PROFILE_VALUE_AREA_FRACTION = 0.7;
export const INDEX_PROFILE_DEFAULT_BINS = 32;

const finite = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);

const validDate = (value: unknown): value is string => {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value))
    return false;
  const parsed = new Date(value + "T00:00:00Z");
  return (
    Number.isFinite(parsed.getTime()) &&
    parsed.toISOString().slice(0, 10) === value
  );
};

/**
 * Chart-only, cross-day price-range distribution on real returned daily OHLC.
 *
 * A day contributes at most one visit to every bucket its high-low range
 * overlaps. This is a range approximation: daily OHLC does not prove that
 * every intermediate price traded, nor supply intraday TPO or price volume.
 * No volume, amount, indicator or trading signal enters this calculation.
 *
 * Buckets are [low, high), with the final bucket also containing the overall
 * maximum. A non-flat range needs positive-length overlap; touching only an
 * edge does not add a second visit. A flat day contributes to one point bucket.
 * Missing market sessions cannot be established from these rows alone.
 */
export function indexPriceProfile(
  source: readonly unknown[],
  options: IndexPriceProfileOptions,
): IndexPriceProfile {
  const result: IndexPriceProfile = {
    state: "unavailable",
    reason: "",
    asOf: typeof options?.asOf === "string" ? options.asOf : "",
    requested: finite(options?.lookback) ? options.lookback : 0,
    observed: 0,
    startDate: null,
    endDate: null,
    rows: [],
    center: null,
    centerIndex: null,
    val: null,
    vah: null,
    valueAreaStartIndex: null,
    valueAreaEndIndex: null,
    totalVisits: 0,
    valueAreaVisits: 0,
    valueAreaFraction: INDEX_PROFILE_VALUE_AREA_FRACTION,
    latestClose: null,
  };
  const unavailable = (reason: string): IndexPriceProfile => ({
    ...result,
    reason,
  });
  if (!validDate(options?.asOf))
    return unavailable("观察日期无效，分布保持未知");
  if (
    !Number.isInteger(options.lookback) ||
    options.lookback < 1 ||
    options.lookback > 750
  )
    return unavailable("报价日窗口须为1至750之间的整数");
  const bins = options.bins ?? INDEX_PROFILE_DEFAULT_BINS;
  if (!Number.isInteger(bins) || bins < 1 || bins > 128)
    return unavailable("价格档数须为1至128之间的整数");
  if (!Array.isArray(source)) return unavailable("未取得可核验的日K报价");

  const dated: Record<string, unknown>[] = [];
  const seen = new Set<string>();
  for (const item of source) {
    if (!item || typeof item !== "object" || Array.isArray(item))
      return unavailable("日K记录或日期无效，分布保持未知");
    const row = item as Record<string, unknown>;
    if (!validDate(row.date))
      return unavailable("日K日期无效，无法确认观察日范围");
    if (row.date > options.asOf) continue;
    if (seen.has(row.date))
      return unavailable(`日K日期重复（${row.date}），分布保持未知`);
    seen.add(row.date);
    dated.push(row);
  }
  dated.sort((left, right) =>
    (left.date as string).localeCompare(right.date as string),
  );
  const window = dated.slice(-options.lookback);
  result.observed = window.length;
  result.startDate = (window[0]?.date as string | undefined) ?? null;
  result.endDate = (window.at(-1)?.date as string | undefined) ?? null;
  if (!window.length) return unavailable("观察日及之前没有可用的日报价");

  for (const row of window) {
    if (
      ![row.open, row.high, row.low, row.close].every(
        (price) => finite(price) && price > 0,
      ) ||
      (row.low as number) > Math.min(row.open as number, row.close as number) ||
      (row.high as number) <
        Math.max(row.open as number, row.close as number) ||
      (row.low as number) > (row.high as number)
    )
      return unavailable(`日K价格无效（${row.date}），分布保持未知`);
  }
  const bars = window as DailyBar[];
  const low = Math.min(...bars.map((bar) => bar.low));
  const high = Math.max(...bars.map((bar) => bar.high));
  const span = high - low;
  if (!finite(span)) return unavailable("价格范围超出可计算范围");

  const rows: IndexPriceProfileRow[] = [];
  if (span === 0) {
    rows.push({ index: 0, low, high, price: low, count: bars.length });
  } else {
    const boundaries = Array.from({ length: bins + 1 }, (_, index) =>
      index === bins ? high : low + span * (index / bins),
    );
    if (
      boundaries.some(
        (value, index) => index > 0 && value <= boundaries[index - 1],
      )
    )
      return unavailable("价格精度不足以划分当前档数，分布保持未知");
    for (let index = 0; index < bins; index++) {
      const bucketLow = boundaries[index];
      const bucketHigh = boundaries[index + 1];
      const count = bars.reduce((visits, bar) => {
        const overlap =
          bar.low === bar.high
            ? bar.low >= bucketLow &&
              (bar.low < bucketHigh || (index === bins - 1 && bar.low === high))
            : bar.high > bucketLow && bar.low < bucketHigh;
        return visits + Number(overlap);
      }, 0);
      rows.push({
        index,
        low: bucketLow,
        high: bucketHigh,
        price: bucketLow + (bucketHigh - bucketLow) / 2,
        count,
      });
    }
  }

  const totalVisits = rows.reduce((sum, row) => sum + row.count, 0);
  if (totalVisits === 0)
    return unavailable("有效价格档访问次数为零，分布保持未知");
  // POC ties: choose closest to the whole price range's midpoint, then lower.
  // Index distance avoids floating point asymmetry between equal-width bins.
  let centerIndex = 0;
  const midpointIndex = (rows.length - 1) / 2;
  for (let index = 1; index < rows.length; index++) {
    if (
      rows[index].count > rows[centerIndex].count ||
      (rows[index].count === rows[centerIndex].count &&
        Math.abs(index - midpointIndex) < Math.abs(centerIndex - midpointIndex))
    )
      centerIndex = index;
  }

  // Expand a continuous area from POC, taking the more visited adjacent bucket.
  // Adjacent ties choose the lower-price side. Stop once >= 70% of visits fit.
  let lowerIndex = centerIndex;
  let upperIndex = centerIndex;
  let valueAreaVisits = rows[centerIndex].count;
  const target = totalVisits * INDEX_PROFILE_VALUE_AREA_FRACTION;
  while (valueAreaVisits < target) {
    const below = lowerIndex > 0 ? rows[lowerIndex - 1].count : -1;
    const above =
      upperIndex < rows.length - 1 ? rows[upperIndex + 1].count : -1;
    if (below >= above) {
      lowerIndex--;
      valueAreaVisits += rows[lowerIndex].count;
    } else {
      upperIndex++;
      valueAreaVisits += rows[upperIndex].count;
    }
  }

  return {
    ...result,
    state: bars.length < options.lookback ? "partial" : "ready",
    reason:
      bars.length < options.lookback
        ? `请求近${options.lookback}个报价日，实际仅取得${bars.length}个；按现有日报价计算，不代表完整交易日覆盖`
        : "按实际日报价的高低区间近似统计，不代表完整交易日覆盖或盘中停留时间",
    rows,
    center: rows[centerIndex].price,
    centerIndex,
    val: rows[lowerIndex].low,
    vah: rows[upperIndex].high,
    valueAreaStartIndex: lowerIndex,
    valueAreaEndIndex: upperIndex,
    totalVisits,
    valueAreaVisits,
    latestClose: bars.at(-1)!.close,
  };
}

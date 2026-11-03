import { useRef, useState, type ChangeEvent } from "react";
import "./StockChipDistribution.css";

const maxFileBytes = 1024 * 1024;
const maxRows = 6000;
const maxPriceLevels = 500;
const fields = ["ts_code", "trade_date", "price", "percent"];

type PriceLevel = { price: number; percent: number };

function csvCells(line: string) {
  return line
    .split(",")
    .map((cell) => cell.trim().replace(/^"([^"]*)"$/, "$1"));
}

export function parseStockChipCsv(
  text: string,
  instrumentId: string,
  date: string,
): PriceLevel[] {
  if (
    !/^\d{6}\.(SH|SZ|BJ)$/.test(instrumentId) ||
    !/^\d{4}-\d{2}-\d{2}$/.test(date) ||
    !Number.isFinite(Date.parse(date)) ||
    new Date(date).toISOString().slice(0, 10) !== date
  )
    throw new Error("需要明确的股票代码和行情日。");
  const [header, ...lines] = text
    .replace(/^\uFEFF/, "")
    .split(/\r?\n/)
    .filter((line) => line.trim());
  if (!header || lines.length === 0 || lines.length > maxRows)
    throw new Error("CSV 需包含 1 至 6000 行价档数据。");
  const columns = csvCells(header);
  // A pandas export may include its unnamed row index before the data columns.
  const names = columns[0] === "" ? columns.slice(1) : columns;
  if (
    names.length !== fields.length ||
    fields.some((key) => !names.includes(key))
  )
    throw new Error("CSV 须包含 ts_code、trade_date、price、percent 四列。");
  const [symbolColumn, dateColumn, priceColumn, percentColumn] = fields.map(
    (key) => columns.indexOf(key),
  );
  const sourceDate = date.replaceAll("-", "");
  const levels = new Map<number, number>();
  let sum = 0;
  for (const line of lines) {
    const cells = csvCells(line);
    if (cells.length !== columns.length)
      throw new Error("CSV 有行未包含完整价档字段。");
    if (
      cells[symbolColumn] !== instrumentId ||
      ![sourceDate, date].includes(cells[dateColumn])
    )
      continue;
    const [priceText, percentText] = [cells[priceColumn], cells[percentColumn]];
    const price = Number(priceText);
    const percent = Number(percentText);
    if (
      ![priceText, percentText].every((value) =>
        /^\d+(?:\.\d+)?$/.test(value),
      ) ||
      !Number.isFinite(price) ||
      price <= 0 ||
      !Number.isFinite(percent) ||
      percent > 100 ||
      levels.has(price)
    )
      throw new Error("对应日期存在无效价格、占比或重复价档。");
    levels.set(price, percent);
    sum += percent;
  }
  if (!levels.size)
    throw new Error("CSV 中找不到该股票与行情日同时匹配的记录。");
  if (levels.size > maxPriceLevels)
    throw new Error("对应日期价档超过 500 档，请核对来源文件。");
  // Percentages may be independently rounded to two decimal places.
  if (sum <= 0 || sum > 100 + levels.size * 0.005 + 1e-8)
    throw new Error("对应日期的占比合计异常，请核对原始导出。");
  return Array.from(levels, ([price, percent]) => ({ price, percent })).sort(
    (a, b) => b.price - a.price,
  );
}

function ChipViewer({
  instrumentId,
  date,
}: {
  instrumentId: string;
  date: string;
}) {
  const [levels, setLevels] = useState<PriceLevel[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const request = useRef(0);

  async function importFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = "";
    if (!file) return;
    const current = ++request.current;
    setError("");
    setLevels([]);
    setLoading(true);
    try {
      if (file.size > maxFileBytes) throw new Error("CSV 不能超过 1 MiB。");
      const parsed = parseStockChipCsv(await file.text(), instrumentId, date);
      if (current === request.current) setLevels(parsed);
    } catch (failure) {
      if (current === request.current)
        setError(failure instanceof Error ? failure.message : "CSV 无法读取。");
    } finally {
      if (current === request.current) setLoading(false);
    }
  }

  const peak = Math.max(0, ...levels.map(({ percent }) => percent));
  return (
    <details className="stock-chip-viewer">
      <summary>筹码分布 · 本地 CSV</summary>
      <p>
        {instrumentId} · {date} · Tushare cyq_chips 估算持仓成本分布
      </p>
      <label>
        选择已获授权的 UTF-8 CSV
        <input type="file" accept=".csv,text/csv" onChange={importFile} />
      </label>
      <p>文件仅在当前页面读取。价档使用来源价格，独立于 K 线复权坐标。</p>
      {loading && <p role="status">正在读取 CSV…</p>}
      {error && <p role="alert">{error}</p>}
      {!!levels.length && (
        <>
          <p>来源价位 · {levels.length} 档 · 横条以最大单档占比为基准</p>
          <ul className="stock-chip-levels" aria-label="来源价位及筹码占比">
            {levels.map(({ price, percent }) => (
              <li key={price}>
                <span>{price}</span>
                <span className="stock-chip-track" aria-hidden="true">
                  <span style={{ width: `${(percent / peak) * 100}%` }} />
                </span>
                <span>{percent}%</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </details>
  );
}

export default function StockChipDistribution({
  instrumentId,
  date,
}: {
  instrumentId: string;
  date: string;
}) {
  return (
    <ChipViewer
      key={`${instrumentId}:${date}`}
      instrumentId={instrumentId}
      date={date}
    />
  );
}

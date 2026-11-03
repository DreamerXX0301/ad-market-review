import NoonStockChange from "./NoonStockChange";
import { useState } from "react";
import CopyStockCode from "../../components/CopyStockCode";
import type { Row } from "../../lib/api";
import { num, percent, tone } from "../../lib/format";
import {
  closeNumber,
  closeStateLabel,
  closeTransitionTone,
} from "./noonClosePresentation";
import { noonSortRows } from "./noonQuotePresentation";

type FocusSortKey =
  | "rank"
  | "name"
  | "noon_change_pct"
  | "close_change_pct"
  | "afternoon_change_pct";
type FocusSort = { key: FocusSortKey; direction: "asc" | "desc" };

/** Sort only this frozen small list; never fill a missing closing return. */
export function closeFocusRows(rows: Row[], sort: FocusSort | null): Row[] {
  if (!sort) return rows;
  return noonSortRows(rows, sort.key, sort.direction, (row, key) =>
    key === "name" ? row.name || row.instrument_id : closeNumber(row[key]),
  );
}

export default function NoonCloseFocusTable({
  rows,
  date,
  label,
  ranked = false,
  onStock,
}: {
  rows: Row[];
  date: string;
  label: string;
  ranked?: boolean;
  onStock?: (id: string, date: string) => void;
}) {
  const [sort, setSort] = useState<FocusSort | null>(null);
  const columns: { key: FocusSortKey; label: string; text?: boolean }[] = [
    ...(ranked ? [{ key: "rank" as const, label: "午间原排名" }] : []),
    { key: "name", label: "股票", text: true },
    { key: "noon_change_pct", label: "午间涨幅" },
    { key: "close_change_pct", label: "今日收盘涨幅" },
    { key: "afternoon_change_pct", label: "午后涨跌幅" },
  ];
  if (!rows.length)
    return (
      <p className="noon-unknown">
        该固定午间名单未留存可核验股票，缺失不按零收益处理。
      </p>
    );
  return (
    <div className="noon-table-wrap">
      <table className="noon-table noon-close-focus-table" aria-label={label}>
        <thead>
          <tr>
            {columns.map((column) => (
              <th
                key={column.key}
                className={column.text ? "noon-close-text" : ""}
                aria-sort={
                  sort?.key === column.key
                    ? sort.direction === "desc"
                      ? "descending"
                      : "ascending"
                    : undefined
                }
              >
                <button
                  type="button"
                  className="noon-close-sort"
                  onClick={() =>
                    setSort({
                      key: column.key,
                      direction:
                        sort?.key === column.key && sort.direction === "desc"
                          ? "asc"
                          : "desc",
                    })
                  }
                  aria-label={
                    label +
                    "按" +
                    column.label +
                    (sort?.key === column.key && sort.direction === "desc"
                      ? "升序"
                      : "降序")
                  }
                >
                  {column.label}
                  <span aria-hidden="true">
                    {sort?.key === column.key
                      ? sort.direction === "desc"
                        ? " ↓"
                        : " ↑"
                      : " ↕"}
                  </span>
                </button>
              </th>
            ))}
            <th className="noon-close-text">午间价 → 收盘价</th>
            <th className="noon-close-text">收盘状态</th>
          </tr>
        </thead>
        <tbody>
          {closeFocusRows(rows, sort).map((row) => {
            const canChart =
              !!onStock &&
              /^\d{6}\.(SH|SZ|BJ)$/.test(row.instrument_id || "") &&
              (closeNumber(row.close_price) || 0) > 0;
            return (
              <tr key={row.instrument_id}>
                {ranked && (
                  <td>{closeNumber(row.rank) == null ? "—" : row.rank}</td>
                )}
                <td className="noon-close-text">
                  <button
                    type="button"
                    className="noon-stock-link"
                    disabled={!canChart}
                    title={
                      canChart
                        ? "查看截至" + date + "的已发布收盘K线"
                        : "该日有效收盘行情尚未核验"
                    }
                    onClick={() => onStock?.(row.instrument_id, date)}
                  >
                    {row.name || row.instrument_id}
                  </button>
                  <div className="noon-close-identity">
                    <CopyStockCode instrumentId={row.instrument_id} />
                  </div>
                </td>
                <td>
                  <NoonStockChange
                    value={row.noon_change_pct}
                    state={row.noon_state}
                  />
                </td>
                <td
                  className={
                    "noon-close-day-change " +
                    tone(closeNumber(row.close_change_pct))
                  }
                >
                  {percent(closeNumber(row.close_change_pct))}
                </td>
                <td className={tone(closeNumber(row.afternoon_change_pct))}>
                  {percent(closeNumber(row.afternoon_change_pct))}
                </td>
                <td className="noon-close-text">
                  {num(closeNumber(row.noon_price))} →{" "}
                  {num(closeNumber(row.close_price))}
                </td>
                <td className={"noon-close-text " + closeTransitionTone(row)}>
                  {closeStateLabel(row)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

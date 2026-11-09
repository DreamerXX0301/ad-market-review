import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import CopyStockCode from "../../components/CopyStockCode";
import { percent, tone } from "../../lib/format";
import { marketMoney } from "./marketPresentation";
import { dragonTigerNetField } from "./dragonTigerPresentation";
import type {
  DragonTigerBoard,
  DragonTigerRow,
  DragonTigerSort,
  DragonTigerSortKey,
} from "./dragonTigerPresentation";

export function dragonTigerRowControl(target: EventTarget | null) {
  return !!(target as HTMLElement | null)?.closest?.(
    "button,a,input,select,textarea,summary,[data-copy-stock-code]",
  );
}

export default function DragonTigerTable({
  rows,
  board,
  page,
  sort,
  selectedKey,
  seatOpen,
  onPage,
  onSort,
  onSeat,
  onStock,
  registerRow,
}: {
  rows: DragonTigerRow[];
  board: DragonTigerBoard;
  page: number;
  sort: DragonTigerSort;
  selectedKey: string | null;
  seatOpen: boolean;
  onPage: (page: number) => void;
  onSort: (key: DragonTigerSortKey) => void;
  onSeat: (row: DragonTigerRow) => void;
  onStock: (row: DragonTigerRow) => void;
  registerRow?: (key: string, element: HTMLTableRowElement | null) => void;
}) {
  const pageCount = Math.max(1, Math.ceil(rows.length / 25));
  const currentPage = Math.max(1, Math.min(page, pageCount));
  const netField = dragonTigerNetField(board);
  const netLabel =
    board === "hot_money"
      ? "该标签净买额"
      : board === "org"
        ? "机构净买额"
        : "龙虎榜净买额";
  const columns: [DragonTigerSortKey, string][] = [
    ["name", "股票 / 来源概念"],
    ["change", "当日涨幅"],
    ["net", netLabel],
    ["buy", "龙虎榜买入额"],
    ["sell", "龙虎榜卖出额"],
    ["org", "机构净买额"],
  ];
  return (
    <>
      <div
        className="dragon-tiger-table-scroll"
        role="region"
        aria-label="龙虎榜记录，可横向滚动查看金额列"
        tabIndex={0}
      >
        <table className="dragon-tiger-table">
          <caption className="sr-only">
            点击股票名称查看K线；行内其余位置展开买卖席位，代码按钮只复制。
          </caption>
          <thead>
            <tr>
              {columns.map(([key, label], index) => {
                const active = key === sort.key;
                const Icon = active
                  ? sort.order === "asc"
                    ? ArrowUp
                    : ArrowDown
                  : ArrowUpDown;
                return (
                  <th
                    key={key}
                    scope="col"
                    className={index ? "numeric" : ""}
                    aria-sort={
                      active
                        ? sort.order === "asc"
                          ? "ascending"
                          : "descending"
                        : "none"
                    }
                  >
                    <button
                      type="button"
                      className={`dragon-tiger-sort ${active ? "active" : ""}`}
                      onClick={() => onSort(key)}
                      aria-label={`按${label}${active && sort.order === "desc" ? "升序" : "降序"}排列`}
                    >
                      <span>{label}</span>
                      <Icon size={12} />
                    </button>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {rows.slice((currentPage - 1) * 25, currentPage * 25).map((row) => (
              <tr
                key={row.key}
                ref={(element) => registerRow?.(row.key, element)}
                tabIndex={0}
                aria-label={`${row.name || row.instrument_id}，查看买卖席位`}
                aria-expanded={selectedKey === row.key && seatOpen}
                aria-controls="dragon-tiger-seats"
                className={selectedKey === row.key ? "selected" : ""}
                onClickCapture={(event) => {
                  if (!dragonTigerRowControl(event.target)) onSeat(row);
                }}
                onKeyDown={(event) => {
                  if (
                    event.target !== event.currentTarget ||
                    !["Enter", " "].includes(event.key)
                  )
                    return;
                  event.preventDefault();
                  onSeat(row);
                }}
              >
                <td>
                  <button
                    type="button"
                    className="stock-name-link"
                    onClick={(event) => {
                      event.stopPropagation();
                      onStock(row);
                    }}
                  >
                    {row.name || row.instrument_id}
                  </button>
                  <div className="dragon-tiger-code">
                    <CopyStockCode
                      code={row.code}
                      instrumentId={row.instrument_id}
                    />
                    {row.label_name && <small>{row.label_name}</small>}
                  </div>
                  {row.source_concepts.length > 0 && (
                    <div className="tags dragon-tiger-concepts">
                      {row.source_concepts.slice(0, 2).map((item, index) => (
                        <span className="badge" key={`${item.name}:${index}`}>
                          {item.name}
                        </span>
                      ))}
                    </div>
                  )}
                </td>
                <td className={`numeric ${tone(row.change_pct)}`}>
                  {percent(row.change_pct)}
                </td>
                <td className={`numeric ${tone(row[netField])}`}>
                  {marketMoney(row[netField])}
                </td>
                <td className="numeric">{marketMoney(row.buy_value)}</td>
                <td className="numeric">{marketMoney(row.sell_value)}</td>
                <td className={`numeric ${tone(row.org_net_value)}`}>
                  {marketMoney(row.org_net_value)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="dragon-tiger-pagination">
        <span>
          第{currentPage}/{pageCount}页 · {rows.length}条记录 · 每页25条
        </span>
        <div className="button-group">
          <button
            className="btn small"
            disabled={currentPage <= 1}
            onClick={() => onPage(currentPage - 1)}
          >
            上一页
          </button>
          <button
            className="btn small"
            disabled={currentPage >= pageCount}
            onClick={() => onPage(currentPage + 1)}
          >
            下一页
          </button>
        </div>
      </div>
      <div className="panel-footnote">
        对当前筛选全部记录排序后分页，未知值始终在末尾。名称查看K线，整行查看席位。
      </div>
    </>
  );
}

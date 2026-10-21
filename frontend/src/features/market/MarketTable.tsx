import { useState } from "react";
import type { ComponentProps } from "react";
import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import { Table } from "../../components/UI";
import type { Column } from "../../components/UI";
import { label, num, tone } from "../../lib/format";
import { MetricHelp } from "./MetricHelp";
import {
  marketExplanations,
  marketLabels,
  marketMoney,
  sortMarketRows,
} from "./marketPresentation";
import type { MarketSort } from "./marketPresentation";

const moneyFields = new Set(["amount", "seal_money", "net_inflow"]);
export function CurrencyCell({
  value,
  field,
  description,
}: {
  value: unknown;
  field?: string;
  description?: string;
}) {
  const known = typeof value === "number" && Number.isFinite(value);
  return (
    <span
      className={
        !known ? "muted" : field === "net_inflow" ? tone(value) : undefined
      }
      title={
        known
          ? `${description ? description + "；" : ""}原始金额 ${num(value)} 元（CNY）`
          : description || "金额未知，未补0"
      }
    >
      {marketMoney(value)}
    </span>
  );
}
export function MarketSortHeader({
  field,
  caption,
  sort,
  onSort,
}: {
  field: string;
  caption: string;
  sort: MarketSort | null;
  onSort: (key: string) => void;
}) {
  const active = sort?.key === field;
  const Icon = active
    ? sort?.order === "desc"
      ? ArrowDown
      : ArrowUp
    : ArrowUpDown;
  return (
    <span className="market-table-heading">
      <button
        className={"market-sort-heading " + (active ? "active" : "")}
        onClick={() => onSort(field)}
        aria-label={`按${caption}${active && sort?.order === "desc" ? "升序" : "降序"}排列`}
      >
        {caption}
        <Icon size={12} />
      </button>
      {marketExplanations[field] && (
        <MetricHelp label={caption} description={marketExplanations[field]} />
      )}
    </span>
  );
}
export function decorateMarketColumns(
  columns: Column[],
  sort: MarketSort | null,
  onSort: (key: string) => void,
  allowed?: Set<string>,
): Column[] {
  return columns.map((column) => {
    const caption =
      typeof column.title === "string"
        ? column.title
        : marketLabels[column.key] || label[column.key] || column.key;
    const sortable =
      column.sortable !== false &&
      !["rank", "actions"].includes(column.key) &&
      (!allowed || allowed.has(column.key));
    return {
      ...column,
      title: sortable ? (
        <MarketSortHeader
          field={column.key}
          caption={caption}
          sort={sort}
          onSort={onSort}
        />
      ) : (
        column.title || caption
      ),
      ...(!column.render && moneyFields.has(column.key)
        ? {
            render: (value: unknown) => (
              <CurrencyCell value={value} field={column.key} />
            ),
          }
        : {}),
    };
  });
}
export function SortableMarketTable({
  rows,
  columns,
  sortScope,
  defaultSort = null,
  ...rest
}: ComponentProps<typeof Table> & {
  sortScope: string;
  defaultSort?: MarketSort | null;
}) {
  const [sort, setSort] = useState<MarketSort | null>(defaultSort);
  function change(key: string) {
    setSort({
      key,
      order: sort?.key === key && sort.order === "desc" ? "asc" : "desc",
    });
  }
  return (
    <>
      <Table
        {...rest}
        sorting="external"
        key={(sort?.key || "source") + sort?.order}
        rows={sortMarketRows(rows, sort)}
        columns={decorateMarketColumns(columns, sort, change)}
      />
      <div className="panel-footnote market-sort-scope">
        {sortScope}；按原始数值或名称排序后分页，未知值始终排在末尾。
      </div>
    </>
  );
}

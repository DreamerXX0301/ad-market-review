import { useState } from "react";
import type { ReactNode } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { Row } from "../../lib/api";
import { num, percent, tone } from "../../lib/format";
import { Badge, Table } from "../../components/UI";
import "./TrackingStockTable.css";

/** An observation row never chooses one strategy's entry price or policy. */
export default function TrackingStockTable({
  groups,
  businessDate,
  renderRoundTable,
  hasFilters,
  onStockNavigate,
}: {
  groups: Row[];
  businessDate?: string;
  renderRoundTable: (rounds: Row[], orderedGroups: Row[]) => ReactNode;
  hasFilters: boolean;
  onStockNavigate: (
    id: string,
    orderedGroups: Row[],
    clickedGroup: Row,
  ) => void;
}) {
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const toggle = (id: string) =>
    setExpanded((previous) => {
      const next = new Set(previous);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const detailsId = (row: Row) =>
    `tracking-stock-rounds-${String(row.instrument_id).replace(/[^\w-]/g, "-")}`;
  return (
    <div className="tracking-stock-groups">
      <Table
        rows={groups}
        rowKey="instrument_id"
        onStockNavigate={onStockNavigate}
        rowProps={(row) => ({
          className: "tracking-stock-group-row",
          tabIndex: 0,
          "aria-expanded": expanded.has(row.instrument_id),
          "aria-controls": detailsId(row),
          onClick: (event) => {
            const target = event.target as HTMLElement;
            if (
              target.closest?.(
                'button, a, input, select, textarea, label, [role="button"], [role="checkbox"], [contenteditable]:not([contenteditable="false"])',
              )
            )
              return;
            toggle(row.instrument_id);
          },
          onKeyDown: (event) => {
            if (
              event.target === event.currentTarget &&
              (event.key === "Enter" || event.key === " ")
            ) {
              event.preventDefault();
              toggle(row.instrument_id);
            }
          },
        })}
        multiSort
        multiDefaultSort={[
          { key: "strategy_count", order: "desc" },
          { key: "today_change_pct", order: "desc" },
        ]}
        columns={[
          {
            key: "name",
            title: "股票",
          },
          {
            key: "strategy_count",
            title: "当前匹配策略",
            render: (value, row) => (
              <div className="tracking-stock-strategies">
                <strong>当前匹配 {num(value, 0)} 个策略</strong>
                <div className="tags">
                  {(row.strategy_names || [])
                    .slice(0, 3)
                    .map((name: string, index: number) => (
                      <Badge key={`${name}-${index}`}>{name}</Badge>
                    ))}
                  {(row.strategy_names || []).length > 3 && (
                    <span
                      className="tracking-stock-more"
                      title={row.strategy_names.join("、")}
                    >
                      +{row.strategy_names.length - 3}
                    </span>
                  )}
                </div>
              </div>
            ),
          },
          {
            key: "latest_price",
            title: "最新收盘",
            render: (value, row) => (
              <span
                title={
                  row.quote_reason ||
                  `行情日 ${row.valuation_date || businessDate || "待核验"}`
                }
              >
                {num(value)}
              </span>
            ),
          },
          {
            key: "today_change_pct",
            title: "今日涨幅",
            render: (value, row) => (
              <span
                className={tone(value)}
                title={
                  row.quote_reason ||
                  `行情日 ${row.valuation_date || businessDate || "待核验"}`
                }
              >
                {percent(value)}
              </span>
            ),
          },
          {
            key: "earliest_entry_date",
            title: "各轮入池日期范围",
            render: (_, row) => (
              <div className="tracking-stock-entry-range">
                <span>
                  {row.earliest_entry_date || "待核验"}
                  {row.last_entry_date &&
                  row.last_entry_date !== row.earliest_entry_date
                    ? ` ～ ${row.last_entry_date}`
                    : ""}
                </span>
                <small>各策略基准与跟踪涨幅见展开明细</small>
              </div>
            ),
          },
          {
            key: "triggered_round_count",
            title: "今日提醒轮次",
            render: (value, row) =>
              value > 0 ? (
                <Badge tone="warn">
                  {value} / {row.round_count} 轮有记录
                </Badge>
              ) : (
                <span className="muted">0 / {row.round_count} 轮有记录</span>
              ),
          },
          {
            key: "actions",
            title: "策略轮次",
            sortable: false,
            render: (_, row) => {
              const open = expanded.has(row.instrument_id);
              const Icon = open ? ChevronDown : ChevronRight;
              return (
                <button
                  type="button"
                  className={`btn small tracking-stock-expand${open ? " active" : ""}`}
                  aria-expanded={open}
                  aria-controls={detailsId(row)}
                  onClick={() => toggle(row.instrument_id)}
                >
                  <Icon size={14} aria-hidden="true" />
                  {open ? "收起" : "展开"} {row.round_count} 轮
                </button>
              );
            },
          },
        ]}
        pageSize={20}
        renderExpandedRow={(row, orderedGroups) =>
          expanded.has(row.instrument_id) ? (
            <section
              id={detailsId(row)}
              className="tracking-stock-round-details"
              aria-label={`${row.name || row.code}的策略跟踪轮次`}
            >
              <div className="tracking-stock-round-heading">
                <strong>
                  {row.name || row.code} · 当前匹配 {row.round_count} 个轮次
                </strong>
                <span>
                  入池基准、收益、政策和移出操作分别属于每个策略轮次。
                </span>
              </div>
              {renderRoundTable(row.rounds, orderedGroups)}
            </section>
          ) : null
        }
        emptyTitle={hasFilters ? "没有匹配的跟踪股票" : "还没有跟踪中的股票"}
        emptyDescription={
          hasFilters
            ? "可切换策略或调整搜索及筛选条件。"
            : "先在策略管理运行选股，再将命中结果加入对应跟踪池。"
        }
      />
    </div>
  );
}

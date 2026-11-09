import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { list, useApi } from "../../lib/api";
import type { Row } from "../../lib/api";
import { createStockNavigation } from "../../components/stockNavigation";
import type { StockNavigation } from "../../components/stockNavigation";
import {
  Badge,
  ErrorBox,
  Loading,
  Panel,
  Table,
  Warnings,
} from "../../components/UI";
import { integer } from "../../lib/format";
import MarketDataCoverage from "./MarketDataCoverage";

export type MonitorScope = "latest" | "historical";

export function monitorRequest(date: string, scope: MonitorScope) {
  if (scope === "historical" && !date) return null;
  return `/market/abnormal?monitor_scope=${scope}&limit=1000${date ? `&date=${encodeURIComponent(date)}` : ""}`;
}

export function monitorQuoteDate(value: unknown) {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value))
    return undefined;
  const stamp = Date.parse(value + "T00:00:00.000Z");
  return Number.isFinite(stamp) &&
    new Date(stamp).toISOString().slice(0, 10) === value
    ? value
    : undefined;
}

export default function AbnormalMonitor({
  date,
  onStock,
}: {
  date: string;
  onStock: (id: string, navigation?: StockNavigation) => void;
}) {
  const [scope, setScope] = useState<MonitorScope>("latest");
  const [filter, setFilter] = useState("");
  const result = useApi<Row>(monitorRequest(date, scope));
  const scopeMismatch =
    result.data != null && result.data.monitor_scope !== scope;
  const data = scopeMismatch ? null : result.data;
  const rows = list(data).filter((row) =>
    [row.name, row.code, row.instrument_id, row.event_type]
      .join(" ")
      .toLowerCase()
      .includes(filter.trim().toLowerCase()),
  );
  const available = data?.data_coverage?.selected_date_available === true;
  const quoteDate = monitorQuoteDate(data?.quote_as_of);
  const observedDate = monitorQuoteDate(data?.monitor_observed_date);
  return (
    <div className="page-stack abnormal-monitor">
      {result.error && (
        <ErrorBox message={result.error} retry={result.reload} />
      )}
      {scopeMismatch && (
        <ErrorBox
          message="当前API未返回所选监控名单范围，请使用配套更新的服务后重读；当前监管状态未知。"
          retry={result.reload}
        />
      )}
      <Panel
        title={
          scope === "latest" ? "监控名单" : `${date || "所选日"} 历史监控名单`
        }
        subtitle="供应商严重异动与重点监控标签；官方监管原文尚未接入。"
        actions={
          <button
            type="button"
            className="icon-btn"
            aria-label="重读监控名单存档"
            onClick={result.reload}
          >
            <RefreshCw size={15} />
          </button>
        }
      >
        <div className="abnormal-monitor-controls">
          <label>
            查看范围{" "}
            <select
              aria-label="选择监控名单范围"
              value={scope}
              onChange={(event) => {
                setScope(event.target.value as MonitorScope);
                setFilter("");
              }}
            >
              <option value="latest">最新名单</option>
              <option value="historical">指定盘面日历史名单</option>
            </select>
          </label>
          <input
            className="search-input"
            type="search"
            aria-label="筛选监控名单"
            placeholder="股票、代码或监控标签…"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
          />
        </div>
        <div
          className="abnormal-monitor-evidence"
          role="group"
          aria-label="名单来源日期"
        >
          <span className="muted">名单日期 {observedDate || "未知"}</span>
          <span className="muted">行情日期 {quoteDate || "未知"}</span>
          <Badge>
            {available
              ? `${integer(data?.total)} 条`
              : result.loading
                ? "核验中"
                : "名单未知"}
          </Badge>
          {scope === "latest" &&
            observedDate &&
            date &&
            observedDate !== date && (
              <Badge tone="warn">非盘面选择日名单</Badge>
            )}
        </div>
        {result.loading && !data ? (
          <Loading />
        ) : (
          <Table
            rows={rows}
            columns={[
              { key: "name", title: "股票" },
              { key: "event_type", title: "供应商监控标签" },
              { key: "start_date", title: "供应商区间起日" },
              { key: "end_date", title: "供应商区间止日" },
              { key: "source", title: "来源" },
            ]}
            onStockNavigate={
              quoteDate
                ? (id, orderedRows) =>
                    onStock(
                      id,
                      createStockNavigation(orderedRows, {
                        source: "market",
                        scopeKey: `abnormal:${scope}:${data?.monitor_observed_date}:${quoteDate}`,
                        label:
                          scope === "latest" ? "最新监控名单" : "历史监控名单",
                        asOf: quoteDate,
                      }),
                    )
                : undefined
            }
            emptyTitle={
              !available
                ? "监控状态未知"
                : filter
                  ? "没有匹配记录"
                  : "该观察日名单为空"
            }
            emptyDescription={
              data?.unavailable_reason ||
              (available
                ? "仅表示该来源在此观察日返回的名单记录，不证明全部监管事件均已接入。"
                : "正在核验已发布名单的来源日期；无留存不能解释为无监管。")
            }
          />
        )}
        <details className="detail-list">
          <summary>来源与日期说明</summary>
          <p>
            {scope === "latest"
              ? `查看最新已留存名单，不代表 ${date || "所选日"} 的历史监管状态。`
              : `仅查看名单观察日为 ${date || "所选日"} 的记录；未留存仍为未知，不用最新名单回填。`}
          </p>
          <p>
            名单获取时间 <b>{data?.monitor_fetched_at || "未知"}</b>
          </p>
          <p>
            来源 <b>{data?.source || "未知"}</b>
          </p>
          <p>
            供应商区间不表示官方监管期限；官方解除日期未知。刷新仅重读已有名单，不联网采集。
          </p>
          <MarketDataCoverage
            coverage={data?.data_coverage}
            loading={result.loading}
          />
          <Warnings items={data?.warnings} />
        </details>
      </Panel>
    </div>
  );
}

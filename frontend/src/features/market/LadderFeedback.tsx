import type { Row } from "../../lib/api";
import { integer, percent, tone } from "../../lib/format";
import {
  createStockNavigation,
  type StockNavigation,
} from "../../components/stockNavigation";
import { Badge, Panel } from "../../components/UI";
import { SortableMarketTable } from "./MarketTable";
import { summarizeLadderFeedback } from "./ladderFeedbackSummary";
import "./LadderFeedback.css";

function TopicTags({ value, row }: { value: unknown; row: Row }) {
  const tags: Row[] = Array.isArray(value) ? value : [];
  if (!tags.length)
    return (
      <span className="muted">
        {row.topics_state === "unknown" ? "当日归属未留存" : "暂无已知题材"}
      </span>
    );
  const render = (tag: Row) => (
    <Badge key={tag.topic_id || tag.name}>{tag.name}</Badge>
  );
  return (
    <div className="ladder-feedback-topics">
      <div className="tags">{tags.slice(0, 3).map(render)}</div>
      {tags.length > 3 && (
        <details>
          <summary>另 {tags.length - 3} 项题材</summary>
          <div className="tags">{tags.slice(3).map(render)}</div>
        </details>
      )}
    </div>
  );
}

export default function LadderFeedback({
  rows,
  date,
  onStock,
}: {
  rows: Row[];
  date: string;
  onStock: (id: string, navigation?: StockNavigation) => void;
}) {
  const summary = summarizeLadderFeedback(rows);
  const distribution = [
    { key: "up", label: "上涨", count: summary.up },
    { key: "down", label: "下跌", count: summary.down },
    { key: "flat", label: "平盘", count: summary.flat },
    { key: "unknown", label: "未知", count: summary.unknown },
  ];
  return (
    <div className="ladder-feedback-layout">
      <Panel
        title="连板样本反馈"
        subtitle="上一交易日已留存连板样本 · 当日实际涨跌与当日可知归属"
        className="ladder-feedback-table"
      >
        <SortableMarketTable
          sortScope="当前返回的上一交易日已留存连板样本"
          rows={rows}
          columns={[
            { key: "name" },
            {
              key: "topics",
              title: "题材",
              render: (value, row) => <TopicTags value={value} row={row} />,
            },
            {
              key: "industries",
              title: "行业",
              render: (value) =>
                Array.isArray(value) && value.length ? (
                  <div className="tags">
                    {value.map((tag: Row) => (
                      <Badge key={tag.topic_id || tag.name}>{tag.name}</Badge>
                    ))}
                  </div>
                ) : (
                  <span className="muted">待核验</span>
                ),
            },
            { key: "previous_board_count", title: "前日连板" },
            { key: "previous_board_date", title: "样本日" },
            { key: "change_pct" },
            { key: "feedback", title: "当日反馈" },
          ]}
          onStock={onStock}
          onStockNavigate={(id, orderedRows) =>
            onStock(
              id,
              createStockNavigation(orderedRows, {
                source: "market",
                scopeKey: `flow:feedback:${date}`,
                label: "资金轮动 · 连板反馈样本",
                asOf: date,
              }),
            )
          }
          pageSize={10}
          emptyTitle="没有留存的可比连板样本"
          emptyDescription="样本反馈只描述已留存标的，不代表全市场赚钱效应或机构资金决策。"
        />
        <div className="panel-footnote">
          按反馈观察日的已留存归属展示，当前业务日包含人工补充；不拿当前题材回填历史，也不代表该股当日上涨原因。
        </div>
      </Panel>
      <Panel
        title="连板反馈概览"
        subtitle={`${date} · 全部已返回反馈样本`}
        className="ladder-feedback-overview"
      >
        <div className="ladder-feedback-summary">
          <div className="ladder-feedback-total">
            <span>已返回反馈样本</span>
            <strong>
              {integer(summary.total)} <small>只</small>
            </strong>
            <span>已知涨幅 {integer(summary.known)} 只</span>
          </div>
          {summary.total > 0 && (
            <div
              className="ladder-feedback-distribution"
              role="img"
              aria-label={distribution
                .map((item) => `${item.label}${integer(item.count)}只`)
                .join("，")}
            >
              {distribution.map((item) =>
                item.count ? (
                  <span
                    key={item.key}
                    className={item.key}
                    style={{ flex: item.count }}
                  />
                ) : null,
              )}
            </div>
          )}
          <dl className="ladder-feedback-counts">
            {distribution.map((item) => (
              <div key={item.key}>
                <dt>
                  <i className={item.key} />
                  {item.label}
                </dt>
                <dd>{integer(item.count)}</dd>
              </div>
            ))}
          </dl>
          <dl className="ladder-feedback-returns">
            <div>
              <dt>平均涨幅</dt>
              <dd className={tone(summary.average)}>
                {percent(summary.average)}
              </dd>
            </div>
            <div>
              <dt>涨幅中位数</dt>
              <dd className={tone(summary.median)}>
                {percent(summary.median)}
              </dd>
            </div>
          </dl>
          <p className="ladder-feedback-decline">
            回落至少5% <strong>{integer(summary.sharpDecline)} 只</strong>
          </p>
          <div className="ladder-feedback-heights">
            <h3>按前日连板高度</h3>
            {summary.heights.length ? (
              <ul>
                {summary.heights.map((height) => (
                  <li key={height.height ?? "unknown"}>
                    <div>
                      <strong>
                        {height.height == null
                          ? "高度未知"
                          : `${integer(height.height)}板`}
                      </strong>
                      <span>
                        {integer(height.total)} 只 · 已知{" "}
                        {integer(height.known)}只
                      </span>
                      <small>
                        涨 {integer(height.up)} / 跌 {integer(height.down)} / 平{" "}
                        {integer(height.flat)} / 未知 {integer(height.unknown)}
                      </small>
                    </div>
                    <div className="ladder-feedback-height-average">
                      <span>平均涨幅</span>
                      <strong className={tone(height.average)}>
                        {percent(height.average)}
                      </strong>
                    </div>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted">暂无可比较的已留存样本</p>
            )}
          </div>
        </div>
        <div className="panel-footnote">
          概览按本次返回的全部样本统计，不随表格排序或分页变化。平均与中位数仅计已知涨幅；不代表上一日全部连板股或全市场。
        </div>
      </Panel>
    </div>
  );
}

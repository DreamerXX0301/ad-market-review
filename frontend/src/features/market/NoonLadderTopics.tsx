import type { Row } from "../../lib/api";
import CopyStockCode from "../../components/CopyStockCode";
import { NoonBoardTag } from "./NoonBoardFilter";
import { noonFilterBoards } from "./noonBoardPresentation";
import type { NoonBoardFilterValue } from "./noonBoardPresentation";
import { percent, tone } from "../../lib/format";
import { noonNumber, noonRows } from "./noonPresentation";
import { noonKnownHeight } from "./noonQuotePresentation";
import "./NoonLadderTopics.css";

type StockProps = {
  onStock?: (id: string) => void;
  chartDate?: string;
  sampleLabel?: string;
  boardFilter?: NoonBoardFilterValue;
  showSampleChange?: boolean;
};

function TopicChip({
  row,
  sampleLabel = "涨停",
  boardFilter = "all",
  showSampleChange = false,
  ...stockProps
}: StockProps & { row: Row }) {
  const maximum = noonKnownHeight(row.max_board_count);
  const allItems = noonRows(row.items);
  const items = noonFilterBoards(allItems, boardFilter);
  const meanChange = noonNumber(row.mean_change_pct);
  const dates = Array.from(
    new Set(
      (Array.isArray(row.members_observed_date)
        ? row.members_observed_date
        : [row.members_observed_date]
      ).filter((day): day is string => typeof day === "string" && !!day),
    ),
  );
  const provenance = `来源代码 ${row.topic_code || "未提供"}；原采集代际成员名单日 ${dates.join("、") || "未提供"}；只统计本次已核验${sampleLabel}样本，成员归属不代表事件原因。`;
  return (
    <details className="noon-ladder-topic-chip">
      <summary title={provenance}>
        <span>{row.name}</span>
        <b>{row.count}只</b>
        {maximum != null && (
          <small>
            {sampleLabel.startsWith("昨日") ? "昨日最高" : "最高"}
            {maximum}板
          </small>
        )}
        {showSampleChange && (
          <small
            className={tone(meanChange)}
            title={`组内有效午间报价的平均涨跌幅；有效${row.valid_count ?? "—"}/${row.count ?? "—"}只，缺报价不计入均值；不随上市板别筛选重算。`}
          >
            平均涨跌幅{percent(meanChange)}
          </small>
        )}
      </summary>
      <div className="noon-ladder-topic-members">
        {boardFilter !== "all" && (
          <p>
            当前板别显示{items.length} / 本组{allItems.length}
            只；家数、最高板数与平均涨跌幅保持原统计。
          </p>
        )}
        {items.length ? (
          <ul aria-label={`${row.name}当前${sampleLabel}样本股票`}>
            {items.map((item, index) => {
              const height = noonKnownHeight(item.board_count);
              const canOpen =
                !!stockProps.onStock &&
                !!stockProps.chartDate &&
                /^\d{6}\.(SH|SZ|BJ)$/.test(item.instrument_id || "");
              return (
                <li key={item.instrument_id || index}>
                  <div>
                    <button
                      type="button"
                      className="noon-stock-link"
                      disabled={!canOpen}
                      title={
                        stockProps.chartDate
                          ? `查看截至${stockProps.chartDate}的已发布K线`
                          : "该午评之前没有已发布收盘行情"
                      }
                      onClick={() => stockProps.onStock?.(item.instrument_id)}
                    >
                      {item.name || item.instrument_id || "身份待核验"}
                    </button>
                    <small className="noon-stock-code-line">
                      <CopyStockCode instrumentId={item.instrument_id} />
                      <NoonBoardTag row={item} />
                    </small>
                  </div>
                  <b>
                    {height != null
                      ? `${sampleLabel.startsWith("昨日") ? "昨日" : ""}${height}板`
                      : ""}
                  </b>
                  {noonNumber(item.change_pct) != null && (
                    <span className={tone(noonNumber(item.change_pct))}>
                      {percent(noonNumber(item.change_pct))}
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        ) : (
          <p>
            {boardFilter !== "all"
              ? "该板别暂无展示样本。"
              : "逐股明细未提供。"}
          </p>
        )}
        <p>{provenance}</p>
      </div>
    </details>
  );
}

export default function NoonLadderTopics({
  value,
  sampleLabel = "涨停",
  limit = 7,
  boardFilter = "all",
  showSampleChange = false,
  ...stockProps
}: StockProps & { value?: Row | null; limit?: number }) {
  if (!value) return null;
  const valid = (items: unknown) =>
    noonRows(items).filter(
      (row) =>
        typeof row.name === "string" &&
        row.name.trim() &&
        typeof row.count === "number" &&
        Number.isInteger(row.count) &&
        row.count > 0,
    );
  const industries = valid(value.industries);
  const concepts = valid(value.topics);
  if (!industries.length && !concepts.length)
    return (
      <p className="noon-ladder-topics-empty">
        本次{sampleLabel}样本暂无已核验的行业/概念分组。
      </p>
    );
  return (
    <section
      className="noon-ladder-topics"
      aria-label={`题材内${sampleLabel}样本分布`}
    >
      <div className="noon-ladder-topics-total">
        <span>已核验归属</span>
        <b>{industries.length}</b>行业 / <b>{concepts.length}</b>概念
        {noonNumber(value.sample_count) != null && (
          <span>
            · {value.sample_count}只{sampleLabel}样本
          </span>
        )}
      </div>
      {[
        { label: "行业", items: industries },
        { label: "概念", items: concepts },
      ].map(({ label, items }) =>
        items.length ? (
          <div className="noon-ladder-topics-row" key={label}>
            <b>{label}</b>
            <div className="noon-ladder-topics-chips">
              {items.slice(0, limit).map((row, index) => (
                <TopicChip
                  key={row.topic_code || index}
                  row={row}
                  sampleLabel={sampleLabel}
                  boardFilter={boardFilter}
                  showSampleChange={showSampleChange}
                  {...stockProps}
                />
              ))}
            </div>
          </div>
        ) : null,
      )}
      <p className="noon-ladder-topics-note" title={value.scope || undefined}>
        各列样本家数前{limit}个方向；同股可属多个行业/概念，数量不可相加。
        {noonNumber(value.unclassified_count) != null &&
        value.unclassified_count > 0
          ? `另有${value.unclassified_count}只未取得可核验归属。`
          : ""}
      </p>
    </section>
  );
}

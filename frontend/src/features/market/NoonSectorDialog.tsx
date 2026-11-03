import NoonStockChange from "./NoonStockChange";
import { useEffect, useState } from "react";
import type { Row } from "../../lib/api";
import { useApi } from "../../lib/api";
import { amount, num, percent, tone } from "../../lib/format";
import {
  Badge,
  Empty,
  ErrorBox,
  Loading,
  Modal,
  Table,
  Warning,
} from "../../components/UI";
import { CandleChart } from "../../components/Charts";
import { boardLabel, NOON_BOARD_OPTIONS } from "./noonBoardPresentation";
import { noonClock } from "./noonPresentation";
import { noonSectorEndpoint } from "./noonSectorPresentation";
import "../sector/MemberDialog.css";
import "./NoonSectorDialog.css";
import "./NoonControls.css";

const eventLabels: Record<string, string> = {
  limit_up: "本次涨停",
  limit_down: "本次跌停",
  limit_break: "本次炸板",
};

export function NoonSectorQuote({ data }: { data: Row }) {
  const quote = data.sector_quote || {};
  return (
    <section className="noon-sector-quote" aria-label="午间形成中报价">
      <div className="sector-chart-context noon-sector-quote-heading">
        <Badge>{data.date || "日期未核验"} · 午间形成中</Badge>
        <span>
          采集区间 {noonClock(data.collected_start_at)} —{" "}
          {noonClock(data.collected_end_at)} · 逐条报价时刻{" "}
          {quote.quote_at || "未提供"}
        </span>
      </div>
      <div className="market-members-summary member-dialog-context noon-sector-values">
        <span className="member-context-item">
          <small>午间涨幅</small>
          <strong className={tone(quote.change_pct)}>
            {percent(quote.change_pct)}
          </strong>
        </span>
        <span className="member-context-item">
          <small>午间最新</small>
          <strong>{num(quote.latest_price)}</strong>
        </span>
        <span className="member-context-item">
          <small>开</small>
          <strong>{num(quote.open)}</strong>
        </span>
        <span className="member-context-item">
          <small>高</small>
          <strong>{num(quote.high)}</strong>
        </span>
        <span className="member-context-item">
          <small>低</small>
          <strong>{num(quote.low)}</strong>
        </span>
        <span className="member-context-item">
          <small>午间成交额</small>
          <strong>{amount(quote.amount)}</strong>
        </span>
      </div>
      <p className="muted sector-chart-scope noon-sector-quote-note">
        同份午评冻结报价；不是收盘确认，不属于正式日K，不纳入正式日K或选股指标。
      </p>
    </section>
  );
}

export default function NoonSectorDialog({
  topic,
  snapshotId,
  initialView = "members",
  onClose,
  onStock,
  embedded = false,
}: {
  topic: Row;
  snapshotId: string;
  initialView?: "chart" | "members";
  onClose?: () => void;
  onStock?: (id: string, asOf?: string) => void;
  embedded?: boolean;
}) {
  const [view, setView] = useState(initialView),
    [board, setBoard] = useState("all");
  const [query, setQuery] = useState(""),
    [search, setSearch] = useState("");
  const [sort, setSort] = useState("change_pct"),
    [direction, setDirection] = useState("desc"),
    [page, setPage] = useState(1);
  useEffect(() => {
    const timer = setTimeout(() => {
      setSearch(query);
      setPage(1);
    }, 200);
    return () => clearTimeout(timer);
  }, [query]);
  const result = useApi<Row>(
    noonSectorEndpoint(snapshotId, topic.topic_code, {
      board,
      query: search,
      sort,
      direction,
      page,
      limit: 50,
    }),
  );
  const data = result.data;
  const content = (
    <div
      className={`${embedded ? "noon-sector-embedded" : "modal-body"} noon-sector-body ${view === "chart" ? "sector-chart-dialog-body entry-page-scroll" : "market-members-body"}`}
    >
      {!embedded && (
        <div
          className="segmented global-sector-tabs"
          role="tablist"
          aria-label="午间板块详情视图"
        >
          <button
            role="tab"
            aria-selected={view === "chart"}
            className={view === "chart" ? "active" : ""}
            onClick={() => setView("chart")}
          >
            板块K线
          </button>
          <button
            role="tab"
            aria-selected={view === "members"}
            className={view === "members" ? "active" : ""}
            onClick={() => setView("members")}
          >
            成分股
          </button>
        </div>
      )}
      {result.error && (
        <ErrorBox message={result.error} retry={result.reload} />
      )}
      {result.loading && !data ? (
        <Loading />
      ) : (
        data && (
          <>
            {embedded ? (
              <details className="market-members-details">
                <summary>板块报价与采集时间</summary>
                <NoonSectorQuote data={data} />
              </details>
            ) : (
              <NoonSectorQuote data={data} />
            )}
            {view === "chart" ? (
              <>
                <p className="muted sector-chart-scope">
                  午间形成中报价单独展示。下方历史图最晚截至{" "}
                  {data.formal_chart_as_of || "未知"}{" "}
                  的已留存收盘，不将本次午间报价拼入历史K线，不触发远程加载。
                </p>
                {data.formal_chart_as_of ? (
                  <CandleChart
                    instrumentId={topic.topic_code}
                    instrumentKind="index"
                    asOf={data.formal_chart_as_of}
                    dataEndpoint={`/market/sector-chart?topic_code=${encodeURIComponent(topic.topic_code)}&date=${data.formal_chart_as_of}`}
                    showStockSummary={false}
                    showDataNotes={false}
                    height={680}
                    entryFocus
                  />
                ) : (
                  <Empty
                    title="未核验前一交易日"
                    description="仅展示本份午间报价，不向后读取收盘行情。"
                  />
                )}
              </>
            ) : (
              <>
                <div className="market-members-summary member-dialog-context noon-sector-members-context">
                  <Badge>本次午间报价</Badge>
                  <span className="member-context-item">
                    <small>午间报价日</small>
                    <strong>{data.date || "未核验"}</strong>
                  </span>
                  <span className="member-context-item">
                    <small>成员名单留存日</small>
                    <strong>{data.membership_as_of || "未核验"}</strong>
                  </span>
                  <span className="member-context-item">
                    <small>已知成员</small>
                    <strong>{data.total ?? "未知"} 只</strong>
                  </span>
                  <span className="member-context-item">
                    <small>可比报价</small>
                    <strong>{data.quote_coverage?.valid ?? "未知"} 只</strong>
                  </span>
                </div>
                <details className="market-members-details">
                  <summary>数据口径与来源</summary>
                  <div>
                    <p>{data.membership_basis || "成员口径尚未核验"}。</p>
                    <p>
                      股票名称打开的正式历史K线仍截至前一交易日；午间价格以上表为准。
                    </p>
                  </div>
                </details>
                {data.unavailable_reason && (
                  <Warning>{data.unavailable_reason}</Warning>
                )}
                <div className="member-dialog-toolbar">
                  <div className="result-toolbar member-dialog-search-row noon-sector-filters">
                    <label className="member-dialog-field member-dialog-field-search">
                      <span>搜索本份成员</span>
                      <input
                        className="search-input"
                        value={query}
                        onChange={(event) => setQuery(event.target.value)}
                        placeholder="名称 / 代码"
                      />
                    </label>
                    <label className="member-dialog-field">
                      <span>排序</span>
                      <select
                        value={sort}
                        onChange={(event) => {
                          setSort(event.target.value);
                          setPage(1);
                        }}
                      >
                        {[
                          ["change_pct", "午间涨幅"],
                          ["amount", "午间成交额"],
                          ["latest_price", "午间最新价"],
                          ["name", "名称"],
                        ].map(([value, label]) => (
                          <option key={value} value={value}>
                            {label}
                          </option>
                        ))}
                      </select>
                    </label>
                    <button
                      className="btn small"
                      onClick={() => {
                        setDirection(direction === "desc" ? "asc" : "desc");
                        setPage(1);
                      }}
                    >
                      {direction === "desc" ? "↓ 降序" : "↑ 升序"}
                    </button>
                    {query && (
                      <button
                        className="btn small"
                        onClick={() => setQuery("")}
                      >
                        清除搜索
                      </button>
                    )}
                  </div>
                  <div
                    className="market-members-board-filter noon-sector-board-filter"
                    role="group"
                    aria-label="午间成分股上市板块筛选"
                  >
                    <span>上市板块</span>
                    {[
                      ...NOON_BOARD_OPTIONS,
                      { value: "unknown", label: "待核验" },
                    ].map((option) => (
                      <button
                        key={option.value}
                        type="button"
                        className={board === option.value ? "active" : ""}
                        aria-pressed={board === option.value}
                        onClick={() => {
                          setBoard(option.value);
                          setPage(1);
                        }}
                      >
                        {option.label}
                      </button>
                    ))}
                    <small>
                      仅筛选本份成员；缺少午间报价不改变板别，未知板别在“全部”或“待核验”中保留。
                    </small>
                  </div>
                </div>
                <div
                  className="noon-sector-member-table"
                  aria-busy={result.loading}
                >
                  <Table
                    rows={data.items || []}
                    pageSize={50}
                    sorting="none"
                    emptyTitle={
                      data.state === "unknown"
                        ? "午间成员尚未核验"
                        : "本次筛选暂无成员"
                    }
                    emptyDescription={
                      data.unavailable_reason ||
                      "仅显示本份已留存成员；缺少午间报价时保持未知。"
                    }
                    onStock={
                      data.formal_chart_as_of && onStock
                        ? (id) => onStock(id, data.formal_chart_as_of)
                        : undefined
                    }
                    columns={[
                      { key: "name", title: "股票 / 代码" },
                      {
                        key: "board",
                        title: "上市板块",
                        render: (value) => (
                          <Badge
                            tone={value === "unknown" ? "warn" : "neutral"}
                          >
                            {boardLabel(value)}
                          </Badge>
                        ),
                      },
                      {
                        key: "change_pct",
                        title: "午间涨幅",
                        render: (value, row) => (
                          <NoonStockChange
                            value={value}
                            state={
                              row.current_events?.length === 1
                                ? row.current_events[0]
                                : null
                            }
                          />
                        ),
                      },
                      {
                        key: "latest_price",
                        title: "午间最新价",
                        render: (value) => num(value),
                      },
                      {
                        key: "amount",
                        title: "午间成交额",
                        render: (value) => amount(value),
                      },
                      {
                        key: "current_events",
                        title: "本次来源池",
                        render: (value) =>
                          value?.length
                            ? value
                                .map((key: string) => eventLabels[key])
                                .join(" / ")
                            : "—",
                      },
                    ]}
                  />
                </div>
                <div className="table-footer noon-sector-pagination">
                  <span>
                    {data.state === "unknown"
                      ? "成员范围待核验，匹配数量未知"
                      : `匹配 ${data.matched} 只 · 第 ${data.page} / ${data.pages} 页`}
                  </span>
                  <button
                    className="btn small"
                    disabled={data.page <= 1 || result.loading}
                    onClick={() => setPage(data.page - 1)}
                  >
                    上一页
                  </button>
                  <button
                    className="btn small"
                    disabled={data.page >= data.pages || result.loading}
                    onClick={() => setPage(data.page + 1)}
                  >
                    下一页
                  </button>
                </div>
                <p className="panel-footnote">{data.notice}</p>
              </>
            )}
          </>
        )
      )}
    </div>
  );
  return embedded ? (
    content
  ) : (
    <Modal
      title={`${topic.name} · ${view === "chart" ? "板块K线" : "成分股"} · 午间快照`}
      onClose={onClose!}
      wide
      escapeUnderStock
    >
      {content}
    </Modal>
  );
}

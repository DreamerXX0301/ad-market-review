import "./AuctionTables.css";
import AuctionCopyText from "./AuctionCopyText";
import { useMemo, useState } from "react";
import { ArrowDown, ArrowUp, ArrowUpDown, Search } from "lucide-react";
import AuctionExplorerSelect from "./AuctionExplorerSelect";
import { useApi } from "../../lib/api";
import type { Row } from "../../lib/api";
import { Empty, ErrorBox, Loading, Panel } from "../../components/UI";
import { integer, num, percent, tone } from "../../lib/format";
import { auctionSortRows } from "./auctionPresentation";
import {
  auctionExplorerGroups,
  auctionGroupIdentity,
  auctionPeerObservation,
  auctionVerifiedPreviousHeight,
  auctionFocusedDirections,
} from "./auctionExplorerPresentation";
import AuctionStockTiles from "./AuctionStockTiles";
import "./AuctionBoardExplorer.css";

export default function AuctionBoardExplorer({
  date,
  quotes,
  onStock,
  chartDate,
  result,
}: {
  date: string;
  quotes: Row[];
  onStock?: (id: string) => void;
  chartDate?: string;
  result: {
    data: Row | null;
    loading: boolean;
    error: string;
    reload: () => void;
  };
}) {
  const resonance = useApi<Row>(
    `/market/auction/resonance?date=${encodeURIComponent(date)}`,
  );
  const [kind, setKind] = useState("all"),
    [mode, setMode] = useState("focus");
  const [query, setQuery] = useState(""),
    [sort, setSort] = useState("top_count");
  const [selectedKey, setSelectedKey] = useState("");
  const [direction, setDirection] = useState<"asc" | "desc">("desc");
  const [anchorSelection, setAnchorSelection] = useState({ date, id: "" });
  const [peerMode, setPeerMode] = useState("peers");
  const [expanded, setExpanded] = useState({ key: "", count: 20 });
  const anchorId = anchorSelection.date === date ? anchorSelection.id : "";
  const anchor = quotes.find((row) => row.instrument_id === anchorId);
  const groups = useMemo(
    () => auctionExplorerGroups(result.data, resonance.data, date, quotes),
    [result.data, resonance.data, date, quotes],
  );
  const filtered = groups.filter(
    (row) =>
      (kind === "all" || row.kind === kind) &&
      (!anchor || row.instrument_ids?.includes(anchorId)) &&
      (!query.trim() ||
        `${row.name} ${row.topic_code}`
          .toLowerCase()
          .includes(query.trim().toLowerCase())) &&
      (mode !== "resonance" || (row.sample_count >= 3 && row.top_count >= 2)),
  );
  const focused = auctionFocusedDirections(filtered);
  const eligible = mode === "focus" ? focused.groups : filtered;
  const sorted =
    sort === "name"
      ? [...eligible].sort(
          (a, b) =>
            String(a.name).localeCompare(String(b.name), "zh-CN") *
            (direction === "asc" ? 1 : -1),
        )
      : auctionSortRows(eligible, sort, direction);
  const expansionKey = JSON.stringify([
    date,
    kind,
    mode,
    query,
    anchorId,
    sort,
    direction,
  ]);
  const count = expanded.key === expansionKey ? expanded.count : 20;
  const shown =
    mode === "resonance"
      ? sorted.slice(0, 15)
      : mode === "focus"
        ? sorted.slice(0, count)
        : sorted;
  const selected =
    shown.find((row) => auctionGroupIdentity(row) === selectedKey) || shown[0];
  const peers =
    selected && anchor
      ? auctionPeerObservation(selected, quotes, anchorId)
      : null;
  const visibleIds =
    peers && peerMode !== "all"
      ? new Set<string>(
          peerMode === "lower"
            ? peers.lower_up_ids || []
            : peers.instrument_ids,
        )
      : undefined;
  function selectSort(key: string) {
    setDirection(sort === key && direction === "desc" ? "asc" : "desc");
    setSort(key);
  }
  return (
    <Panel
      className="auction-board-explorer"
      title="竞价板块与个股"
      subtitle="按行业、概念比较竞价表现，结合昨日梯队查看个股。"
    >
      <div className="auction-explorer-body">
        <div className="auction-explorer-controls">
          <label>
            分类
            <AuctionExplorerSelect
              aria-label="分类"
              value={kind}
              onChange={(e) => setKind(e.target.value)}
            >
              <option value="all">行业与概念</option>
              <option value="industry">行业</option>
              <option value="concept">概念</option>
            </AuctionExplorerSelect>
          </label>
          <label>
            范围
            <AuctionExplorerSelect
              aria-label="范围"
              value={mode}
              onChange={(e) => {
                setMode(e.target.value);
                setSort(
                  e.target.value === "resonance"
                    ? "concentration_lift"
                    : e.target.value === "focus"
                      ? "top_count"
                      : "sample_count",
                );
              }}
            >
              <option value="focus">聚焦方向</option>
              <option value="all">全部关联</option>
              <option value="resonance">共振候选前15组</option>
            </AuctionExplorerSelect>
          </label>
          <label>
            排序
            <AuctionExplorerSelect
              aria-label="排序"
              value={sort}
              onChange={(e) => setSort(e.target.value)}
            >
              <option value="sample_count">原样本数</option>
              <option value="previous_max_height">昨日最高板</option>
              <option value="average_auction_pct">竞价均幅</option>
              <option value="top_count">前20命中</option>
              <option value="concentration_lift">集中倍数</option>
              <option value="median_auction_pct">竞价中位数</option>
              <option value="up_ratio">高开占比</option>
              <option value="name">名称</option>
            </AuctionExplorerSelect>
          </label>
          <button
            type="button"
            className="btn auction-explorer-order"
            onClick={() => setDirection(direction === "desc" ? "asc" : "desc")}
            aria-label="切换板块排序方向"
          >
            {direction === "desc" ? (
              <ArrowDown size={14} />
            ) : (
              <ArrowUp size={14} />
            )}
            {direction === "desc" ? "降序" : "升序"}
          </button>
          <label className="auction-explorer-search">
            板块搜索
            <span className="auction-explorer-search-field">
              <Search size={14} aria-hidden="true" />
              <input
                type="search"
                aria-label="板块搜索"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="行业、概念名称或代码"
              />
            </span>
          </label>
        </div>
        <div className="auction-explorer-scope" role="status">
          <span>
            {mode === "focus"
              ? `聚焦多股关联方向：${focused.groups.length}个；已收起${focused.attribute_count}个通用属性、${focused.small_count}个不足3只候选的方向。`
              : mode === "all"
                ? `全部关联 ${filtered.length}个方向，包含通用属性和单股关联。`
                : "共振候选按所选排序展示前15组。"}
          </span>
          {mode !== "all" && (
            <button className="btn small" onClick={() => setMode("all")}>
              显示全部关联
            </button>
          )}
        </div>
        {(result.error || resonance.error) && (
          <ErrorBox
            message={result.error || resonance.error}
            retry={() => {
              result.reload();
              resonance.reload();
            }}
          />
        )}
        {result.loading && !result.data && <Loading />}
        <div className="auction-explorer-anchor">
          <label>
            <strong>同题材跟随观察</strong>
            <AuctionExplorerSelect
              aria-label="观察锚股"
              value={anchor?.instrument_id || ""}
              onChange={(e) => {
                setAnchorSelection({ date, id: e.target.value });
                setMode("focus");
                setQuery("");
                setKind("all");
                setPeerMode("peers");
              }}
            >
              <option value="">选择观察锚股，查看同方向个股</option>
              {quotes.map((row) => (
                <option key={row.instrument_id} value={row.instrument_id}>
                  {row.name || row.instrument_id} · {row.instrument_id}
                </option>
              ))}
            </AuctionExplorerSelect>
          </label>
          <p>
            {anchor
              ? `${anchor.name || anchorId} · ${auctionVerifiedPreviousHeight(anchor) == null ? "昨日板高待核验" : `昨日${auctionVerifiedPreviousHeight(anchor)}板`} · 竞价${percent(anchor.state === "observed" ? anchor.auction_pct : null)}。已限定为该股关联方向。`
              : "从候选中选择你要观察的一字板或前排股，比较同题材、同概念的其他候选。"}{" "}
            手选仅作观察基准；本份快照缺少今日涨停价与封板时序，今日一字状态未核验。
          </p>
        </div>
        {selected ? (
          <div className="auction-explorer-layout">
            <div
              className="auction-explorer-directions"
              aria-label="竞价板块列表"
            >
              <table className="auction-explorer-direction-table">
                <thead>
                  <tr>
                    {[
                      ["name", "方向"],
                      ["sample_count", "样本"],
                      ["previous_max_height", "昨日最高"],
                      ["average_auction_pct", "均幅"],
                    ].map(([key, label]) => (
                      <th
                        key={key}
                        aria-sort={
                          sort === key
                            ? direction === "desc"
                              ? "descending"
                              : "ascending"
                            : "none"
                        }
                      >
                        <button
                          className="auction-explorer-sort-button"
                          type="button"
                          onClick={() => selectSort(key)}
                          aria-label={`按${label}排序`}
                        >
                          <span>{label}</span>
                          {sort === key ? (
                            direction === "desc" ? (
                              <ArrowDown size={12} aria-hidden="true" />
                            ) : (
                              <ArrowUp size={12} aria-hidden="true" />
                            )
                          ) : (
                            <ArrowUpDown size={12} aria-hidden="true" />
                          )}
                        </button>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {shown.map((group) => (
                    <tr
                      className={
                        auctionGroupIdentity(group) ===
                        auctionGroupIdentity(selected)
                          ? "is-selected"
                          : ""
                      }
                      key={auctionGroupIdentity(group)}
                      onClick={() =>
                        setSelectedKey(auctionGroupIdentity(group))
                      }
                    >
                      <td>
                        <button
                          type="button"
                          onClick={() =>
                            setSelectedKey(auctionGroupIdentity(group))
                          }
                          aria-pressed={
                            auctionGroupIdentity(group) ===
                            auctionGroupIdentity(selected)
                          }
                        >
                          <strong>{group.name}</strong>
                          <small>
                            {group.kind === "industry" ? "行业" : "概念"}
                          </small>
                        </button>
                      </td>
                      <td>
                        {group.sample_count}
                        <small>有效{group.valid_count}</small>
                      </td>
                      <td
                        title={`原候选内已核验高度；${group.previous_height_unknown_count}只高度未核验，昨日炸板另列；不代表全板块最高。`}
                      >
                        <b className="auction-explorer-height">
                          {group.previous_max_height == null
                            ? "—"
                            : `${group.previous_max_height}板`}
                        </b>
                        <small>
                          {group.previous_max_height == null
                            ? "待核验"
                            : group.previous_height_unknown_count
                              ? `缺${group.previous_height_unknown_count}只`
                              : "已核验"}
                        </small>
                      </td>
                      <td>
                        <b className={tone(group.average_auction_pct)}>
                          {percent(group.average_auction_pct)}
                        </b>
                        <small>前20: {integer(group.top_count)}</small>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {mode === "focus" && shown.length < sorted.length && (
                <button
                  className="btn auction-explorer-more"
                  onClick={() =>
                    setExpanded({ key: expansionKey, count: count + 20 })
                  }
                >
                  再显示 {Math.min(20, sorted.length - shown.length)} 个方向 ·
                  还有 {sorted.length - shown.length} 个
                </button>
              )}
            </div>
            <section
              className="auction-explorer-stocks"
              aria-label="所选板块个股"
            >
              <header>
                <div>
                  <div className="auction-explorer-selection-heading">
                    <h3>{selected.name}</h3>
                    <AuctionCopyText
                      text={selected.topic_code}
                      label="复制板块代码"
                      visibleLabel="复制代码"
                    />
                  </div>
                  <p>
                    {selected.source} · {selected.topic_code} · 成员档案{" "}
                    {selected.members_observed_date || "待核验"} · 竞价 {date}
                  </p>
                </div>
                <div className="auction-explorer-selection-actions">
                  <span>{shown.length} 个可见方向</span>
                  <AuctionCopyText
                    text={(selected.instrument_ids || [])
                      .map(
                        (id: string) =>
                          `${quotes.find((row) => row.instrument_id === id)?.name || id} ${id.split(".")[0]}`,
                      )
                      .join("\n")}
                    label="复制本组股票"
                    visibleLabel="复制本组"
                  />
                </div>
              </header>
              <p className="auction-explorer-height-note">
                昨日样本已知最高{" "}
                <b>
                  {selected.previous_max_height == null
                    ? "待核验"
                    : `${selected.previous_max_height}板`}
                </b>{" "}
                · {selected.previous_height_known_count}只已核验连板高度 ·{" "}
                {selected.previous_height_unknown_count}
                只高度待核验；不包含今日晋级。
              </p>
              <div className="auction-explorer-metrics">
                <span>
                  原候选<b>{selected.sample_count}只</b>
                </span>
                <span>
                  有效报价<b>{selected.valid_count}只</b>
                </span>
                <span>
                  竞价均幅
                  <b className={tone(selected.average_auction_pct)}>
                    {percent(selected.average_auction_pct)}
                  </b>
                </span>
                <span>
                  集中倍数
                  <b>
                    {selected.concentration_lift == null
                      ? "—"
                      : `${num(selected.concentration_lift)}×`}
                  </b>
                </span>
                <span>
                  中位数
                  <b className={tone(selected.median_auction_pct)}>
                    {percent(selected.median_auction_pct)}
                  </b>
                </span>
                <span>
                  高开占比
                  <b>
                    {selected.up_ratio == null
                      ? "—"
                      : `${num(selected.up_ratio * 100)}%`}
                  </b>
                </span>
              </div>
              {peers && (
                <div
                  className="auction-explorer-peers"
                  aria-label="剔除锚股后的跟随观察"
                >
                  <strong>
                    剔除 {anchor?.name || anchorId} 后 · 同方向 {peers.count}只
                  </strong>
                  <div className="auction-explorer-metrics">
                    <span>
                      高 / 平 / 低开
                      <b>
                        {peers.up_count} / {peers.flat_count} /{" "}
                        {peers.down_count}
                      </b>
                    </span>
                    <span>
                      跟随样本中位数
                      <b className={tone(peers.median_auction_pct)}>
                        {percent(peers.median_auction_pct)}
                      </b>
                    </span>
                    <span>
                      高开占有效报价
                      <b>
                        {peers.up_ratio == null
                          ? "—"
                          : `${num(peers.up_ratio * 100)}%`}
                      </b>
                    </span>
                  </div>
                  <p>
                    有效报价 {peers.valid_count}/{peers.count}只 · 缺报{" "}
                    {peers.missing_count}只。昨日首板 {peers.first_count} · 二板{" "}
                    {peers.second_count} · 三板及以上 {peers.higher_count} ·
                    炸板 {peers.broken_count} · 其他/未核验 {peers.other_count}
                    。
                  </p>
                  <p>
                    {peers.anchor_height == null
                      ? "锚股昨日板高待核验，暂不比较高低梯队。"
                      : `低于锚股昨日${peers.anchor_height}板：${peers.lower_count}只，其中有效报价${peers.lower_valid_count}只、高开${peers.lower_up_ids.length}只。`}{" "}
                    这里只比较已核验连板高度；不代表股价低位、带动因果或冲板概率。
                  </p>
                  <div
                    className="auction-explorer-peer-filters"
                    aria-label="跟随个股范围"
                  >
                    {[
                      ["all", "本组全部"],
                      ["peers", "剔除锚股"],
                      ["lower", "较低板高且高开"],
                    ].map(([key, label]) => (
                      <button
                        type="button"
                        key={key}
                        className="btn small"
                        aria-pressed={peerMode === key}
                        disabled={
                          key === "lower" && peers.anchor_height == null
                        }
                        onClick={() => setPeerMode(key)}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                </div>
              )}
              <AuctionStockTiles
                key={`${date}:${auctionGroupIdentity(selected)}:${anchorId}`}
                ids={selected.instrument_ids || []}
                quotes={quotes}
                onStock={onStock}
                chartDate={chartDate}
                topIds={new Set(selected.resonance?.top_instrument_ids || [])}
                explorer={{
                  date,
                  topics: result.data,
                  visibleIds,
                  anchorId,
                  group: selected,
                }}
              />
            </section>
          </div>
        ) : (
          !result.loading && (
            <Empty
              title="当前没有可展示的方向"
              description="可清空筛选；来源缺失或不一致时保持待核验。"
            />
          )
        )}
        <p className="auction-topic-note">
          仅比较本份冻结候选，未采集的同板块股票不计入。一股可归属多个方向，组数不可相加作为去重股票数。共振要求原候选至少3只、有效竞价前20命中至少2只；先排序再取前15。缺失报价不补0。
        </p>
      </div>
    </Panel>
  );
}

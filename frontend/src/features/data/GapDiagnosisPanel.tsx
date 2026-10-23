import { useState } from "react";
import { ChevronRight, FileSearch, Search } from "lucide-react";
import type { Row } from "../../lib/api";
import { dateTime, integer } from "../../lib/format";
import { Badge, Empty } from "../../components/UI";
import CopyStockCode from "../../components/CopyStockCode";
import { filterGapDiagnosisStocks, gapEpisodeView } from "./dataPresentation";

export function GapDiagnosisOverview({
  diagnosis,
  onOpen,
}: {
  diagnosis: Row;
  onOpen?: () => void;
}) {
  return (
    <div className="coverage-gap-overview">
      <span className="coverage-gap-overview-icon">
        <FileSearch size={21} />
      </span>
      <div>
        <strong>日K与分析行情 · 共用缺口诊断</strong>
        <p>
          {integer(diagnosis.total_missing_sessions)} 个证券交易日无报价，归并为{" "}
          {integer(diagnosis.stock_count)} 只股票、
          {integer(diagnosis.episode_count)}{" "}
          个连续区间；两个模块引用同一份结果，不重复计算缺口。
        </p>
      </div>
      {onOpen && (
        <button className="btn small" onClick={onOpen}>
          查看诊断与下一步
          <ChevronRight size={14} />
        </button>
      )}
    </div>
  );
}

export function GapEpisode({ episode }: { episode: Row }) {
  const [showDates, setShowDates] = useState(false);
  const view = gapEpisodeView(episode);
  return (
    <article className="coverage-gap-episode">
      <div className="coverage-gap-episode-heading">
        <strong>
          {episode.start_date}{" "}
          {episode.end_date !== episode.start_date
            ? `→ ${episode.end_date}`
            : ""}
        </strong>
        <span>{integer(episode.missing_sessions)} 个交易日</span>
        <Badge>{view.label}</Badge>
      </div>
      <dl className="coverage-gap-conclusion">
        <div>
          <dt>核验结论</dt>
          <dd>{view.reason}</dd>
        </div>
        <div>
          <dt>影响</dt>
          <dd>{view.impact}</dd>
        </div>
        <div>
          <dt>下一步</dt>
          <dd>{view.nextAction}</dd>
        </div>
      </dl>
      {view.source && (
        <div className="coverage-source-episode">
          <strong>
            {view.source.source || "来源"} · 已核验{" "}
            {dateTime(view.source.checked_at_ms)}
          </strong>
          <span>
            返回有效报价{" "}
            {integer(
              Array.isArray(view.source.returned_dates)
                ? view.source.returned_dates.length
                : null,
            )}{" "}
            日 · 仍无报价{" "}
            {integer(
              Array.isArray(view.source.missing_dates)
                ? view.source.missing_dates.length
                : null,
            )}{" "}
            日 · 本地对比差异{" "}
            {integer(
              Array.isArray(view.source.local_difference_dates)
                ? view.source.local_difference_dates.length
                : null,
            )}{" "}
            日
          </span>
          <small>
            来源未返回报价不等于已确认停牌；实际价格修复仍需单独发布核验。
          </small>
        </div>
      )}
      <div className="coverage-gap-evidence">
        <span>前一实际报价 {episode.previous_quote_date || "未留存"}</span>
        <span>后一实际报价 {episode.next_quote_date || "未留存"}</span>
        <span>
          {episode.shape_label || "区间形态待核验"} · 形态不代表停牌原因
        </span>
      </div>
      <details
        className="coverage-gap-dates-details"
        onToggle={(event) => setShowDates(event.currentTarget.open)}
      >
        <summary>
          查看 {integer(episode.missing_sessions)} 个交易日与本地证据
        </summary>
        <p>{episode.source_comparison?.basis || "本地来源包尚未逐日比对。"}</p>
        <p>
          本地原始有效报价{" "}
          {integer(episode.source_comparison?.raw_available_sessions)} 日 ·
          本地原始包同样无报价{" "}
          {integer(episode.source_comparison?.raw_absent_sessions)} 日
        </p>
        {showDates && (
          <div className="coverage-gap-dates">
            {(Array.isArray(episode.dates) ? episode.dates : []).map(
              (date: string) => (
                <time key={date}>{date}</time>
              ),
            )}
          </div>
        )}
      </details>
    </article>
  );
}

export function GapStock({ stock }: { stock: Row }) {
  const [opened, setOpened] = useState(false);
  const episodes: Row[] = Array.isArray(stock.episodes) ? stock.episodes : [];
  const texts = (field: string) => [
    ...new Set(
      episodes
        .map((episode) => {
          const view = gapEpisodeView(episode);
          return field === "type_label"
            ? view.label
            : field === "reason"
              ? view.reason
              : field === "impact"
                ? view.impact
                : field === "next_action"
                  ? view.nextAction
                  : episode[field];
        })
        .filter(
          (value): value is string =>
            typeof value === "string" && value.length > 0,
        ),
    ),
  ];
  const labels = texts("type_label"),
    reasons = texts("reason"),
    impacts = texts("impact"),
    actions = texts("next_action");
  const brief = (values: string[], fallback: string) =>
    values.length === 1
      ? values[0]
      : values.length
        ? `${values[0]}（另有${values.length - 1}类，展开查看）`
        : fallback;
  return (
    <details
      className="coverage-gap-stock"
      onToggle={(event) => setOpened(event.currentTarget.open)}
    >
      <summary>
        <div className="coverage-gap-stock-name">
          <strong>{stock.name || stock.instrument_id}</strong>
          <CopyStockCode code={stock.code} instrumentId={stock.instrument_id} />
        </div>
        <span>
          共 {integer(stock.missing_sessions)} 日 ·{" "}
          {integer(stock.episode_count)} 段 · 最长{" "}
          {integer(stock.longest_episode_sessions)} 日
        </span>
        <Badge>
          {labels.length === 1
            ? labels[0]
            : labels.length
              ? `${labels.length}类本地核验结论`
              : "原因待核验"}
        </Badge>
        <small>展开区间与日期</small>
        <div className="coverage-gap-stock-brief">
          <p>
            <b>结论</b>
            {brief(reasons, "暂缺可确认的原因证据")}
          </p>
          <p>
            <b>影响</b>
            {brief(impacts, "保留严格日线信号与收益核验缺口")}
          </p>
          <p>
            <b>下一步</b>
            {brief(actions, "请先核验本地报告，再按来源证据处理")}
          </p>
        </div>
      </summary>
      {opened && (
        <div className="coverage-gap-stock-episodes">
          {episodes.map((episode) => (
            <GapEpisode
              key={episode.episode_id || episode.start_date}
              episode={episode}
            />
          ))}
        </div>
      )}
    </details>
  );
}

export default function GapDiagnosisPanel({
  daily,
  stale = false,
}: {
  daily?: Row;
  stale?: boolean;
}) {
  const [query, setQuery] = useState(""),
    [sort, setSort] = useState("missing_sessions"),
    [limit, setLimit] = useState(25);
  const diagnosis = daily?.gap_diagnosis;
  const rows = filterGapDiagnosisStocks(diagnosis, query, sort);
  const classifications = new Map<
    string,
    { label: string; episodes: number; sessions: number }
  >();
  for (const stock of diagnosis?.stocks || [])
    for (const episode of stock.episodes || []) {
      const key =
        episode.source_verification?.status ||
        episode.diagnosis_type ||
        "unknown";
      const item = classifications.get(key) || {
        label: gapEpisodeView(episode).label,
        episodes: 0,
        sessions: 0,
      };
      item.episodes++;
      if (
        Number.isSafeInteger(episode.missing_sessions) &&
        episode.missing_sessions >= 0
      )
        item.sessions += episode.missing_sessions;
      classifications.set(key, item);
    }
  if (!daily?.ready)
    return (
      <p className="muted">
        先运行“核验本地数据”，生成当前代际逐股报价范围与连续缺口诊断；不会联网或补造K线。
      </p>
    );
  if (!diagnosis)
    return (
      <div className="coverage-plan-caveat">
        <strong>请更新本地诊断报告</strong>
        <p>
          当前报告记录 {integer(daily.unquoted_sessions)}{" "}
          个证券交易日无报价；重新“核验本地数据”后可按股票与连续区间查看原因、影响和下一步，不再逐个翻日期。
        </p>
      </div>
    );
  return (
    <div className="coverage-gap-diagnosis">
      <GapDiagnosisOverview diagnosis={diagnosis} />
      {diagnosis.source_verification_stale === true && (
        <p className="coverage-plan-caveat">
          已有来源核验属于旧行情或旧诊断，未用于当前结论；请按当前计划重新核验。
        </p>
      )}
      {stale && (
        <p className="coverage-plan-caveat">
          行情、来源或上市身份已更新，以下为上次核验结果；请重新“核验本地数据”再按新范围处理。
        </p>
      )}
      <div className="coverage-diagnosis-stats">
        <div>
          <span>无报价股票</span>
          <strong>{integer(diagnosis.stock_count)}</strong>
        </div>
        <div>
          <span>连续区间</span>
          <strong>{integer(diagnosis.episode_count)}</strong>
        </div>
        <div>
          <span>单日缺口</span>
          <strong>{integer(diagnosis.isolated_episode_count)}</strong>
        </div>
        <div>
          <span>较长连续区间</span>
          <strong>
            {integer(diagnosis.long_episode_count)}
            <small>至少5个市场交易日</small>
          </strong>
        </div>
      </div>
      <div className="coverage-gap-type-summary">
        {[...classifications.entries()].map(([key, value]) => (
          <div key={key}>
            <strong>{value.label}</strong>
            <span>
              {integer(value.episodes)} 段 · {integer(value.sessions)} 日
            </span>
          </div>
        ))}
      </div>
      <p className="muted">
        {diagnosis.notice ||
          "本地包同样无报价只能说明留存来源未返回该日记录；没有明确停牌证据时，原因仍待确认。"}
      </p>
      {diagnosis.details_complete === false && (
        <p className="coverage-plan-caveat">
          统计总数覆盖完整核验范围；当前详情仅列出{" "}
          {integer(diagnosis.reported_missing_sessions)}{" "}
          个无报价日期，未列部分不当作已补齐。
        </p>
      )}
      <div className="coverage-gap-toolbar">
        <label className="coverage-gap-search">
          <Search size={15} />
          <input
            aria-label="搜索缺口股票日期或原因"
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setLimit(25);
            }}
            placeholder="股票 / 代码 / 日期 / 诊断原因"
          />
        </label>
        <label>
          排序
          <select
            aria-label="缺口股票排序"
            value={sort}
            onChange={(event) => {
              setSort(event.target.value);
              setLimit(25);
            }}
          >
            <option value="missing_sessions">无报价交易日最多</option>
            <option value="longest_episode_sessions">最长连续区间</option>
            <option value="episode_count">缺口区间最多</option>
          </select>
        </label>
        <span>
          匹配 {integer(rows.length)} / {integer(diagnosis.stock_count)} 只
        </span>
      </div>
      <div className="coverage-gap-stock-list">
        {rows.slice(0, limit).map((stock) => (
          <GapStock stock={stock} key={stock.instrument_id} />
        ))}
      </div>
      {!rows.length && (
        <Empty
          title="没有匹配的缺口股票"
          description="可调整名称、代码、日期或原因后搜索。"
        />
      )}
      {rows.length > limit && (
        <button
          className="btn small"
          onClick={() => setLimit((old) => old + 25)}
        >
          继续显示25只 · 当前 {Math.min(limit, rows.length)} /{" "}
          {integer(rows.length)}
        </button>
      )}
    </div>
  );
}

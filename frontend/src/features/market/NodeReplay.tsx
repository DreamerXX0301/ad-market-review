import { useId, useState } from "react";
import { ChevronDown, RefreshCw } from "lucide-react";
import { Badge, Empty, ErrorBox, Loading, Warning } from "../../components/UI";
import { useApi } from "../../lib/api";
import { num, percent, tone } from "../../lib/format";
import NodeReplayTopics, {
  ReplayOriginalReason,
  ReplayStockIdentity,
} from "./NodeReplayTopics";
import type { ReasonMatch, TopicLadders } from "./NodeReplayTopics";
import { intradayReadingProps } from "./intradayReadingNavigation";
import "./NodeReplay.css";

type Progress = "advanced" | "broken" | "unknown";
type Attribution = {
  basis: "limit_up_reason" | "membership_fallback" | "manual" | "unknown";
  automatic?: Pick<Attribution, "labels"> | null;
  labels: string[];
  reason: string | null;
  observed_date: string | null;
  reason_match?: ReasonMatch;
};
type Anchor = {
  instrument_id: string;
  name: string;
  attribution?: Attribution;
  height: number;
  state: Progress;
  change_pct: number | null;
  previous_volume_ratio: number | null;
  current_volume_ratio: number | null;
  previous_board_shape: "one_price" | "not_one_price" | "unknown";
  reason?: string | null;
};
type Cohort = {
  height: number;
  count: number;
  members: {
    instrument_id: string;
    name: string;
    attribution?: Attribution;
    feedback: { date: string; state: Progress }[];
  }[];
  feedback: {
    date: string;
    advanced: number;
    broken: number;
    unknown: number;
  }[];
};

const nodeLabels = {
  highest_broken: "前日最高梯队全部断板",
  highest_diverged: "前日最高梯队分化",
  highest_advanced: "前日最高梯队延续",
  unknown: "前日最高梯队待核验",
  no_highest: "无可比较的前日梯队",
};
const shapeLabels = {
  one_price: "一字涨停",
  not_one_price: "非一字涨停",
  unknown: "待核验",
};
const progressLabels = { advanced: "晋级", broken: "断板", unknown: "待核验" };

type ReplayGroup = {
  regime: "main_10";
  state: keyof typeof nodeLabels;
  previous_height: number | null;
  anchors: Anchor[];
  secondary_height: number | null;
  secondary: Anchor[];
  reason: string | null;
  cohorts: Cohort[];
};
type Replay = {
  date: string;
  as_of: string;
  latest_date: string | null;
  previous_date: string | null;
  snapshot_id: string | null;
  state: "observed" | "partial" | "unknown";
  reason: string | null;
  groups: ReplayGroup[];
  topic_ladders?: TopicLadders;
  topic_adjustments_enabled?: boolean;
  sessions: string[];
  notice: string;
};
type OpenStock = (id: string) => void;

const boardLabel = (height: number) => (height === 1 ? "首板" : `${height} 板`);
const multiplier = (value: number | null) =>
  value == null ? "—" : `${num(value)} 倍`;
const startingHeights = [1, 2, 3];

function AttributionHeading({
  label,
  date,
}: {
  label: string;
  date: string | null;
}) {
  return (
    <th scope="col">
      {label}
      {date && (
        <small className="node-replay-topic-source-date">
          <time dateTime={date}>{date}</time>
        </small>
      )}
    </th>
  );
}

function AttributionCell({ attribution }: { attribution?: Attribution }) {
  const basis = attribution?.basis;
  const reasonLabel =
    attribution?.reason_match === "related" ? "原因关联" : "原因归类";
  const source =
    basis === "limit_up_reason"
      ? reasonLabel
      : basis === "membership_fallback"
        ? "成员补充"
        : basis === "manual"
          ? "手工归类"
          : "归类待核验";
  const label = basis !== "unknown" ? attribution?.labels[0] : undefined;
  const observedDate =
    basis === "membership_fallback" ? attribution?.observed_date : null;
  return (
    <td className="node-replay-attribution">
      {label && <strong>{label}</strong>}
      <small className="node-replay-note">
        {source}
        {observedDate && (
          <>
            {" "}
            · 依据日期 <time dateTime={observedDate}>{observedDate}</time>
          </>
        )}
      </small>
      {basis === "manual" && attribution?.automatic?.labels[0] && (
        <small className="node-replay-note">
          自动归类 · {attribution.automatic.labels[0]}
        </small>
      )}
      <ReplayOriginalReason reason={attribution?.reason} />
    </td>
  );
}

function AnchorComparison({
  group,
  date,
  onStock,
}: {
  group: ReplayGroup;
  date: string | null;
  onStock: OpenStock;
}) {
  const rows = [
    ...group.anchors.map((row) => ({ row, tier: "最高" })),
    ...group.secondary.map((row) => ({ row, tier: "次高" })),
  ];
  return rows.length ? (
    <div
      className="table-wrap node-replay-scroll"
      tabIndex={0}
      role="region"
      aria-label="前日梯队量价对照"
    >
      <table
        className="node-replay-table node-replay-anchors"
        aria-label="前日梯队量价对照"
      >
        <thead>
          <tr>
            <th scope="col">前日梯队</th>
            <th scope="col">股票</th>
            <AttributionHeading label="前日归类" date={date} />
            <th scope="col">前日板型</th>
            <th scope="col" className="node-replay-number">
              前日成交量
            </th>
            <th scope="col" className="node-replay-number">
              节点日成交量
            </th>
            <th scope="col" className="node-replay-number">
              节点日涨跌
            </th>
            <th scope="col">节点日结果</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ row, tier }) => (
            <tr key={row.instrument_id}>
              <td>
                <span className="node-replay-tier">{tier}</span>{" "}
                {boardLabel(row.height)}
              </td>
              <th scope="row">
                <ReplayStockIdentity
                  id={row.instrument_id}
                  name={row.name}
                  onStock={onStock}
                />
              </th>
              <AttributionCell attribution={row.attribution} />
              <td>{shapeLabels[row.previous_board_shape]}</td>
              <td className="node-replay-number">
                {multiplier(row.previous_volume_ratio)}
              </td>
              <td className="node-replay-number">
                {multiplier(row.current_volume_ratio)}
              </td>
              <td className={`node-replay-number ${tone(row.change_pct)}`}>
                {percent(row.change_pct)}
              </td>
              <td>
                <span className={`node-replay-progress ${row.state}`}>
                  {progressLabels[row.state]}
                </span>
                {row.reason && (
                  <small className="node-replay-proof">{row.reason}</small>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  ) : (
    <p className="node-replay-note">当前盘面日没有可对照的普通主板高位样本。</p>
  );
}

function FollowUpCell({
  feedback,
  count,
}: {
  feedback?: Cohort["feedback"][number];
  count: number;
}) {
  if (!feedback || count <= 0)
    return <td className="node-replay-missing">—</td>;
  const { advanced, broken, unknown } = feedback;
  const low = num((100 * advanced) / count, 1);
  const high = num((100 * (advanced + unknown)) / count, 1);
  const rate = unknown ? `${low}%–${high}%` : `${low}%`;
  return (
    <td>
      <div className="node-replay-rate" aria-label={`连续晋级率${rate}`}>
        {rate}
      </div>
      <div className="node-replay-counts">
        <span className={advanced ? "advanced" : undefined}>晋 {advanced}</span>
        <span className={broken ? "broken" : undefined}>断 {broken}</span>
        <span className={unknown ? "unknown" : undefined}>待 {unknown}</span>
      </div>
    </td>
  );
}

function CohortRoster({
  cohort,
  sessions,
  date,
  onStock,
}: {
  cohort: Cohort;
  sessions: string[];
  date: string;
  onStock: OpenStock;
}) {
  return (
    <div
      className="table-wrap node-replay-scroll node-replay-roster"
      tabIndex={0}
      role="region"
      aria-label={`${boardLabel(cohort.height)}原始名单`}
    >
      <table className="node-replay-table node-replay-matrix">
        <thead>
          <tr>
            <th scope="col">{boardLabel(cohort.height)}原始名单</th>
            <AttributionHeading label="节点日归类" date={date} />
            {sessions.map((day) => (
              <th key={day} scope="col">
                <time dateTime={day}>{day.slice(5)}</time>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {cohort.members.map((member) => (
            <tr key={member.instrument_id}>
              <th scope="row">
                <ReplayStockIdentity
                  id={member.instrument_id}
                  name={member.name}
                  onStock={onStock}
                />
              </th>
              <AttributionCell attribution={member.attribution} />
              {sessions.map((day) => {
                const state =
                  member.feedback.find((item) => item.date === day)?.state ??
                  "unknown";
                return (
                  <td key={day}>
                    <span className={`node-replay-progress ${state}`}>
                      {progressLabels[state]}
                    </span>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CohortMatrix({
  cohorts,
  sessions,
  date,
  onStock,
}: {
  cohorts: Cohort[];
  sessions: string[];
  date: string;
  onStock: OpenStock;
}) {
  const [expandedHeight, setExpandedHeight] = useState<number | null>(null);
  const rosterId = useId();
  const selected = cohorts.find((cohort) => cohort.height === expandedHeight);
  return (
    <>
      <div
        className="table-wrap node-replay-scroll"
        tabIndex={0}
        role="region"
        aria-label="同批样本按反馈日对照"
      >
        <table
          className="node-replay-table node-replay-matrix"
          aria-label="同批样本按反馈日对照"
        >
          <thead>
            <tr>
              <th scope="col">起始板高 · 样本数</th>
              {sessions.map((day, index) => (
                <th key={day} scope="col">
                  <time dateTime={day}>{day.slice(5)}</time>
                  <small>第 {index + 1} 日</small>
                </th>
              ))}
              {!sessions.length && <th scope="col">后续交易日</th>}
            </tr>
          </thead>
          <tbody>
            {startingHeights.map((height) => {
              const cohort = cohorts.find((item) => item.height === height);
              const expanded = expandedHeight === height;
              return (
                <tr key={height}>
                  <th scope="row">
                    <div className="node-replay-cohort-label">
                      <strong>{boardLabel(height)}</strong>
                      <span>
                        {cohort ? `${cohort.count} 只` : "暂无已核验样本"}
                      </span>
                      {!!cohort?.members.length && (
                        <button
                          type="button"
                          className="node-replay-roster-toggle"
                          aria-label={`${expanded ? "收起" : "查看"}${boardLabel(height)}原始名单`}
                          aria-expanded={expanded}
                          aria-controls={rosterId}
                          onClick={() =>
                            setExpandedHeight(expanded ? null : height)
                          }
                        >
                          名单
                          <ChevronDown size={12} />
                        </button>
                      )}
                    </div>
                  </th>
                  {sessions.map((day) => (
                    <FollowUpCell
                      key={day}
                      feedback={cohort?.feedback.find(
                        (item) => item.date === day,
                      )}
                      count={cohort?.count ?? 0}
                    />
                  ))}
                  {!sessions.length && (
                    <td className="node-replay-missing">—</td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {!sessions.length && (
        <p className="node-replay-note">
          当前截止日内暂无后续反馈。选择更晚的截止日可比较。
        </p>
      )}
      <div id={rosterId} hidden={!selected}>
        {selected && (
          <CohortRoster
            cohort={selected}
            sessions={sessions}
            date={date}
            onStock={onStock}
          />
        )}
      </div>
    </>
  );
}

function GroupComparison({
  group,
  sessions,
  previousDate,
  date,
  topicLadders,
  adjustmentsEnabled,
  onStock,
}: {
  group: ReplayGroup;
  sessions: string[];
  previousDate: string | null;
  date: string;
  topicLadders?: TopicLadders;
  adjustmentsEnabled: boolean;
  onStock: OpenStock;
}) {
  const crossing =
    group.state === "highest_broken" &&
    group.secondary.some((item) => item.state === "advanced");
  return (
    <>
      <section
        {...intradayReadingProps("nodePrior")}
        className="node-replay-section"
      >
        <header className="node-replay-section-head">
          <h3>前日梯队对照</h3>
          <div className="node-replay-labels">
            <Badge tone={group.state === "unknown" ? "warn" : "neutral"}>
              {nodeLabels[group.state]}
            </Badge>
            {crossing && <Badge tone="warn">高标穿越候选</Badge>}
          </div>
        </header>
        {group.reason && <p className="node-replay-note">{group.reason}</p>}
        {crossing && (
          <p className="node-replay-note">
            最高梯队断板与次高梯队晋级在同日出现。后续延续仍待观察。
          </p>
        )}
        <AnchorComparison group={group} date={previousDate} onStock={onStock} />
        <p className="node-replay-note">成交量均为各自相对前一交易日的倍数。</p>
      </section>
      <NodeReplayTopics
        date={date}
        value={topicLadders}
        adjustmentsEnabled={adjustmentsEnabled}
        onStock={onStock}
      />
      <section
        {...intradayReadingProps("nodeFeedback")}
        className="node-replay-section"
      >
        <header className="node-replay-section-head">
          <h3>原名单与反馈</h3>
          <p className="node-replay-note">连续晋级率按起始名单计算</p>
        </header>
        <CohortMatrix
          cohorts={group.cohorts}
          sessions={sessions}
          date={date}
          onStock={onStock}
        />
        {sessions.length > 0 && (
          <p className="node-replay-note">
            晋＝连续晋级，断＝已断板，待＝待核验。存在待核验样本时显示晋级率区间。
          </p>
        )}
      </section>
    </>
  );
}

export default function NodeReplay({
  date,
  latestDate,
  onStock,
}: {
  date: string;
  latestDate?: string;
  onStock: OpenStock;
}) {
  const [asOf, setAsOf] = useState(date);
  const [draft, setDraft] = useState(date);
  const { data, error, loading, reload } = useApi<Replay>(
    date
      ? `/market/ladder/nodes?${new URLSearchParams({ date, as_of: asOf })}`
      : null,
  );
  const replay = data?.date === date && data.as_of === asOf ? data : null;
  const group = replay?.groups.find((item) => item.regime === "main_10");
  const latest = replay?.latest_date ?? latestDate ?? date;
  const validDraft =
    /^\d{4}-\d{2}-\d{2}$/.test(draft) && date <= draft && draft <= latest;
  function selectCutoff(value: string) {
    setDraft(value);
    setAsOf(value);
  }
  return (
    <>
      <section
        {...intradayReadingProps("nodeReplay")}
        className="node-replay-toolbar"
      >
        <header className="node-replay-toolbar-head">
          <div className="node-replay-labels">
            <h2>节点回放</h2>
            <Badge>普通主板（10%）</Badge>
          </div>
          <dl className="node-replay-dates">
            <div>
              <dt>前一交易日</dt>
              <dd>{replay?.previous_date || "—"}</dd>
            </div>
            <div>
              <dt>节点日</dt>
              <dd>{date || "—"}</dd>
            </div>
            <div>
              <dt>当前截止</dt>
              <dd>{asOf || "—"}</dd>
            </div>
          </dl>
          <button
            type="button"
            className="btn small"
            onClick={reload}
            aria-label="刷新节点回放"
            disabled={!date || loading}
          >
            <RefreshCw size={13} />
            刷新
          </button>
        </header>
        <form
          className="node-replay-controls"
          onSubmit={(event) => {
            event.preventDefault();
            if (validDraft) selectCutoff(draft);
          }}
        >
          <label>
            反馈截止日{" "}
            <input
              type="date"
              value={draft}
              min={date}
              max={latest}
              disabled={!date || latest <= date}
              onChange={(event) => setDraft(event.target.value)}
            />
          </label>
          <button
            type="submit"
            className="btn small"
            disabled={!validDraft || draft === asOf}
          >
            应用
          </button>
          {asOf !== date && (
            <button
              type="button"
              className="btn small"
              onClick={() => selectCutoff(date)}
            >
              回到节点日
            </button>
          )}
          {(asOf === date || asOf < latest) && (
            <button
              type="button"
              className="btn small"
              disabled={!date || latest <= asOf}
              onClick={() => selectCutoff(latest)}
            >
              查看后续
            </button>
          )}
        </form>
      </section>
      {error && <ErrorBox message={error} retry={reload} />}
      {loading && !replay && <Loading />}
      {!date && (
        <Empty
          title="暂无可用节点日"
          description="选择有已发布收盘行情的盘面日后查看。"
        />
      )}
      {replay?.reason && <Warning>{replay.reason}</Warning>}
      {replay && !group && (
        <Empty
          title="暂无可核验的普通主板样本"
          description="可选择其他盘面日查看。"
        />
      )}
      {group && replay && (
        <GroupComparison
          group={group}
          sessions={replay.sessions}
          previousDate={replay.previous_date}
          date={replay.date}
          topicLadders={replay.topic_ladders}
          adjustmentsEnabled={replay.topic_adjustments_enabled !== false}
          onStock={onStock}
        />
      )}
      {replay?.notice && (
        <p className="node-replay-note node-replay-footnote">{replay.notice}</p>
      )}
    </>
  );
}

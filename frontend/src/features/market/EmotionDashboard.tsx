import { useState } from "react";
import type { ReactNode } from "react";
import { Activity, ChartNoAxesCombined, Layers3 } from "lucide-react";
import type { Row } from "../../lib/api";
import { Badge } from "../../components/UI";
import { MetricHelp } from "./MetricHelp";
import MarketDataCoverage from "./MarketDataCoverage";
import SentimentSnapshots from "./SentimentSnapshots";
import {
  sentimentCount,
  sentimentCoverage,
  sentimentMetricView,
  sentimentReferences,
  sentimentPromotionBounds,
  sentimentKnownTime,
} from "./sentimentPresentation";
import "./EmotionDashboard.css";

function MetricList({ metrics }: { metrics: Row[] }) {
  return (
    <div className="sentiment-metrics">
      {metrics.map((metric, index) => {
        const view = sentimentMetricView(metric);
        return (
          <div className="sentiment-metric" key={metric.key || index}>
            <span className="sentiment-metric-label">
              {metric.label || "指标名称待核验"}
              {view.help && (
                <MetricHelp
                  label={metric.label || "观察指标"}
                  description={view.help}
                />
              )}
            </span>
            <strong className={view.tone}>
              {view.valueText}
              <small>{view.unit}</small>
            </strong>
            {!view.known ? (
              <span className="sentiment-metric-note">待核验</span>
            ) : view.reason ? (
              <span className="sentiment-metric-note" title={view.reason}>
                查看样本口径说明
              </span>
            ) : null}
          </div>
        );
      })}
      {!metrics.length && (
        <p className="sentiment-empty">该维度尚无可核验指标。</p>
      )}
    </div>
  );
}

function Dimension({
  dimension,
  kind,
  controls,
}: {
  dimension: Row;
  kind: "market" | "short_term";
  controls?: ReactNode;
}) {
  const metrics: Row[] = Array.isArray(dimension.metrics)
    ? dimension.metrics
    : [];
  const Icon = kind === "market" ? Activity : Layers3;
  return (
    <section className={"sentiment-dimension sentiment-dimension--" + kind}>
      <div className="sentiment-dimension-head">
        <span className="sentiment-dimension-icon">
          <Icon size={19} />
        </span>
        <h2>
          {dimension.label || (kind === "market" ? "市场赚钱效应" : "短线情绪")}
        </h2>
        <Badge>{dimension.state_label || "待核验"}</Badge>
      </div>
      {controls}
      {kind === "short_term" &&
        dimension.sample_count < dimension.observed_count && (
          <p className="sentiment-cohort-note">
            当前仅{sentimentCount(dimension.sample_count)} /{" "}
            {sentimentCount(dimension.observed_count)}
            只具有可比反馈，下列均值和比例不代表完整队列。
            {dimension.current_identity_unknown_count > 0
              ? `其中${sentimentCount(dimension.current_identity_unknown_count)}只今日制度身份待核验。`
              : ""}
          </p>
        )}
      <MetricList metrics={metrics} />
      {kind === "short_term" && (
        <div className="sentiment-promotion-range">
          <span>
            完整队列晋级范围{" "}
            <MetricHelp
              label="晋级范围"
              description="分母须为可证明的昨日完整队列。确认晋级数/N为下界，（确认晋级数＋今日状态未知数）/N为上界；分母未知时不计算。"
            />
          </span>
          <strong>
            {sentimentPromotionBounds(dimension.promotion_bounds_pct) ||
              "待核验"}
          </strong>
          <small>
            {dimension.promotion_bounds_reason ||
              dimension.identity_reason ||
              (dimension.cohort_source_complete === true
                ? "范围保留今日状态未知的影响，不用区间中点代替晋级率。"
                : "昨日完整分母或制度身份尚未核验。")}
          </small>
        </div>
      )}
      <div className="sentiment-sample">
        <span>
          可比 <b>{sentimentCount(dimension.sample_count)}</b> / 观察{" "}
          <b>{sentimentCount(dimension.observed_count)}</b>
        </span>
        <span>
          待核验 <b>{sentimentCount(dimension.unknown_count)}</b>
        </span>
        <span>
          收益可比率 <b>{sentimentCoverage(dimension.coverage_pct)}</b>
        </span>
      </div>
      <p className="sentiment-dimension-scope">
        {kind === "short_term" && dimension.previous_date && (
          <span>样本日 {dimension.previous_date} · </span>
        )}
        {dimension.scope || "样本范围待核验"}
      </p>
    </section>
  );
}

function ShortTermDimension({ dimension }: { dimension: Row }) {
  const [selected, setSelected] = useState("all");
  const groups: Row[] = Array.isArray(dimension.groups) ? dimension.groups : [];
  const choices = [
    { ...dimension, id: "all", group_label: "全部队列价格观察" },
    ...groups,
  ];
  const current = choices.find((group) => group.id === selected) || choices[0];
  return (
    <Dimension
      dimension={{ ...current, label: "短线接力" }}
      kind="short_term"
      controls={
        <div className="sentiment-cohort-controls" aria-label="选择接力制度组">
          {choices.map((group) => (
            <button
              key={group.id}
              aria-pressed={current.id === group.id}
              onClick={() => setSelected(group.id)}
            >
              {group.group_label || group.label || group.id}
            </button>
          ))}
        </div>
      }
    />
  );
}

function IndexEnvironment({ dimension }: { dimension: Row }) {
  const indices: Row[] = Array.isArray(dimension.indices)
    ? dimension.indices
    : [];
  return (
    <section className="sentiment-index-environment" aria-label="指数环境">
      <div className="sentiment-dimension-head">
        <span className="sentiment-dimension-icon">
          <ChartNoAxesCombined size={19} />
        </span>
        <h2>指数环境</h2>
        <Badge>{dimension.state_label || "窗口待核验"}</Badge>
      </div>
      <div className="sentiment-index-grid">
        {indices.map((index) => (
          <article className="sentiment-index-card" key={index.instrument_id}>
            <header>
              <div>
                <h3>{index.name || index.instrument_id}</h3>
                <span>{index.instrument_id}</span>
              </div>
              <Badge>{index.state_label || "待核验"}</Badge>
            </header>
            <MetricList
              metrics={Array.isArray(index.metrics) ? index.metrics : []}
            />
            <p className="sentiment-index-coverage">
              {index.date || "日期待核验"} · 有效
              {sentimentCount(index.observed_count)} / 所需
              {sentimentCount(index.sample_count)}日 ·{" "}
              {sentimentCoverage(index.coverage_pct)}
            </p>
          </article>
        ))}
      </div>
      {!indices.length && (
        <p className="sentiment-empty">尚无可核验的指数窗口。</p>
      )}
      <p className="sentiment-dimension-scope">
        {dimension.scope ||
          "按指数分别观察20日趋势与已实现波动；不合成全市场收益。"}
      </p>
    </section>
  );
}

function SentimentProvenance({ provenance }: { provenance: Row }) {
  const records: Row[] = Array.isArray(provenance.source_records)
    ? provenance.source_records
    : [];
  const snapshots: string[] = Array.isArray(provenance.input_snapshot_ids)
    ? provenance.input_snapshot_ids.filter(
        (id: unknown): id is string => typeof id === "string",
      )
    : [];
  return (
    <section className="sentiment-provenance" aria-label="复盘证据时间">
      <div>
        <Badge>
          {provenance.mode === "retained_retrospective"
            ? "当前留存回看"
            : "来源时点待核验"}
        </Badge>
        <span>
          规则 {provenance.rule_version || provenance.model || "未登记"}
        </span>
      </div>
      <p>
        {provenance.notice ||
          "交易日期不等于当时已知时间。仅按已留存材料回看，不代表历史当时已经发出信号。"}
      </p>
      <details>
        <summary>输入版本与来源时间 · {records.length}条记录</summary>
        <dl>
          <div>
            <dt>行情快照</dt>
            <dd>{snapshots.join("、") || "未登记"}</dd>
          </div>
          <div>
            <dt>输入指纹</dt>
            <dd>
              <code>{provenance.input_fingerprint || "未登记"}</code>
            </dd>
          </div>
          <div>
            <dt>历史首次可知</dt>
            <dd>{sentimentKnownTime(provenance.first_known_at)}</dd>
          </div>
        </dl>
        {!!records.length && (
          <div className="sentiment-source-table">
            <table>
              <thead>
                <tr>
                  <th>事件日期</th>
                  <th>来源 / 池</th>
                  <th>来源发布时间</th>
                  <th>实际获取时间</th>
                  <th>已登记首知时间</th>
                  <th>来源SHA</th>
                </tr>
              </thead>
              <tbody>
                {records.map((record, index) => (
                  <tr key={index}>
                    <td>{record.trade_date || "未知"}</td>
                    <td>
                      {record.source || "未知"}
                      <small>{record.pool}</small>
                    </td>
                    <td>{sentimentKnownTime(record.source_published_at)}</td>
                    <td>{sentimentKnownTime(record.fetched_at)}</td>
                    <td>{sentimentKnownTime(record.first_known_at)}</td>
                    <td>
                      <code title={record.source_sha256}>
                        {record.source_sha256 || "未登记"}
                      </code>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </details>
    </section>
  );
}

export default function EmotionDashboard({
  sentiment,
  coverage,
  warnings = [],
  observation,
}: {
  sentiment: Row;
  coverage?: unknown;
  warnings?: string[];
  observation?: ReactNode;
}) {
  const dimensions: Row[] = [
    sentiment.market || {},
    sentiment.short_term || {},
  ];
  const references = sentimentReferences(sentiment.references);
  const notices = [
    ...new Set(
      [sentiment.notice, ...warnings].filter(
        (item): item is string => typeof item === "string" && !!item.trim(),
      ),
    ),
  ];
  return (
    <div className="emotion-dashboard">
      <section className="sentiment-atmosphere" aria-label="盘面氛围">
        <span>三轴复盘</span>
        <strong>环境与反馈对照</strong>
        <p>{sentiment.atmosphere?.summary || "样本尚不足以描述当前盘面。"}</p>
        <time>{sentiment.date || "日期待核验"}</time>
      </section>
      <IndexEnvironment dimension={sentiment.index_environment || {}} />
      <div className="sentiment-dimensions">
        <Dimension
          dimension={{ ...dimensions[0], label: "个股广度" }}
          kind="market"
        />
        <ShortTermDimension dimension={dimensions[1]} />
      </div>
      <SentimentProvenance provenance={sentiment.provenance || {}} />
      <SentimentSnapshots
        key={`${sentiment.date}:${sentiment.provenance?.input_fingerprint}`}
        date={sentiment.date}
        fingerprint={sentiment.provenance?.input_fingerprint}
      />
      <details className="sentiment-evidence">
        <summary>查看统计口径、数据缺口与广度观察</summary>
        <div className="sentiment-evidence-body">
          {coverage != null && <MarketDataCoverage coverage={coverage} />}
          {sentiment.scope && (
            <p className="sentiment-total-scope">本次范围：{sentiment.scope}</p>
          )}
          {notices.map((notice) => (
            <p key={notice}>{notice}</p>
          ))}
          <div className="sentiment-formula-groups">
            {dimensions.map((dimension, index) => (
              <section key={index}>
                <h3>
                  {dimension.label ||
                    (index === 0 ? "市场赚钱效应" : "短线情绪")}
                </h3>
                <p>{dimension.scope || "样本范围待核验"}</p>
                {index === 1 &&
                  (dimension.cohort_candidate_count != null ||
                    dimension.previous_unverified_count != null) && (
                    <p>
                      前日来源候选{" "}
                      {sentimentCount(dimension.cohort_candidate_count)} 家 ·
                      前日涨停状态待核验{" "}
                      {sentimentCount(dimension.previous_unverified_count)} 家。
                    </p>
                  )}
                <dl>
                  {(Array.isArray(dimension.metrics)
                    ? dimension.metrics
                    : []
                  ).map((metric: Row, metricIndex: number) => (
                    <div key={metric.key || metricIndex}>
                      <dt>{metric.label || "观察指标"}</dt>
                      <dd>
                        {sentimentMetricView(metric).help || "暂无补充口径。"}
                      </dd>
                    </div>
                  ))}
                </dl>
              </section>
            ))}
          </div>
          {!!references.length && (
            <div className="sentiment-references">
              <h3>参考资料</h3>
              {references.map((reference) => (
                <p key={reference.url}>
                  <a href={reference.url} target="_blank" rel="noreferrer">
                    {reference.title || "打开来源资料"} ↗
                  </a>
                  {reference.use && <span> · {reference.use}</span>}
                </p>
              ))}
            </div>
          )}
          {observation && (
            <details className="sentiment-legacy-observation">
              <summary>旧版广度观察 · 原有标签与上涨家数</summary>
              <p>这里保留原有广度模型的标签，不是六阶段情绪周期的确认结果。</p>
              <div>{observation}</div>
            </details>
          )}
        </div>
      </details>
    </div>
  );
}

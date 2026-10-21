import type { ReactNode } from "react";
import { Activity, Layers3 } from "lucide-react";
import type { Row } from "../../lib/api";
import { Badge } from "../../components/UI";
import { MetricHelp } from "./MetricHelp";
import MarketDataCoverage from "./MarketDataCoverage";
import {
  sentimentCount,
  sentimentCoverage,
  sentimentMetricView,
  sentimentReferences,
} from "./sentimentPresentation";
import "./EmotionDashboard.css";

function Dimension({
  dimension,
  kind,
}: {
  dimension: Row;
  kind: "market" | "short_term";
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
        <span>盘面氛围</span>
        <strong>{sentiment.atmosphere?.label || "待核验"}</strong>
        <p>{sentiment.atmosphere?.summary || "样本尚不足以描述当前盘面。"}</p>
        <time>{sentiment.date || "日期待核验"}</time>
      </section>
      <div className="sentiment-dimensions">
        <Dimension dimension={dimensions[0]} kind="market" />
        <Dimension dimension={dimensions[1]} kind="short_term" />
      </div>
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
              <summary>广度观察 · 原有阶段读数与上涨家数</summary>
              <div>{observation}</div>
            </details>
          )}
        </div>
      </details>
    </div>
  );
}

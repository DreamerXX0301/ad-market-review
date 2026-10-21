import { Activity, ChevronDown } from "lucide-react";
import type { Row } from "../../lib/api";
import { integer, num, tone } from "../../lib/format";
import { Panel } from "../../components/UI";
import { MetricHelp } from "./MetricHelp";
import { emotionStageSummary, marketExplanations } from "./marketPresentation";

/** Present the server's phase, without duplicating or changing its rules. */
export default function EmotionStage({
  emotion,
  warnings = [],
}: {
  emotion: Row;
  warnings?: string[];
}) {
  const valid = (value: unknown): value is number =>
    typeof value === "number" && Number.isFinite(value);
  const temperature = valid(emotion.temperature) ? emotion.temperature : null;
  const delta = valid(emotion.temperature_delta)
    ? emotion.temperature_delta
    : null;
  const coverage = valid(emotion.comparable_coverage)
    ? emotion.comparable_coverage * 100
    : null;
  const phase = emotion.phase || "待核验";
  const style = ["高潮", "普涨", "修复"].includes(phase)
    ? "warm"
    : ["冰点", "退潮"].includes(phase)
      ? "cool"
      : "neutral";
  return (
    <Panel title="盘面阶段" subtitle="市场广度与来源事件代理 · 本项目观察口径">
      <div className={`emotion-stage ${style}`}>
        <div className="emotion-stage-hero">
          <span className="emotion-stage-icon">
            <Activity size={22} />
          </span>
          <div>
            <span className="emotion-stage-caption">当前阶段</span>
            <h3>{phase}</h3>
            <p title={emotion.phase_reason || "阶段依据待核验"}>
              {emotionStageSummary(phase)}
            </p>
          </div>
          <span className="emotion-stage-date">
            {emotion.date || "日期待核验"}
          </span>
        </div>
        <div className="emotion-stage-facts">
          <div>
            <span>
              广度温度{" "}
              <MetricHelp
                label="广度温度"
                description={marketExplanations.temperature}
              />
            </span>
            <strong>
              {temperature == null ? "—" : num(temperature, 1)}
              <small> / 100</small>
            </strong>
            <div className="emotion-temperature-track">
              <i
                style={{
                  width:
                    temperature == null
                      ? "0%"
                      : `${Math.max(0, Math.min(100, temperature))}%`,
                }}
              />
            </div>
          </div>
          <div>
            <span>
              相邻交易日变化{" "}
              <MetricHelp
                label="温度变化"
                description={marketExplanations.temperature_delta}
              />
            </span>
            <strong className={tone(delta)}>
              {delta == null ? "—" : `${delta > 0 ? "+" : ""}${num(delta, 1)}`}
              <small> 个百分点</small>
            </strong>
            <small>
              {emotion.previous_date
                ? `对比 ${emotion.previous_date}`
                : "前一同口径读数待核验"}
            </small>
          </div>
          <div>
            <span>
              可比样本{" "}
              <MetricHelp
                label="可比样本"
                description={marketExplanations.comparable_coverage_pct}
              />
            </span>
            <strong>
              {integer(emotion.comparable_count)}
              <small> 家</small>
            </strong>
            <small>
              样本可比率 {coverage == null ? "—" : num(coverage, 1) + "%"}
            </small>
          </div>
          <div>
            <span>来源事件证据</span>
            <strong className="emotion-proof-state">
              {emotion.event_pools_complete === true
                ? "已齐备"
                : emotion.event_pools_complete === false
                  ? "待补齐"
                  : "待核验"}
            </strong>
            <small>
              {emotion.event_pools_complete === true
                ? "仅指本日来源涨跌停池"
                : "池不完整时不确认高潮"}
            </small>
          </div>
        </div>
      </div>
      <details className="emotion-stage-details">
        <summary>
          阶段规则、数据口径与参考来源 <ChevronDown size={13} />
        </summary>
        <div className="emotion-stage-rule-body">
          <p>
            <strong>本次判定依据：</strong>
            {emotion.phase_reason || "待核验"}
          </p>
          <p>{emotion.notice}</p>
          <p className="muted">
            {emotion.formula} · {emotion.scope} · 模型{" "}
            {emotion.model || "待核验"}
          </p>
          <ol>
            {(emotion.rules || []).map((item: Row) => (
              <li
                key={item.phase}
                className={item.phase === phase ? "current" : ""}
              >
                <strong>{item.phase}</strong>
                <span>{item.rule}</span>
              </li>
            ))}
          </ol>
          {warnings
            .filter((warning) => warning !== emotion.notice)
            .map((warning) => (
              <p className="muted" key={warning}>
                {warning}
              </p>
            ))}
          <p className="muted">
            参考资料用于解释投资行为边界，交易所没有为本项目阈值背书。
          </p>
          {(emotion.references || []).map((reference: Row) => (
            <p key={reference.url}>
              <a href={reference.url} target="_blank" rel="noreferrer">
                {reference.title}
              </a>
              <span className="muted"> · {reference.use}</span>
            </p>
          ))}
        </div>
      </details>
    </Panel>
  );
}

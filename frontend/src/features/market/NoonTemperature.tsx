import { Badge, Panel } from "../../components/UI";
import type { Row } from "../../lib/api";
import "./NoonTemperature.css";

const finite = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);
const percentage = (value: unknown): number | null =>
  finite(value) && value >= 0 && value <= 100 ? value : null;

/** The component displays a versioned local rule; it never calculates market facts. */
export default function NoonTemperature({
  temperature,
}: {
  temperature?: Row | null;
}) {
  const supported = temperature?.version === "noon-temperature-v1";
  const score =
    supported && temperature.state === "available"
      ? percentage(temperature.score)
      : null;
  const available = score != null;
  const components =
    supported && Array.isArray(temperature.components)
      ? temperature.components.filter(
          (item: unknown): item is Row =>
            !!item && typeof item === "object" && !Array.isArray(item),
        )
      : [];
  const level =
    score == null
      ? "unknown"
      : score < 40
        ? "cool"
        : score < 60
          ? "neutral"
          : "warm";
  const coverage = supported ? temperature.coverage : null;
  return (
    <Panel
      className={`noon-temperature noon-temperature-${level}`}
      title="午间情绪温度"
      subtitle="本次快照的本地规则参考"
      actions={
        <Badge tone={available ? "neutral" : "warn"}>
          {available ? "午间状态" : "数据不足"}
        </Badge>
      }
    >
      <div className="noon-temperature-body">
        <div className="noon-temperature-heading">
          <div className="noon-temperature-reading">
            <strong aria-label="午间情绪温度分数">
              {score == null ? "—" : score.toFixed(1)}
            </strong>
            <span>/ 100</span>
          </div>
          <div className="noon-temperature-status">
            <b>{available ? temperature?.label || "已计算" : "暂不评分"}</b>
            <small>
              {finite(coverage?.available_components)
                ? `${coverage.available_components} / 5 项依据可用`
                : "等待完整评分依据"}
            </small>
          </div>
        </div>
        <div
          className="noon-temperature-meter"
          role={available ? "meter" : undefined}
          aria-label={available ? "午间情绪温度" : "温度暂不可用"}
          aria-valuemin={available ? 0 : undefined}
          aria-valuemax={available ? 100 : undefined}
          aria-valuenow={score ?? undefined}
          aria-valuetext={
            available
              ? `${score.toFixed(1)}分，${temperature?.label || "已计算"}`
              : undefined
          }
        >
          {score != null && <i style={{ left: `${score}%` }} />}
        </div>
        <div className="noon-temperature-scale" aria-hidden="true">
          <span>偏冷</span>
          <span>中性</span>
          <span>偏热</span>
        </div>
        {!available && (
          <p className="noon-temperature-gap">
            {temperature?.reason ||
              "当前快照缺少完整评分依据，不用已知部分推算总分。"}
          </p>
        )}
        {components.length > 0 && (
          <div
            className="noon-temperature-components"
            aria-label="午间温度评分依据"
          >
            {components.map((item: Row, index: number) => {
              const value = percentage(item.value);
              return (
                <div
                  key={item.key || index}
                  title={item.reason || item.evidence || "评分依据未提供"}
                >
                  <span>{item.label || "依据待核验"}</span>
                  <div
                    className="noon-temperature-component-track"
                    aria-hidden="true"
                  >
                    {value != null && <i style={{ width: `${value}%` }} />}
                  </div>
                  <b>{value == null ? "—" : value.toFixed(0)}</b>
                </div>
              );
            })}
          </div>
        )}
        <details className="noon-temperature-rules">
          <summary>查看评分依据与权重</summary>
          {components.length > 0 ? (
            <ul>
              {components.map((item: Row, index: number) => (
                <li key={item.key || index}>
                  <b>
                    {item.label || "依据待核验"}
                    {finite(item.weight) ? ` · 权重${item.weight}%` : ""}
                  </b>
                  <span>
                    {item.evidence || item.reason || "该项证据未提供"}
                  </span>
                  {item.reason && item.evidence && <small>{item.reason}</small>}
                </li>
              ))}
            </ul>
          ) : (
            <p>尚无可展示的分项依据。</p>
          )}
          <p>五项依据全部有效才给出总分；缺失不补零，不重新分配权重。</p>
          {supported && temperature.scope && <p>{temperature.scope}</p>}
        </details>
        <p className="noon-temperature-notice">
          仅描述本次午评，不是官方指标、收盘确认或策略信号。
        </p>
      </div>
    </Panel>
  );
}

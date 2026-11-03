import type { Row } from "../../lib/api";
import { num, textValue } from "../../lib/format";
import {
  sectorCheckLeaves,
  sectorFieldLabel,
  sectorPeriodLabel,
} from "./sectorPresentation";

export default function SectorEvidence({
  checks,
  fields,
}: {
  checks: any;
  fields: Row[];
}) {
  const leaves = sectorCheckLeaves(checks);
  if (!leaves.length) return <span className="muted">暂无条件证据</span>;
  const unknown = leaves.filter(
    (check) => !check.hidden && check.result == null,
  ).length;
  return (
    <details className="sector-evidence">
      <summary>
        {unknown ? `${unknown}项待核验 · ` : ""}查看{leaves.length}项判断依据
      </summary>
      <ul>
        {leaves.map((check, index) =>
          check.hidden ? (
            <li key={index}>{check.description}</li>
          ) : (
            <li key={index}>
              <strong>
                {sectorPeriodLabel(
                  check.indicator_period || check.period || "D",
                )}{" "}
                · {sectorFieldLabel(check.field, fields)}
              </strong>
              ：
              {check.result === true
                ? "满足"
                : check.result === false
                  ? "未满足"
                  : "待核验"}
              {check.actual != null && (
                <span>
                  {" "}
                  · 实际{" "}
                  {typeof check.actual === "number"
                    ? num(check.actual)
                    : textValue(check.actual)}
                </span>
              )}
              {(check.observation_date ||
                check.observed_date ||
                check.date ||
                check.evidence?.observed_date) && (
                <span>
                  {" "}
                  ·{" "}
                  {check.observation_date ||
                    check.observed_date ||
                    check.date ||
                    check.evidence?.observed_date}
                </span>
              )}
              {check.evidence?.line && (
                <div>
                  投影线价 {num(check.evidence.line.projected_price, 4)} · 锚点{" "}
                  {check.evidence.line.start_date}、
                  {check.evidence.line.anchors?.[1]?.date}
                  {" · 确认日 "}
                  {check.evidence.line.confirmed_on}
                </div>
              )}
              {check.reason && <div>{check.reason}</div>}
            </li>
          ),
        )}
      </ul>
    </details>
  );
}

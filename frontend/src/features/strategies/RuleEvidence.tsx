import type { Row } from "../../lib/api";
import { num } from "../../lib/format";
import { Badge } from "../../components/UI";
import { historyWindowText } from "./historicalRules";
import { summarizeChecks } from "./Conditions";

function eventChecks(value: any): Row[] {
  if (Array.isArray(value)) return value.flatMap(eventChecks);
  if (!value || typeof value !== "object") return [];
  if (value.type === "history_event") return [value];
  return eventChecks(
    value.checks || value.all || value.any || value.conditions || [],
  );
}
export default function RuleEvidence({ value }: { value: any }) {
  const events = eventChecks(value);
  if (!events.length) return <>{summarizeChecks(value)}</>;
  return (
    <details className="historical-check-evidence">
      <summary>
        {summarizeChecks(value)}
        <span>查看事件依据</span>
      </summary>
      <div>
        {events.map((check, index) => (
          <section key={index}>
            <header>
              <strong>{historyWindowText(check.window)}</strong>
              <Badge
                tone={
                  check.result === true
                    ? "good"
                    : check.result === false
                      ? "neutral"
                      : "warn"
                }
              >
                {check.result === true
                  ? "满足"
                  : check.result === false
                    ? "未满足"
                    : "待核验"}
              </Badge>
            </header>
            <p>
              {check.selected_event?.date ? (
                <>
                  绑定事件 <b>{check.selected_event.date}</b> ·{" "}
                  {check.selected_event.offset}个交易日前
                </>
              ) : (
                "尚未绑定可确认的事件日"
              )}
            </p>
            {check.selected_event && (
              <div className="historical-event-prices">
                <span>
                  事件收盘 <b>{num(check.selected_event.close)}</b>
                </span>
                <span>
                  最高 <b>{num(check.selected_event.high)}</b>
                </span>
                <span>
                  最低 <b>{num(check.selected_event.low)}</b>
                </span>
                <span>未复权原价</span>
              </div>
            )}
            <p>
              <span className="muted">历史事件：</span>
              {check.event_checks
                ? summarizeChecks(check.event_checks)
                : "该位置报价或事件条件尚未核验"}
            </p>
            {check.checks?.length > 0 && (
              <p>
                <span className="muted">当前条件：</span>
                {summarizeChecks(check.checks)}
              </p>
            )}
            {check.reason && <p className="muted">{check.reason}</p>}
            {check.evidence?.price_comparison_reason && (
              <p className="muted">{check.evidence.price_comparison_reason}</p>
            )}
            <small>
              历史日线 · 观察日 {check.evidence?.as_of || "待核验"}
              {check.evidence?.current_condition_period === "W"
                ? " · 普通条件按完整周，事件价比较按观察日日K"
                : ""}
              {check.evidence?.blocking_date
                ? ` · 优先待核验日 ${check.evidence.blocking_date}`
                : ""}
            </small>
          </section>
        ))}
      </div>
    </details>
  );
}

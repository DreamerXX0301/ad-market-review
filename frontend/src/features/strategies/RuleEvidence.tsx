import type { Row } from "../../lib/api";
import { num } from "../../lib/format";
import { Badge } from "../../components/UI";
import {
  conditionReasonText,
  historyWindowText,
  historyEventPriceLabels,
  historyReclaimModes,
  eventPriceTargetText,
} from "./historicalRules";
import { summarizeChecks } from "./Conditions";

function eventChecks(value: any): Row[] {
  if (Array.isArray(value)) return value.flatMap(eventChecks);
  if (!value || typeof value !== "object") return [];
  if (value.type === "history_event") return [value];
  return eventChecks(
    value.checks || value.all || value.any || value.conditions || [],
  );
}
const priceState = (result: unknown) =>
  result === true ? "满足" : result === false ? "未满足" : "待核验";
function PriceStateBadge({ result }: { result: unknown }) {
  return (
    <Badge
      tone={result === true ? "good" : result === false ? "neutral" : "warn"}
    >
      {priceState(result)}
    </Badge>
  );
}
function PrecisePrice({ value, exact }: { value: unknown; exact?: unknown }) {
  const ready = typeof value === "number" && Number.isFinite(value);
  const exactTitle =
    typeof exact === "string" && exact.length ? exact : undefined;
  return (
    <span title={exactTitle || (ready ? String(value) : undefined)}>
      {ready
        ? value.toLocaleString("zh-CN", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 6,
          })
        : exactTitle || "—"}
    </span>
  );
}
export function HistoryPricePathEvidence({ value }: { value: Row }) {
  const guard = value.guard,
    reclaim = value.reclaim,
    cycle = value.cross_cycle;
  return (
    <div className="history-price-evidence">
      <header>
        <strong>事件后的价格过程 · 日K</strong>
        <PriceStateBadge result={value.result} />
      </header>
      {cycle && (
        <div>
          <h5>
            {cycle.indicator === "macd" ? "MACD" : "KDJ"} 金叉→死叉→再金叉{" "}
            <PriceStateBadge result={cycle.result} />
          </h5>
          <dl>
            <dt>事件日金叉</dt>
            <dd>{priceState(cycle.event_cross)}</dd>
            <dt>今日同类金叉</dt>
            <dd>{priceState(cycle.current_cross)}</dd>
            <dt>中间确实死叉</dt>
            <dd>
              {priceState(
                Object.hasOwn(cycle, "dead_cross_result")
                  ? cycle.dead_cross_result
                  : cycle.dead_cross_date
                    ? true
                    : Number.isInteger(cycle.expected_sessions) &&
                        cycle.known_signal_sessions === cycle.expected_sessions
                      ? false
                      : null,
              )}
              {cycle.dead_cross_date ? ` · ${cycle.dead_cross_date}` : ""}
            </dd>
            <dt>窗口内确为上次金叉</dt>
            <dd>
              {priceState(cycle.previous_cross_confirmed)}
              {cycle.intervening_cross_date
                ? `；另有更近金叉 ${cycle.intervening_cross_date}`
                : ""}
            </dd>
            <dt>已核验中间日 / 应有日</dt>
            <dd>
              {Number.isInteger(cycle.known_signal_sessions)
                ? cycle.known_signal_sessions
                : "—"}{" "}
              /{" "}
              {Number.isInteger(cycle.expected_sessions)
                ? cycle.expected_sessions
                : "—"}
            </dd>
            <dt>事件收盘 / 今日收盘</dt>
            <dd>
              <PrecisePrice value={cycle.event_close} /> /{" "}
              <PrecisePrice value={cycle.current_close} />
            </dd>
            {cycle.blocking_date && (
              <>
                <dt>待核验日期</dt>
                <dd>{cycle.blocking_date}</dd>
              </>
            )}
          </dl>
          <p className="path-subcheck">
            这里只核验同类金叉与中间死叉；事件价比较、百分比偏移与今日K值范围分别按今日条件判断，不影响上次金叉的选择。
          </p>
          {cycle.reason && (
            <p className="muted">{conditionReasonText(cycle.reason)}</p>
          )}
        </div>
      )}
      {guard && (
        <div>
          <h5>
            期间收盘守位{" "}
            <PriceStateBadge
              result={
                Object.hasOwn(guard, "holding_result")
                  ? guard.holding_result
                  : guard.result
              }
            />
          </h5>
          <dl>
            <dt>保护价</dt>
            <dd>
              <PrecisePrice value={guard.floor} exact={guard.floor_decimal} />
            </dd>
            <dt>计算依据</dt>
            <dd>
              事件收盘 <PrecisePrice value={guard.event_close} /> × (1 −{" "}
              {guard.max_drawdown_pct ?? "—"}%)
            </dd>
            <dt>核验区间</dt>
            <dd>
              {guard.start || "—"} → {guard.end || "—"}
            </dd>
            <dt>可比日 / 应核验日</dt>
            <dd>
              {Number.isInteger(guard.comparable_sessions)
                ? guard.comparable_sessions
                : "—"}{" "}
              /{" "}
              {Number.isInteger(guard.expected_sessions)
                ? guard.expected_sessions
                : "—"}
            </dd>
            <dt>
              {Number.isInteger(guard.expected_sessions) &&
              guard.comparable_sessions === guard.expected_sessions
                ? "期间最低收盘"
                : "已核验可比部分最低收盘"}
            </dt>
            <dd>
              <PrecisePrice value={guard.min_close} />
              {guard.min_close_date ? ` · ${guard.min_close_date}` : ""}
            </dd>
            {guard.first_breach_date && (
              <>
                <dt>首次收盘失守</dt>
                <dd>
                  {guard.first_breach_date} ·{" "}
                  <PrecisePrice value={guard.first_breach_close} />
                </dd>
              </>
            )}
            {guard.blocking_date && (
              <>
                <dt>待核验日期</dt>
                <dd>{guard.blocking_date}</dd>
              </>
            )}
          </dl>
          {guard.pullback && (
            <p className="path-subcheck">
              事件后至昨日实际回落：{priceState(guard.pullback.result)}
              {guard.pullback.date && (
                <>
                  {" "}
                  · {guard.pullback.date} 收盘{" "}
                  <PrecisePrice value={guard.pullback.close} />
                </>
              )}
              {guard.pullback.reason
                ? `；${conditionReasonText(guard.pullback.reason)}`
                : ""}
            </p>
          )}
          {guard.near_floor && (
            <p className="path-subcheck">
              {guard.near_floor.sessions === 1
                ? "昨日"
                : `截至昨日近${guard.near_floor.sessions ?? "?"}个交易日`}
              接近保护价：{priceState(guard.near_floor.result)}
              ；允许收盘区间{" "}
              <PrecisePrice value={guard.floor} exact={guard.floor_decimal} />—
              <PrecisePrice
                value={guard.near_floor.upper}
                exact={guard.near_floor.upper_decimal}
              />
              （保护价上方{guard.near_floor.band_pct ?? "—"}%）
              {guard.near_floor.date && (
                <>
                  ；{guard.near_floor.date} 收盘{" "}
                  <PrecisePrice value={guard.near_floor.close} />
                </>
              )}
              {guard.near_floor.reason
                ? `；${conditionReasonText(guard.near_floor.reason)}`
                : ""}
            </p>
          )}
          {guard.reason && (
            <p className="muted">{conditionReasonText(guard.reason)}</p>
          )}
          <small>价格按未舍入值判断；悬停数值可查看完整精度。</small>
        </div>
      )}
      {reclaim && (
        <div>
          <h5>
            事件价收复或触及 <PriceStateBadge result={reclaim.result} />
          </h5>
          <dl>
            <dt>触发方式</dt>
            <dd>
              {historyReclaimModes.find((mode) => mode.value === reclaim.mode)
                ?.label || "待核验"}
            </dd>
            <dt>事件参照价</dt>
            <dd>
              {historyEventPriceLabels[reclaim.event_field] || "—"} ·{" "}
              <PrecisePrice value={reclaim.reference_price} />
            </dd>
            <dt>前一交易日收盘</dt>
            <dd>
              {reclaim.previous_date || "—"} ·{" "}
              <PrecisePrice value={reclaim.previous_close} />
            </dd>
            <dt>今日收盘</dt>
            <dd>
              {reclaim.current_date || "—"} ·{" "}
              <PrecisePrice value={reclaim.current_close} />
            </dd>
            <dt>今日最高 / 最低</dt>
            <dd>
              <PrecisePrice value={reclaim.current_high} /> /{" "}
              <PrecisePrice value={reclaim.current_low} />
            </dd>
            {reclaim.first_prior_reclaim_date && (
              <>
                <dt>此前已发生收复</dt>
                <dd>{reclaim.first_prior_reclaim_date}</dd>
              </>
            )}
            {reclaim.blocking_date && (
              <>
                <dt>待核验日期</dt>
                <dd>{reclaim.blocking_date}</dd>
              </>
            )}
          </dl>
          {reclaim.mode === "first_close_reclaim" && (
            <p className="path-subcheck">
              首次只按同一事件的价格收复判断；此前技术条件不满足不会重置首次。
            </p>
          )}
          {reclaim.mode === "touch" && (
            <p className="path-subcheck">
              实际触及需最低≤参照价≤最高；整日跳空在其上方不算触及。
            </p>
          )}
          {reclaim.reason && (
            <p className="muted">{conditionReasonText(reclaim.reason)}</p>
          )}
        </div>
      )}
      {value.reason && (
        <p className="muted">{conditionReasonText(value.reason)}</p>
      )}
    </div>
  );
}
function eventPriceOffsetChecks(value: any): Row[] {
  if (Array.isArray(value)) return value.flatMap(eventPriceOffsetChecks);
  if (!value || typeof value !== "object") return [];
  if (value.right_event_field && Object.hasOwn(value, "right_event_change_pct"))
    return [value];
  return eventPriceOffsetChecks(
    value.checks || value.all || value.any || value.conditions || [],
  );
}
export function EventPriceOffsetEvidence({ value }: { value: Row }) {
  const change = value.event_change_pct_decimal ?? value.event_change_pct;
  return (
    <div className="history-price-evidence">
      <div>
        <h5>
          事件价偏移比较 <PriceStateBadge result={value.result} />
        </h5>
        <dl>
          <dt>今日收盘</dt>
          <dd>
            <PrecisePrice value={value.actual} />
          </dd>
          <dt>原事件参照价</dt>
          <dd>
            <PrecisePrice value={value.event_price} />
          </dd>
          <dt>比较关系与公式</dt>
          <dd>
            {value.op} {eventPriceTargetText(value)}
          </dd>
          <dt>偏移后精确阈值</dt>
          <dd>
            <PrecisePrice
              value={value.right_actual}
              exact={value.threshold_decimal}
            />
          </dd>
          <dt>实际相对事件价涨跌</dt>
          <dd>
            {change === null || change === undefined ? "—" : `${change}%`}
          </dd>
        </dl>
        <small>
          偏移作用于事件参照价；按未舍入阈值判断，悬停可看完整精度。
        </small>
        {value.reason && (
          <p className="muted">{conditionReasonText(value.reason)}</p>
        )}
      </div>
    </div>
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
            {check.price_path_checks && (
              <HistoryPricePathEvidence value={check.price_path_checks} />
            )}
            {eventPriceOffsetChecks(check.checks).map((comparison, at) => (
              <EventPriceOffsetEvidence key={at} value={comparison} />
            ))}
            {check.reason && (
              <p className="muted">{conditionReasonText(check.reason)}</p>
            )}
            {check.evidence?.price_comparison_reason && (
              <p className="muted">
                {conditionReasonText(check.evidence.price_comparison_reason)}
              </p>
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

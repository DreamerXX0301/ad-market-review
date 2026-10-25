import type { RuleNode } from "./historicalRules";
import {
  applyHistoryPricePreset,
  defaultHistoryGuard,
  defaultHistoryReclaim,
  historyEventPriceLabels,
  historyReclaimModes,
  validateHistoryPricePath,
  withHistoryPricePart,
  pureGoldenCross,
  previousCrossConfigurationError,
} from "./historicalRules";

const inputNumber = (value: string) => (value === "" ? "" : Number(value));

export default function HistoryPricePathEditor({
  event,
  onChange,
  allowRebound = true,
}: {
  event: RuleNode;
  onChange: (event: RuleNode) => void;
  allowRebound?: boolean;
}) {
  const guard = event.price_path?.guard;
  const reclaim = event.price_path?.reclaim;
  const cycle = event.price_path?.cross_cycle;
  const previousIndicator = pureGoldenCross(event.where);
  const canEnableCycle =
    !!previousIndicator && event.window?.from === 1 && event.match === "latest";
  const error = validateHistoryPricePath(event.price_path);
  const updateGuard = (patch: RuleNode) =>
    onChange(withHistoryPricePart(event, "guard", { ...guard, ...patch }));
  const updateReclaim = (patch: RuleNode) =>
    onChange(withHistoryPricePart(event, "reclaim", { ...reclaim, ...patch }));
  const afterYesterday =
    guard?.require_pullback ||
    guard?.near_floor ||
    ["close_reclaim", "first_close_reclaim"].includes(reclaim?.mode);
  return (
    <div className="event-stage history-price-path-editor">
      <header>
        <span>2</span>
        <div>
          <strong>
            事件后的价格过程 <em>可选 · 日K</em>
          </strong>
          <small>
            只检查已绑定的同一次事件。下方已启用的过程全部满足，再同时检查今日条件。
          </small>
          <small>
            只要求当前收盘高于事件收盘时，在“今日条件”添加“观察日收盘价 &gt;
            事件日收盘价”。“今日重新收复”还要求昨日低于事件价。
          </small>
        </div>
      </header>
      <div className="history-price-presets">
        <span>快速设置</span>
        {allowRebound && (
          <button
            type="button"
            className="btn small"
            onClick={() =>
              onChange(applyHistoryPricePreset(event, "guard-rebound"))
            }
          >
            守位后今日回升 ≥2%
          </button>
        )}
        <button
          type="button"
          className="btn small"
          onClick={() => onChange(applyHistoryPricePreset(event, "reclaim"))}
        >
          事件价重新收复
        </button>
        <small>保留已有历史事件与今日技术条件</small>
      </div>
      <div className="history-price-parts">
        {(cycle || previousIndicator) && (
          <section
            className={"history-price-cross-cycle " + (cycle ? "enabled" : "")}
          >
            <label className="history-price-toggle">
              <input
                type="checkbox"
                aria-label="启用金叉死叉再金叉周期"
                checked={!!cycle}
                disabled={!cycle && !canEnableCycle}
                onChange={(e) =>
                  onChange(
                    withHistoryPricePart(
                      event,
                      "cross_cycle",
                      e.target.checked
                        ? {
                            indicator: previousIndicator,
                            require_dead_cross: true,
                          }
                        : undefined,
                    ),
                  )
                }
              />
              <span>
                <strong>金叉→死叉→再金叉</strong>
                <small>
                  窗口内上次同类日K金叉，期间确实死叉，今日再次金叉；价格高低与K值范围另看今日条件。
                </small>
              </span>
            </label>
            {cycle ? (
              <div className="history-price-options">
                <small>
                  核验 {cycle.indicator === "macd" ? "MACD" : "KDJ"}{" "}
                  周期。历史仅按同类金叉选最近一次，不用今日K值或价格筛掉真正的上次。关闭本开关不会删除今日事件价比较。
                </small>
                {previousCrossConfigurationError(event) && (
                  <p className="history-price-error" role="alert">
                    {previousCrossConfigurationError(event)}
                  </p>
                )}
              </div>
            ) : (
              !canEnableCycle && (
                <p className="history-price-age-note">
                  先在历史事件模板选择MACD或KDJ上次金叉，或手动设为前1日开始回看、最近一次、纯同类日K金叉，才能启用周期核验。
                </p>
              )
            )}
          </section>
        )}
        <section className={guard ? "enabled" : ""}>
          <label className="history-price-toggle">
            <input
              type="checkbox"
              aria-label="启用期间收盘守位"
              checked={!!guard}
              onChange={(e) =>
                onChange(
                  withHistoryPricePart(
                    event,
                    "guard",
                    e.target.checked ? defaultHistoryGuard() : undefined,
                  ),
                )
              }
            />
            <span>
              <strong>期间收盘守位</strong>
              <small>事件之后至今日，每次收盘都不能跌破保护价</small>
            </span>
          </label>
          {guard && (
            <div className="history-price-options">
              <label>
                事件收盘下方最多回撤 (%)
                <input
                  type="number"
                  min={0}
                  max={100}
                  step="any"
                  aria-label="事件收盘最大回撤百分比"
                  value={guard.max_drawdown_pct ?? ""}
                  onChange={(e) =>
                    updateGuard({
                      max_drawdown_pct: inputNumber(e.target.value),
                    })
                  }
                />
              </label>
              <p className="history-price-formula">
                保护价 = 事件收盘 × (1 − 回撤百分比)。例如10元、5% →
                9.50元；相等算守住。
              </p>
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={guard.require_pullback === true}
                  aria-label="要求事件后真实回落"
                  onChange={(e) =>
                    updateGuard({ require_pullback: e.target.checked })
                  }
                />
                事件后至昨日，至少一次收盘低于事件收盘
              </label>
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={!!guard.near_floor}
                  aria-label="要求回踩接近保护价"
                  onChange={(e) => {
                    const next = { ...guard };
                    if (e.target.checked)
                      next.near_floor = { sessions: 1, band_pct: 1 };
                    else delete next.near_floor;
                    onChange(withHistoryPricePart(event, "guard", next));
                  }}
                />
                回踩曾接近保护价
              </label>
              {guard.near_floor && (
                <div className="history-price-near">
                  <label>
                    回踩时间
                    <select
                      aria-label="接近保护价回踩时间"
                      value={
                        guard.near_floor.sessions === 1 ? "yesterday" : "recent"
                      }
                      onChange={(e) =>
                        updateGuard({
                          near_floor: {
                            ...guard.near_floor,
                            sessions: e.target.value === "yesterday" ? 1 : 5,
                          },
                        })
                      }
                    >
                      <option value="yesterday">昨日</option>
                      <option value="recent">截至昨日近N个交易日</option>
                    </select>
                  </label>
                  {guard.near_floor.sessions !== 1 && (
                    <label>
                      回看交易日数
                      <input
                        type="number"
                        min={1}
                        max={750}
                        step={1}
                        aria-label="接近保护价回看交易日数"
                        value={guard.near_floor.sessions ?? ""}
                        onChange={(e) =>
                          updateGuard({
                            near_floor: {
                              ...guard.near_floor,
                              sessions: inputNumber(e.target.value),
                            },
                          })
                        }
                      />
                    </label>
                  )}
                  <label>
                    保护价上方区间 (%)
                    <input
                      type="number"
                      min={0}
                      max={100}
                      step="any"
                      aria-label="接近保护价上方区间百分比"
                      value={guard.near_floor.band_pct ?? ""}
                      onChange={(e) =>
                        updateGuard({
                          near_floor: {
                            ...guard.near_floor,
                            band_pct: inputNumber(e.target.value),
                          },
                        })
                      }
                    />
                  </label>
                  <small>
                    只看事件发生后、截至昨日的收盘；需位于保护价至其上方区间，且仍低于事件收盘。
                  </small>
                </div>
              )}
              <small>
                默认只限制收盘，盘中最低跌破不会单独判失败；任何已确认收盘失守，后续收回也不算始终守位。
              </small>
            </div>
          )}
        </section>
        <section className={reclaim ? "enabled" : ""}>
          <label className="history-price-toggle">
            <input
              type="checkbox"
              checked={!!reclaim}
              aria-label="启用事件价收复或触及"
              onChange={(e) =>
                onChange(
                  withHistoryPricePart(
                    event,
                    "reclaim",
                    e.target.checked ? defaultHistoryReclaim() : undefined,
                  ),
                )
              }
            />
            <span>
              <strong>事件价收复或触及</strong>
              <small>比较今日价格与同一次历史事件的价格</small>
            </span>
          </label>
          {reclaim && (
            <div className="history-price-options">
              <label>
                事件参照价
                <select
                  aria-label="事件收复参照价"
                  value={reclaim.event_field || ""}
                  onChange={(e) =>
                    updateReclaim({ event_field: e.target.value })
                  }
                >
                  {Object.entries(historyEventPriceLabels).map(
                    ([key, label]) => (
                      <option key={key} value={key}>
                        事件日{label}
                      </option>
                    ),
                  )}
                </select>
              </label>
              <label>
                触发方式
                <select
                  aria-label="事件价收复触发方式"
                  value={reclaim.mode || ""}
                  onChange={(e) => updateReclaim({ mode: e.target.value })}
                >
                  {historyReclaimModes.map((mode) => (
                    <option key={mode.value} value={mode.value}>
                      {mode.label}
                    </option>
                  ))}
                </select>
              </label>
              <small>
                {reclaim.mode === "close_at_or_above" &&
                  "仅检查今日收盘不低于参照价，等于也满足；不要求昨日回踩，也不检查事件后每天都站上。"}
                {reclaim.mode === "close_reclaim" &&
                  "昨日收盘须低于参照价，今日收盘不低于参照价；昨日必须晚于事件日。"}
                {reclaim.mode === "first_close_reclaim" &&
                  "从事件次日起按历史价格判断首次收复，再检查今日其他条件；此前技术条件没满足，也不会重置价格首次。"}
                {reclaim.mode === "high_at_or_above" &&
                  "只要求今日最高价不低于参照价，不代表收盘守住；跳空在参照价上方也可满足。"}
                {reclaim.mode === "touch" &&
                  "今日最低价不高于参照价、最高价不低于参照价；整日跳空在其上方不算实际触及。"}
              </small>
            </div>
          )}
        </section>
      </div>
      {afterYesterday && (
        <p className="history-price-age-note">
          真实回落、接近保护价或重新收复需要事件后至少有昨日这一根日K，事件应在前2个交易日或更早。不会为满足过程而跳过已绑定的最近事件。
        </p>
      )}
      {error && (
        <p className="history-price-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

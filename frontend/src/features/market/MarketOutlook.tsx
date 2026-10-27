import {
  ArrowUpRight,
  ChevronDown,
  Compass,
  Thermometer,
  Crosshair,
} from "lucide-react";
import type { ReactNode } from "react";
import type { Row } from "../../lib/api";
import { num, percent, tone } from "../../lib/format";
import {
  outlookAction,
  outlookAxisEvidence,
  outlookCandidateTracking,
  outlookChanges,
  outlookForDate,
  outlookInsightsForDate,
  outlookLines,
  outlookNumber,
  outlookRows,
  outlookRoutes,
  outlookStocks,
} from "./marketOutlookPresentation";
import "./MarketOutlook.css";
import BoardTags from "./BoardTags";
import CopyStockCode from "../../components/CopyStockCode";

function turnoverAmount(value: number | null) {
  return value == null ? "—" : `${num(value / 1e8)}亿元`;
}

function Lines({
  items,
  className = "",
}: {
  items: unknown;
  className?: string;
}) {
  const lines = outlookLines(items);
  return lines.length ? (
    <ul className={className}>
      {lines.map((line, index) => (
        <li key={index}>{line}</li>
      ))}
    </ul>
  ) : null;
}

function Candidate({
  stock,
  onStock,
  date,
  snapshotId,
}: {
  stock: Row;
  onStock: (id: string) => void;
  date: string;
  snapshotId: unknown;
}) {
  return (
    <article className="outlook-stock">
      <div className="outlook-stock-title">
        <button
          onClick={() => onStock(stock.instrument_id)}
          aria-label={`查看${stock.name || stock.code || stock.instrument_id}个股详情`}
        >
          <strong>{stock.name || stock.code || stock.instrument_id}</strong>
          <ArrowUpRight size={13} />
        </button>
        <CopyStockCode code={stock.code} instrumentId={stock.instrument_id} />
        <span className={`outlook-stock-change ${tone(stock.change_pct)}`}>
          {percent(outlookNumber(stock.change_pct))}
        </span>
      </div>
      <p>{stock.reason || "观察依据待核验"}</p>
      <div className="outlook-stock-facts">
        <span>
          {stock.evidence_date} 收盘 {num(outlookNumber(stock.close))}
        </span>
      </div>
      <BoardTags row={stock} date={date} snapshotId={snapshotId} />
      <details>
        <summary>
          跟踪条件 <ChevronDown size={12} />
        </summary>
        {stock.trigger && <p>确认：{stock.trigger}</p>}
        <Lines items={stock.invalidations} />
      </details>
    </article>
  );
}

function Change({ change }: { change: Row }) {
  const value = outlookNumber(change.value);
  const previous = outlookNumber(change.previous_value);
  const delta = outlookNumber(change.delta);
  const amountDifference =
    change.unit === "CNY" && value != null && previous != null
      ? value - previous
      : null;
  const deltaUnit =
    change.delta_unit || (change.unit === "%" ? "个百分点" : change.unit) || "";
  const explanation = outlookLines([
    change.evidence,
    change.interpretation,
    change.reason,
  ])[0];
  const readingText = (reading: number) =>
    change.unit === "CNY"
      ? turnoverAmount(reading)
      : `${num(reading, 1)}${change.unit || ""}`;
  return (
    <article
      className={`outlook-change ${change.state === "unknown" ? "unknown" : "observed"}`}
    >
      <span>{change.label || "当日变化"}</span>
      <strong>{value == null ? "待核验" : readingText(value)}</strong>
      {amountDifference != null ? (
        <small className={tone(amountDifference)}>
          较前日 {amountDifference > 0 ? "+" : ""}
          {turnoverAmount(amountDifference)}
          {delta != null
            ? `（${delta > 0 ? "+" : ""}${num(delta, 1)}${deltaUnit}）`
            : ""}
        </small>
      ) : delta != null ? (
        <small className={tone(delta)}>
          较前日 {delta > 0 ? "+" : ""}
          {num(delta, 1)}
          {deltaUnit}
        </small>
      ) : null}
      <p>{explanation || "变化依据待核验"}</p>
      {typeof change.interpretation === "string" &&
        change.interpretation !== explanation && (
          <small>{change.interpretation}</small>
        )}
      {change.previous_date && (
        <small>
          比较日 {change.previous_date}
          {previous == null ? "" : ` · 前值 ${readingText(previous)}`}
        </small>
      )}
      {change.scope && <small>{change.scope}</small>}
    </article>
  );
}

function EntryRoute({ route }: { route: Row }) {
  const view = outlookAction(route);
  return (
    <article className={`outlook-route ${view.status}`}>
      <header>
        <h4>
          {route.label ||
            (
              {
                trend: "趋势参与",
                relay: "短线接力",
                repair: "冰点修复",
              } as Row
            )[route.id]}
        </h4>
        <strong>{view.label}</strong>
      </header>
      <span>已观察的市场条件</span>
      <p>{route.observation || "市场条件待核验"}</p>
      {route.reason && <p>{route.reason}</p>}
      {!!outlookLines(route.missing_conditions).length && (
        <div className="outlook-route-missing">
          <span>尚缺条件</span>
          <Lines items={route.missing_conditions} />
        </div>
      )}
      <details>
        <summary>
          下一交易日确认 / 失效条件 <ChevronDown size={12} />
        </summary>
        <h4>待确认</h4>
        <Lines items={route.triggers} />
        <h4>失效条件</h4>
        <Lines items={route.invalidations} />
      </details>
      <small>执行条件尚未观察，需下一交易日人工核验</small>
    </article>
  );
}

function CandidateReplay({
  tracking,
  date,
}: {
  tracking: Row | null;
  date: string;
}) {
  if (!tracking)
    return (
      <p className="outlook-replay-empty">
        本响应未提供可核验的20交易日候选回放。
      </p>
    );
  const observations = outlookRows(tracking.observations);
  return (
    <details className="outlook-candidate-replay">
      <summary>
        近20交易日候选跟踪 · {tracking.label || "待核验"}{" "}
        <ChevronDown size={12} />
      </summary>
      <p>{tracking.reason || "候选后续证据待核验"}</p>
      <p>
        截至 {date}
        {tracking.candidate_date ? ` · 候选日 ${tracking.candidate_date}` : ""}
        {tracking.last_observed_date
          ? ` · 最近观察 ${tracking.last_observed_date}`
          : ""}
      </p>
      <small>
        {tracking.window_begin || "窗口起点待核验"} 至{" "}
        {tracking.window_end || date} · 最多20个市场交易日
        {tracking.history_complete === false
          ? " · 窗口资料存在缺口，缺失不视为延续或失效"
          : ""}
      </small>
      {!!outlookRows(tracking.index_references).length && (
        <div className="outlook-replay-references">
          {outlookRows(tracking.index_references).map((index) => (
            <span key={index.instrument_id}>
              {index.name || index.instrument_id} · {index.date}低点{" "}
              {num(outlookNumber(index.low))}
            </span>
          ))}
        </div>
      )}
      {observations.length ? (
        <div className="outlook-replay-table">
          <table>
            <caption>候选之后的逐日证据，仅至所选观察日</caption>
            <thead>
              <tr>
                <th scope="col">交易日</th>
                <th scope="col">观察状态</th>
                <th scope="col">广度温度</th>
                <th scope="col">依据与缺口</th>
              </tr>
            </thead>
            <tbody>
              {observations.map((row) => (
                <tr key={row.date}>
                  <th scope="row">{row.date}</th>
                  <td>{row.label || "待核验"}</td>
                  <td>{num(outlookNumber(row.temperature), 1)}</td>
                  <td>
                    {row.reason || "—"}
                    {outlookLines(row.broken_indices).length
                      ? ` · 跌破参照：${outlookLines(row.broken_indices).join("、")}`
                      : ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p>窗口内尚无可展示的后续观察。</p>
      )}
      <Lines items={tracking.conditions} />
      <p>
        {tracking.notice ||
          "仅使用候选日至所选观察日的留存证据；延续观察不等于确认底部。"}
      </p>
    </details>
  );
}

export default function MarketOutlook({
  data,
  date,
  snapshotId,
  onStock,
  indexEnvironment,
}: {
  data?: Row;
  indexEnvironment?: ReactNode;
  date: string;
  snapshotId: unknown;
  onStock: (id: string) => void;
}) {
  const current = outlookForDate(data, date, snapshotId);
  if (!current)
    return (
      <>
        <section
          className="market-outlook outlook-pending"
          aria-label="市场温度与下一交易日计划"
        >
          <Thermometer size={19} />
          <div>
            <strong>市场温度与下一交易日计划</strong>
            <p>正在核对 {date || "所选日期"} 的盘面依据…</p>
          </div>
        </section>
        {indexEnvironment}
      </>
    );
  const stage = current.stage || {};
  const temperature = current.temperature || {};
  const reading = outlookNumber(temperature.value);
  const delta = outlookNumber(temperature.delta);
  const coverage = outlookNumber(temperature.coverage_pct);
  const validReading = reading != null && reading >= 0 && reading <= 100;
  const axes = outlookRows(current.axes);
  const plan = current.plan || {};
  const gate = current.entry_gate || {};
  const turnover = current.turnover || {};
  const bottom = current.bottom_watch || {};
  const insights = outlookInsightsForDate(current.insights, date, snapshotId);
  const headline = insights?.headline || {};
  const changes = outlookChanges(insights?.changes, date);
  const routes = outlookRoutes(insights?.routes);
  const tracking = outlookCandidateTracking(insights?.candidate_tracking, date);
  const actions = outlookRows(plan.actions).filter((action) =>
    ["open", "add", "bottom"].includes(action.id),
  );
  const scenarios = outlookRows(plan.scenarios || current.scenarios);
  const watchlist = current.watchlist || {};
  const topics = outlookRows(watchlist.topics)
    .filter((topic) => topic.evidence_date === date)
    .slice(0, 3);
  const stocks = outlookStocks(watchlist.stocks, date).slice(0, 5);
  return (
    <div className="market-outlook" aria-label="市场温度与下一交易日计划">
      <section
        id="emotion-reading"
        className={`outlook-hero outlook-stage-${stage.code || "unknown"}`}
      >
        <div className="outlook-hero-main">
          <div className="outlook-eyebrow">
            <Thermometer size={16} />
            <span>今日市场读数</span>
            <time>{date} 收盘</time>
          </div>
          <div className="outlook-stage-title">
            <h2>{stage.label || "待核验"}</h2>
            <span>
              {stage.status === "unknown"
                ? "依据待补齐"
                : temperature.universe_expected_known === false
                  ? "样本阶段观察"
                  : "阶段观察"}
            </span>
          </div>
          <p className="outlook-summary">
            {headline.summary ||
              stage.summary ||
              "当前依据尚不足以形成阶段观察。"}
          </p>
          {headline.label && (
            <div
              className={`outlook-headline ${headline.observation_state === "unknown" ? "unknown" : "observed"}`}
            >
              <span>今日观察</span>
              <strong>{headline.label}</strong>
              <small>下一交易日执行条件尚未观察</small>
            </div>
          )}
          <Lines items={stage.reasons} className="outlook-reasons" />
        </div>
        <div className="outlook-thermometer">
          <span>沪深广度温度</span>
          <div className="outlook-temperature-number">
            <strong>{validReading ? num(reading, 1) : "—"}</strong>
            <span>/ 100</span>
          </div>
          <div
            className="outlook-temperature-scale"
            role="meter"
            aria-label="沪深广度温度"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={validReading ? reading : undefined}
            aria-valuetext={
              validReading ? `${num(reading, 1)}，上涨占可比样本比例` : "待核验"
            }
          >
            {validReading && <i style={{ left: `${reading}%` }} />}
          </div>
          <div className="outlook-scale-labels">
            <span>偏冷</span>
            <span>分化</span>
            <span>偏热</span>
          </div>
          <p>
            {delta == null ? (
              "前一同口径读数待核验"
            ) : (
              <>
                <span className={tone(delta)}>
                  {delta > 0 ? "+" : ""}
                  {num(delta, 1)} 个百分点
                </span>{" "}
                较前一交易日
              </>
            )}
          </p>
          <small>
            {temperature.scope || "沪深可比样本"} · 可比率{" "}
            {coverage == null ? "待核验" : `${num(coverage, 1)}%`}
            {temperature.coverage_basis === "retained_master_upper_bound" && (
              <> · 按留存主表上界保守核验，应有范围未全核实</>
            )}
          </small>
        </div>
        <div className="outlook-axis-strip">
          {axes.map((axis) => (
            <div key={axis.id}>
              <span>{axis.label}</span>
              <strong>{axis.state_label || "待核验"}</strong>
              <small>{outlookAxisEvidence(axis)}</small>
            </div>
          ))}
        </div>
        {!!outlookLines(stage.conflicts).length && (
          <div className="outlook-conflicts">
            <strong>盘面分歧</strong>
            <span>{outlookLines(stage.conflicts).join("；")}</span>
          </div>
        )}
      </section>

      {indexEnvironment}

      <section className="outlook-insights" aria-label="今日变化与机会风险资料">
        <header className="outlook-section-head">
          <div>
            <h2>今日变化与参与线索</h2>
            <p>市场表现、风险与资料缺口分别核对。</p>
          </div>
          <span className="outlook-observation-tag">截至 {date}</span>
        </header>
        {insights ? (
          <>
            {!!changes.length && (
              <div className="outlook-changes">
                {changes.map((change) => (
                  <Change key={change.id} change={change} />
                ))}
              </div>
            )}
            <div className="outlook-insight-layers">
              <article className="outlook-layer opportunity">
                <h3>机会线索</h3>
                <Lines items={insights.opportunities} />
                {!outlookLines(insights.opportunities).length && (
                  <p>暂无有据的机会线索。</p>
                )}
              </article>
              <article className="outlook-layer risk">
                <h3>风险观察</h3>
                <Lines items={insights.risks} />
                {!outlookLines(insights.risks).length && (
                  <p>本次未列额外风险观察，仍需核对各路径失效条件。</p>
                )}
              </article>
              <article className="outlook-layer gap">
                <h3>资料缺口</h3>
                <Lines items={insights.gaps} />
                {!outlookLines(insights.gaps).length && (
                  <p>本次未列额外缺口，以各项来源核验为准。</p>
                )}
              </article>
            </div>
          </>
        ) : (
          <p className="outlook-empty">
            本响应未提供当前日期与行情代际的分层观察，暂按原始阶段与条件清单核对。
          </p>
        )}
      </section>

      <section
        id="emotion-plan"
        className="outlook-plan"
        aria-label="下一交易日执行计划"
      >
        <header className="outlook-section-head">
          <div>
            <span className="outlook-eyebrow">
              <Compass size={16} />
              盘前准备
            </span>
            <h2>下一交易日执行计划</h2>
            <p>
              基于 {date} 收盘 ·{" "}
              {current.next_session?.date ||
                current.next_session?.label ||
                "下一交易日"}
              {!current.next_session?.date ? "，具体日期待日历确认" : ""}
            </p>
          </div>
          <span className={`outlook-stance ${gate.status || "unknown"}`}>
            {plan.label || "等待依据确认"}
          </span>
        </header>
        <p className="outlook-plan-summary">
          {plan.summary || "明日条件尚未发生，先核对当前盘面资料。"}
        </p>
        <div className="outlook-actions">
          {actions.map((action) => {
            const view = outlookAction(action);
            return (
              <article
                key={action.id}
                className={`outlook-action ${view.status}`}
              >
                <header>
                  <h3>
                    {action.label ||
                      (
                        {
                          open: "开仓",
                          add: "加仓",
                          bottom: "低吸 / 抄底",
                        } as Row
                      )[action.id]}
                  </h3>
                  <strong>{view.label}</strong>
                </header>
                <div className="outlook-action-observation">
                  <span>今日市场观察</span>
                  <strong>
                    {action.observation ||
                      (action.id === "bottom"
                        ? routes.find((route) => route.id === "repair")
                            ?.observation || bottom.label
                        : action.id === "add"
                          ? routes.find((route) => route.id === "trend")
                              ?.observation
                          : headline.label) ||
                      "按观察日依据准备"}
                  </strong>
                  <p>{action.reason}</p>
                </div>
                <div className="outlook-action-execution">
                  <span>下一交易日执行状态</span>
                  <strong>条件尚未观察</strong>
                  <small>需真实数据人工核验</small>
                </div>
                <h4>明日先确认</h4>
                <Lines items={action.triggers} />
                <div className="outlook-invalidation">
                  <span>失效 / 放弃条件</span>
                  <Lines items={action.invalidations} />
                </div>
                <small>条件待下一交易日验证</small>
              </article>
            );
          })}
        </div>
        {!!scenarios.length && (
          <details className="outlook-scenarios">
            <summary>
              盘中三种情景怎么应对 <ChevronDown size={14} />
            </summary>
            <div>
              {scenarios.map((scenario) => (
                <article key={scenario.id}>
                  <h3>{scenario.label}</h3>
                  <h4>观察条件</h4>
                  <Lines items={scenario.triggers} />
                  <h4>应对</h4>
                  <Lines items={scenario.actions} />
                  <h4>放弃条件</h4>
                  <Lines items={scenario.invalidations} />
                </article>
              ))}
            </div>
          </details>
        )}
      </section>

      <section
        id="emotion-entry"
        className="outlook-context-grid"
        aria-label="开仓限制与放量修复核验"
      >
        <article className={`outlook-gate ${gate.status || "unknown"}`}>
          <span>开仓闸门</span>
          <h3>{gate.label || "等待核验"}</h3>
          <Lines items={gate.reasons} />
          <details>
            <summary>
              何时放行 / 重新禁止 <ChevronDown size={12} />
            </summary>
            <h4>放行条件</h4>
            <Lines items={gate.release_conditions} />
            <h4>禁止条件</h4>
            <Lines items={gate.blocked_conditions} />
          </details>
        </article>
        <article className="outlook-turnover">
          <span>成交活跃度 · 与盘面总览同口径</span>
          <h3>{turnoverAmount(outlookNumber(turnover.amount))}</h3>
          <p className={tone(turnover.change_pct)}>
            较前日 {percent(outlookNumber(turnover.change_pct))}
          </p>
          <small>
            {turnover.expanded === true
              ? `达到显著放量门槛 ${num(turnover.expansion_threshold_pct, 0)}%`
              : turnover.expanded === false
                ? `未达显著放量门槛 ${num(turnover.expansion_threshold_pct, 0)}%`
                : turnover.reason || "相邻交易日成交额待核验"}
          </small>
          <small>
            {turnover.scope || "沪深京已留存A股股票成交额"}
            {turnover.previous_date ? ` · 对比 ${turnover.previous_date}` : ""}
          </small>
        </article>
        <article className="outlook-bottom">
          <span>短底观察</span>
          <h3>{bottom.label || "尚未确认"}</h3>
          <p>
            {outlookNumber(bottom.prior_ice_streak) == null
              ? "前日连续冰点记录待核验"
              : `此前连续冰点 ${bottom.prior_ice_streak_at_least ? "至少" : ""}${num(bottom.prior_ice_streak, 0)} 日`}
            {outlookNumber(bottom.recent_ice_count) == null
              ? ""
              : ` · 近5日 ${num(bottom.recent_ice_count, 0)}次${bottom.recent_ice_count_complete === false ? "（资料未齐，下界）" : ""}`}
          </p>
          <details>
            <summary>
              候选低点与确认 / 失效条件 <ChevronDown size={12} />
            </summary>
            {bottom.candidate_date && <p>候选日 {bottom.candidate_date}</p>}
            {bottom.index_references_complete === false && (
              <p>四指数观察日低点参照未齐，不能据此形成短底候选。</p>
            )}
            {outlookRows(bottom.index_references).map((index) => (
              <p key={index.instrument_id}>
                {index.name || index.instrument_id} ·{" "}
                {bottom.candidate_date
                  ? "候选日低点"
                  : `${index.date || date} 观察日低点（未形成候选）`}{" "}
                {num(outlookNumber(index.low))}
              </p>
            ))}
            <h4>后续确认条件</h4>
            <Lines items={bottom.confirmation_conditions} />
            <h4>失效条件</h4>
            <Lines items={bottom.invalidations} />
          </details>
          <CandidateReplay tracking={tracking} date={date} />
        </article>
      </section>

      <section className="outlook-routes" aria-label="趋势接力修复独立参与路径">
        <header className="outlook-section-head">
          <div>
            <h2>分路径核对参与条件</h2>
            <p>趋势、短线接力与冰点修复分别读取各自证据和缺口。</p>
          </div>
        </header>
        {routes.length ? (
          <div className="outlook-route-grid">
            {routes.map((route) => (
              <EntryRoute key={route.id} route={route} />
            ))}
          </div>
        ) : (
          <p className="outlook-empty">
            本响应未提供独立路径条件，请结合现有开仓闸门与三轴原始依据核对。
          </p>
        )}
      </section>

      <section
        id="emotion-watchlist"
        className="outlook-watchlist"
        aria-label="核心题材与个股观察名单"
      >
        <header className="outlook-section-head">
          <div>
            <span className="outlook-eyebrow">
              <Crosshair size={16} />
              重点跟踪
            </span>
            <h2>核心方向与个股观察</h2>
            <p>从当日有据样本中列出候选，结合明日强弱确认。</p>
          </div>
          <span className="outlook-observation-tag">观察候选</span>
        </header>
        {topics.length ? (
          <div className="outlook-topic-grid">
            {topics.map((topic, index) => (
              <article
                className="outlook-topic"
                key={topic.id || topic.topic_id || index}
              >
                <header>
                  <span>{String(index + 1).padStart(2, "0")}</span>
                  <h3>{topic.name}</h3>
                  <small>{topic.kind === "industry" ? "行业" : "题材"}</small>
                </header>
                <p>{topic.reason || "入选依据待核验"}</p>
                <div>
                  {outlookStocks(topic.stocks, date)
                    .slice(0, 3)
                    .map((stock) => (
                      <Candidate
                        key={stock.instrument_id}
                        stock={stock}
                        onStock={onStock}
                        date={date}
                        snapshotId={snapshotId}
                      />
                    ))}
                </div>
              </article>
            ))}
          </div>
        ) : (
          <p className="outlook-empty">
            {watchlist.topics_empty_reason ||
              watchlist.empty_reason ||
              "本日题材归属或确认事件不足，暂不生成核心方向候选。"}
          </p>
        )}
        {!!stocks.length && (
          <details className="outlook-independent-stocks" open>
            <summary>
              个股观察名单 · {stocks.length}只 <ChevronDown size={14} />
            </summary>
            <div>
              {stocks.map((stock) => (
                <Candidate
                  key={stock.instrument_id}
                  stock={stock}
                  onStock={onStock}
                  date={date}
                  snapshotId={snapshotId}
                />
              ))}
            </div>
          </details>
        )}
        <details className="outlook-rule-notes">
          <summary>
            查看判断依据与名单口径 <ChevronDown size={14} />
          </summary>
          <p>{current.notice}</p>
          <Lines items={stage.limitations} />
          <p>
            {typeof watchlist.coverage?.selection_basis === "string"
              ? watchlist.coverage.selection_basis
              : "名单仅覆盖当前留存且核验通过的样本，未知不补零。"}
          </p>
          <p>
            {current.provenance?.mode === "retained_retrospective"
              ? "使用当前留存材料回看所选日期；不是当时已经发布的盘前计划。"
              : "生成时间与材料日期分别记录。"}
          </p>
          {outlookRows(current.rules).map((rule, index) => (
            <p key={index}>
              <strong>{rule.label || rule.phase}</strong>{" "}
              {rule.rule || rule.description}
            </p>
          ))}
        </details>
      </section>
    </div>
  );
}

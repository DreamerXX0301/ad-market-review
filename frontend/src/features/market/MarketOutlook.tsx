import {
  ArrowUpRight,
  ChevronDown,
  Compass,
  Thermometer,
  Crosshair,
} from "lucide-react";
import type { Row } from "../../lib/api";
import { amount, num, percent, tone } from "../../lib/format";
import {
  outlookAction,
  outlookAxisEvidence,
  outlookForDate,
  outlookLines,
  outlookNumber,
  outlookRows,
  outlookStocks,
} from "./marketOutlookPresentation";
import "./MarketOutlook.css";

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
}: {
  stock: Row;
  onStock: (id: string) => void;
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
        <code>{stock.code || stock.instrument_id.slice(0, 6)}</code>
        <span className={tone(stock.change_pct)}>
          {percent(outlookNumber(stock.change_pct))}
        </span>
      </div>
      <p>{stock.reason || "观察依据待核验"}</p>
      <div className="outlook-stock-facts">
        <span>
          {stock.evidence_date} 收盘 {num(outlookNumber(stock.close))}
        </span>
        {Number.isInteger(stock.board_count) && stock.board_count > 0 && (
          <span>{stock.board_count}板样本</span>
        )}
      </div>
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

export default function MarketOutlook({
  data,
  date,
  snapshotId,
  onStock,
}: {
  data?: Row;
  date: string;
  snapshotId: unknown;
  onStock: (id: string) => void;
}) {
  const current = outlookForDate(data, date, snapshotId);
  if (!current)
    return (
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
            {stage.summary || "当前依据尚不足以形成阶段观察。"}
          </p>
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

      <section className="outlook-plan" aria-label="下一交易日执行计划">
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
          <span className="outlook-stance">{plan.label || "等待依据确认"}</span>
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
                <p>{action.reason}</p>
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
        className="outlook-context-grid"
        aria-label="开仓限制与放量修复核验"
      >
        <article
          className={`outlook-gate ${gate.status === "blocked" ? "blocked" : ""}`}
        >
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
          <h3>{amount(outlookNumber(turnover.amount))}</h3>
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
        </article>
      </section>

      <section
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
          <details className="outlook-independent-stocks" open={!topics.length}>
            <summary>
              个股观察名单 · {stocks.length}只 <ChevronDown size={14} />
            </summary>
            <div>
              {stocks.map((stock) => (
                <Candidate
                  key={stock.instrument_id}
                  stock={stock}
                  onStock={onStock}
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

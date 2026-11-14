import { compactQuantity, num, percent } from "../lib/format";
import type { ReactNode } from "react";
import type {
  PriceVolumeProfile,
  PriceVolumeView,
} from "./chartPriceVolumePresentation";
import "./ChartPriceVolume.css";

const levelNames = {
  stop: "结构失效参考",
  support: "支撑参考",
  resistance: "压力参考",
  breakout: "突破参考",
} as const;
const toggleLevels = ["stop", "support", "breakout"] as const;
const signalTone = (state: string) =>
  ["expanded_breakout", "retest_holding"].includes(state)
    ? "up"
    : ["failed_breakout", "support_broken"].includes(state)
      ? "down"
      : "muted";

export default function ChartPriceVolume({
  view,
  visibleIds,
  onToggle,
  onToggleAll,
  actions,
  onProfileChange,
  pressureZone,
}: {
  view: PriceVolumeView | null;
  visibleIds: ReadonlySet<string>;
  onToggle: (id: string, show: boolean) => void;
  onToggleAll?: (show: boolean) => void;
  actions?: ReactNode;
  onProfileChange?: (profile: PriceVolumeProfile) => void;
  pressureZone?: { low: number; high: number };
}) {
  if (!view) return null;
  const shortTerm = view.profile === "short_week_v1";
  const hasPressureReference = !!(view.resistance || view.breakout);
  const hasReferenceLines = toggleLevels.some((key) => view[key]);
  const showReferenceLines = toggleLevels.some(
    (key) => view[key] && visibleIds.has(`price-volume:${key}`),
  );
  const price = (value: number | null) =>
    value == null ? "待核验" : `${num(value)} ${view.unit}`;
  const levelLabel = (key: keyof typeof levelNames) =>
    key === "stop" && shortTerm
      ? "短线风险线"
      : key === "support" && view.supportScope === "historical_120"
        ? "历史结构支撑（120根）"
        : key === "support" && view.supportScope === "recent_20"
          ? "近20日结构支撑"
          : key === "breakout" &&
              ["held", "retest"].includes(view.breakout?.state || "")
            ? "原突破参考"
            : levelNames[key];
  const volumeLabel =
    {
      expanded: "放量",
      contracted: "缩量",
      normal: "量能正常",
    }[view.volume.state] || "量能待核验";
  const breakoutSignalLabel = {
    expanded_breakout: "突破且放量",
    breakout_without_expansion: "突破 · 未达放量门槛",
    breakout_volume_unknown: "突破 · 量能待核验",
  }[view.signal.state];
  const signalLabel =
    breakoutSignalLabel && view.recentBreakout
      ? `${view.recentBreakout.date} ${breakoutSignalLabel}`
      : view.signal.label;
  const renderLevel = (key: keyof typeof levelNames) => {
    const level = view[key];
    const id = `price-volume:${key}`;
    const noSupport =
      key === "support" && view.referencePrice != null && !level;
    const content = (
      <>
        <span className="chart-price-volume-name">
          {levelLabel(key)}
          {key === "support" &&
            level &&
            view.supportDistanceState === "far" && (
              <small className="chart-price-volume-far">距离较远</small>
            )}
        </span>
        <span className="chart-price-volume-value">
          <strong
            className={!level ? "chart-price-volume-unavailable" : undefined}
          >
            {noSupport ? "暂无确认支撑" : price(level?.price ?? null)}
          </strong>
          {level?.distancePct != null && (
            <small title="距参考收盘">
              距收盘 {percent(level.distancePct)}
            </small>
          )}
        </span>
      </>
    );
    return key !== "resistance" && level ? (
      <button
        type="button"
        className={`chart-price-volume-level ${key}`}
        aria-label={`${levelLabel(key)} ${price(level.price)}，切换图上标注`}
        aria-pressed={visibleIds.has(id)}
        title="点击显示或隐藏对应参考线"
        onClick={() => onToggle(id, !visibleIds.has(id))}
      >
        {content}
      </button>
    ) : (
      <div className={`chart-price-volume-level ${key}`}>{content}</div>
    );
  };
  return (
    <section className="chart-price-volume" aria-label="自动量价参考">
      <div className="chart-price-volume-heading">
        <strong>量价参考</strong>
        {onProfileChange && (
          <label className="chart-price-volume-profile">
            <span>参考模式</span>
            <select
              aria-label="量价参考模式"
              value={view.profile}
              onChange={(event) =>
                onProfileChange(event.target.value as PriceVolumeProfile)
              }
            >
              <option value="short_week_v1">一周短线</option>
              <option value="structure">历史结构</option>
            </select>
          </label>
        )}
        <span>
          {view.referenceDate
            ? `${view.referenceDate} · 完整${view.cycle}K`
            : `完整${view.cycle}K待核验`}
        </span>
        <span
          className={signalTone(view.signal.state)}
          title={view.signal.reason}
        >
          {signalLabel}
        </span>
        <span
          className="chart-price-volume-ratio"
          title={
            view.volume.reason ||
            `本${view.cycle}成交量与此前20${view.cycle}均量比较`
          }
        >
          本{view.cycle}
          {volumeLabel}
          {view.volume.ratio != null && ` · ${num(view.volume.ratio)} 倍`}
        </span>
        <div className="chart-price-volume-actions">
          {onToggleAll && hasReferenceLines && (
            <button
              type="button"
              className="btn small"
              aria-pressed={showReferenceLines}
              onClick={() => onToggleAll(!showReferenceLines)}
            >
              {showReferenceLines ? "隐藏参考线" : "显示参考线"}
            </button>
          )}
          {actions}
        </div>
      </div>
      <div className="chart-price-volume-levels">
        {renderLevel("stop")}
        {renderLevel("support")}
        <div
          className="chart-price-volume-pressure"
          role="group"
          aria-label="压力与突破参考"
        >
          {hasPressureReference ? (
            <div className="chart-price-volume-pressure-values">
              {view.resistance && renderLevel("resistance")}
              {renderLevel("breakout")}
            </div>
          ) : (
            <div className="chart-price-volume-level">
              <span className="chart-price-volume-name">
                {pressureZone ? "水平压力区" : "压力与突破参考"}
              </span>
              <span className="chart-price-volume-value">
                {pressureZone ? (
                  <strong className="chart-price-volume-pressure-zone">
                    {num(pressureZone.low)}–{num(pressureZone.high)} {view.unit}
                  </strong>
                ) : (
                  <strong className="chart-price-volume-unavailable">
                    {view.referencePrice == null ? "待核验" : "暂无确认压力"}
                  </strong>
                )}
                {pressureZone && <small>突破位待核验</small>}
              </span>
            </div>
          )}
        </div>
      </div>
      <details className="chart-price-volume-evidence">
        <summary>判断依据</summary>
        <div className="chart-price-volume-evidence-grid">
          <div>
            <strong>价格结构</strong>
            <p>
              参考收盘 {price(view.referencePrice)}
              {view.reason && ` · ${view.reason}`}
            </p>
            <p>
              {shortTerm
                ? `优先近20个完整交易日结构，必要时查找120根内历史支撑。风险线按本图收盘重算，距收盘最多 ${num(view.maxRiskPct)}%。支撑类提醒以设置中已保存近端基准为准。`
                : "按最近120根完整K线观察历史结构，结构失效参考未绑定个人成本或持股期限。"}
            </p>
            {view.referencePrice != null && !view.support && (
              <p>{view.supportReason || "当前结构窗口暂无有效支撑"}</p>
            )}
            {shortTerm && view.support && (
              <p>
                {view.supportDistanceState === "far"
                  ? "距离较远，不能替代短线风险线"
                  : "结构观察，不等于短线风险线"}
              </p>
            )}
            {!hasPressureReference && (
              <p>
                {view.referencePrice == null
                  ? "暂无可核验收盘行情"
                  : shortTerm
                    ? "近20根完整日K暂无有效的单点压力及突破参考"
                    : "当前结构窗口暂无有效的单点压力及突破参考"}
              </p>
            )}
            {view.resistance && !view.breakout && view.atr.value == null && (
              <p>缺少可比波动数据，突破参考待核验。</p>
            )}
            {(["support", "resistance"] as const).map((key) => {
              const level = view[key];
              return level ? (
                <p key={key}>
                  {levelLabel(key)}来自 {level.formedAt} 的拐点，
                  {level.confirmedOn} 确认。
                </p>
              ) : null;
            })}
            {view.signal.reason && <p>{view.signal.reason}</p>}
            {view.recentBreakout && (
              <p>
                最近收盘突破 {view.recentBreakout.date}，参考位{" "}
                {price(view.recentBreakout.price)}。
              </p>
            )}
          </div>
          <div>
            <strong>成交量</strong>
            <p>
              本{view.cycle}成交量{" "}
              {compactQuantity(view.volume.current, view.volume.unit)}
              ，前20{view.cycle}均量{" "}
              {compactQuantity(view.volume.mean, view.volume.unit)}。
            </p>
            {view.recentBreakout && (
              <p>
                突破当{view.cycle}较其前20{view.cycle}均量
                {view.recentBreakout.volumeRatio == null
                  ? "待核验。"
                  : ` ${num(view.recentBreakout.volumeRatio)} 倍。`}
              </p>
            )}
            <p>
              {view.volume.start && view.volume.end
                ? `${view.volume.start} 至 ${view.volume.end}，均量不含本${view.cycle}。`
                : view.volume.reason || "缺少可比成交量，量能判断保持未知。"}
            </p>
          </div>
          <div>
            <strong>{shortTerm ? "短线风险与波动" : "波动缓冲"}</strong>
            <p>
              14{view.cycle}平均真实波幅 {price(view.atr.value)}。
            </p>
            {(["stop", "breakout"] as const).map((key) => {
              const level = view[key];
              return level?.bufferAtr != null && level.bufferValue != null ? (
                <p key={key}>
                  {level.source === "short_risk_boundary" ? (
                    <>
                      短线风险线以 {level.formedAt} 收盘为公式基准，综合1倍ATR、
                      {num(view.maxRiskPct)}%距离上限及可用近端结构计算； 距基准{" "}
                      {price(level.bufferValue)}，不是确认支撑或个人成本止损。
                    </>
                  ) : (
                    <>
                      {levelLabel(key)}以
                      {level.source === "breakout_retest_reference"
                        ? ` ${level.breakoutDate || "已确认"} 突破前的压力位`
                        : key === "stop"
                          ? "支撑位"
                          : "压力位"}{" "}
                      {price(level.basePrice)} 为基准，
                      {key === "stop" ? "下留" : "上留"} {num(level.bufferAtr)}{" "}
                      倍波幅 （{price(level.bufferValue)}）。
                      {level.atrReferenceDate &&
                        `波幅截至 ${level.atrReferenceDate}。`}
                    </>
                  )}
                </p>
              ) : null;
            })}
            {view.atr.value == null && view.atr.reason && (
              <p>{view.atr.reason}</p>
            )}
            {view.notice && <p>{view.notice}</p>}
          </div>
        </div>
      </details>
    </section>
  );
}

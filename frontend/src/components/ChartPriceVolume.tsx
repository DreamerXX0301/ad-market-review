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
}: {
  view: PriceVolumeView | null;
  visibleIds: ReadonlySet<string>;
  onToggle: (id: string, show: boolean) => void;
  onToggleAll?: (show: boolean) => void;
  actions?: ReactNode;
  onProfileChange?: (profile: PriceVolumeProfile) => void;
}) {
  if (!view) return null;
  const price = (value: number | null) =>
    value == null ? "待核验" : `${num(value)} ${view.unit}`;
  const levelLabel = (key: keyof typeof levelNames) =>
    key === "stop" && view.profile === "short_week_v1"
      ? "短线风险线"
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
          {onToggleAll && (view.stop || view.support || view.breakout) && (
            <button
              type="button"
              className="btn small"
              aria-pressed={(["stop", "support", "breakout"] as const).some(
                (key) => view[key] && visibleIds.has(`price-volume:${key}`),
              )}
              onClick={() =>
                onToggleAll(
                  !(["stop", "support", "breakout"] as const).some(
                    (key) => view[key] && visibleIds.has(`price-volume:${key}`),
                  ),
                )
              }
            >
              {(["stop", "support", "breakout"] as const).some(
                (key) => view[key] && visibleIds.has(`price-volume:${key}`),
              )
                ? "隐藏参考线"
                : "显示参考线"}
            </button>
          )}
          {actions}
        </div>
      </div>
      <p className="chart-price-volume-scope">
        {view.profile === "short_week_v1"
          ? `近20个完整交易日结构 · 风险线按本图收盘重算，距收盘最多 ${num(view.maxRiskPct)}%；已开启提醒使用设置中单独保存的跟踪基准。`
          : "最近120根完整K线的历史结构观察；结构失效参考未绑定个人成本或持股期限。"}
      </p>
      <div className="chart-price-volume-levels">
        {(["stop", "support", "resistance", "breakout"] as const).map((key) => {
          const level = view[key];
          const id = `price-volume:${key}`;
          const noConfirmedLevel =
            view.referencePrice != null &&
            !level &&
            (key === "support" || key === "resistance");
          const content = (
            <>
              <span className="chart-price-volume-name">{levelLabel(key)}</span>
              <strong
                className={
                  noConfirmedLevel
                    ? "chart-price-volume-unavailable"
                    : undefined
                }
              >
                {noConfirmedLevel
                  ? key === "support"
                    ? view.profile === "short_week_v1"
                      ? "暂无近端支撑"
                      : "暂无确认支撑"
                    : "暂无确认压力"
                  : price(level?.price ?? null)}
              </strong>
              <small>
                {noConfirmedLevel
                  ? key === "support" && view.supportReason
                    ? view.supportReason
                    : "不推算不存在的价位"
                  : level?.distancePct != null
                    ? `距参考收盘 ${percent(level.distancePct)}`
                    : "暂无可核验价位"}
              </small>
            </>
          );
          return key !== "resistance" && level ? (
            <button
              type="button"
              key={key}
              className={`chart-price-volume-level ${key}`}
              aria-label={`${levelLabel(key)} ${price(level.price)}，切换图上标注`}
              aria-pressed={visibleIds.has(id)}
              title="点击显示或隐藏对应参考线"
              onClick={() => onToggle(id, !visibleIds.has(id))}
            >
              {content}
            </button>
          ) : (
            <div className={`chart-price-volume-level ${key}`} key={key}>
              {content}
            </div>
          );
        })}
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
            <strong>
              {view.profile === "short_week_v1" ? "短线风险与波动" : "波动缓冲"}
            </strong>
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

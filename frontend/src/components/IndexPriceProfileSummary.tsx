import { Crosshair } from "lucide-react";
import type { IndexPriceProfile } from "./indexPriceProfile";
import "./IndexPriceProfile.css";

const priceText = (value: number | null) =>
  typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString("zh-CN", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      })
    : "—";

export default function IndexPriceProfileSummary({
  profile,
  lookback,
  onLookbackChange,
  onLocate,
}: {
  profile: IndexPriceProfile;
  lookback: number;
  onLookbackChange: (value: 20 | 60 | 120) => void;
  onLocate: () => void;
}) {
  const usable = profile.state !== "unavailable" && profile.rows.length > 0;
  const areaFraction =
    profile.totalVisits > 0
      ? `${((profile.valueAreaVisits / profile.totalVisits) * 100).toFixed(1)}%`
      : "—";
  return (
    <section className="index-price-profile" aria-label="指数价格分布">
      <div className="index-profile-heading">
        <div className="index-profile-title">
          <strong>指数价格分布</strong>
          <span>日线区间观察</span>
        </div>
        <div className="index-profile-actions">
          <div className="index-profile-lookbacks" aria-label="分布样本范围">
            {([20, 60, 120] as const).map((value) => (
              <button
                type="button"
                key={value}
                aria-pressed={lookback === value}
                onClick={() => onLookbackChange(value)}
              >
                近{value}报价日
              </button>
            ))}
          </div>
          <button
            type="button"
            className="index-profile-locate"
            onClick={onLocate}
            disabled={!usable}
            title="将K线视窗定位到本次分布的实际样本区间"
          >
            <Crosshair size={13} aria-hidden="true" /> 定位区间
          </button>
        </div>
      </div>
      <div className="index-profile-reading">
        <div className="index-profile-sample">
          <span>
            {profile.startDate || "—"} → {profile.endDate || "—"}
          </span>
          <small>
            实际 {profile.observed} / {profile.requested} 个报价日 · 截至{" "}
            {profile.asOf || "—"}
          </small>
        </div>
        <dl className="index-profile-levels">
          <div className="index-profile-level center">
            <dt>活动中心</dt>
            <dd>{priceText(usable ? profile.center : null)}</dd>
          </div>
          <div className="index-profile-level">
            <dt>主要区上沿</dt>
            <dd>{priceText(usable ? profile.vah : null)}</dd>
          </div>
          <div className="index-profile-level">
            <dt>主要区下沿</dt>
            <dd>{priceText(usable ? profile.val : null)}</dd>
          </div>
        </dl>
      </div>
      {profile.state !== "ready" && profile.reason && (
        <p className="index-profile-status" role="status">
          {profile.reason}
        </p>
      )}
      <details className="index-profile-method">
        <summary>怎么看 · 计算口径</summary>
        <p>
          右侧横条越长，表示越多报价日的最高价至最低价区间覆盖该价格档。金色是活动中心，蓝色是主要活动区，虚线与主图点位对应。
        </p>
        <p>
          将所选真实日线的整体高低区间等分32档（全区间同价时为单档），每个报价日每档至多计一次。主要活动区从活动中心向两侧扩展，覆盖至少70%的“报价日
          × 价格档”访问数。
          {usable
            ? `本次实际覆盖 ${areaFraction}（${profile.valueAreaVisits} / ${profile.totalVisits} 次）。`
            : "本次分布不可用，覆盖率保持未知。"}
          样本不足按实际数量展示，数据缺口不补。
        </p>
        <p>
          日线高低区间不能证明中间每个价位实际成交。这是日线区间的近似观察，不是盘中TPO、真实按价成交量、筹码或估值，也不表示资金净流入、支撑有效性或买卖信号；不改变策略、跟踪与回测口径。
        </p>
      </details>
    </section>
  );
}

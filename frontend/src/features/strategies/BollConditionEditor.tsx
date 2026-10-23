import {
  BOLL_MODES,
  bollConfig,
  bollConfigError,
  bollSummary,
} from "./historicalRules";
import type { BollConfig } from "./historicalRules";
import "./BollConditionEditor.css";

export default function BollConditionEditor({
  value,
  onChange,
  label,
  period,
}: {
  value: unknown;
  onChange: (value: BollConfig) => void;
  label: string;
  period?: string;
}) {
  const config = bollConfig(value);
  const patch = (key: string, next: unknown) =>
    onChange({ ...config, [key]: next });
  const mode = BOLL_MODES.find((item) => item.value === config.mode);
  const error = bollConfigError(value);
  const numberInput = (
    key: string,
    title: string,
    min: number,
    max: number,
    step: number | "any" = 1,
  ) => (
    <label>
      <span>{title}</span>
      <input
        type="number"
        aria-label={`${label}${title}`}
        min={min}
        max={max}
        step={step}
        value={config[key] ?? ""}
        onChange={(event) =>
          patch(
            key,
            event.target.value === "" ? "" : Number(event.target.value),
          )
        }
      />
    </label>
  );
  const needsSqueeze = config.mode === "squeeze" || config.require_squeeze;
  return (
    <div className="boll-condition-editor">
      <div className="boll-parameter-grid">
        <label className="boll-signal-mode">
          <span>判断信号</span>
          <select
            aria-label={`${label}BOLL判断信号`}
            value={config.mode}
            onChange={(event) => patch("mode", event.target.value)}
          >
            {!mode && <option value={config.mode}>未识别信号</option>}
            {BOLL_MODES.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
        {numberInput("band_period", "BOLL周期（根）", 2, 250)}
        {numberInput("deviations", "标准差倍数", 0.1, 10, "any")}
        <label>
          <span>中轨方向</span>
          <select
            aria-label={`${label}BOLL中轨方向`}
            value={config.middle_direction}
            onChange={(event) => patch("middle_direction", event.target.value)}
          >
            <option value="any">不限</option>
            <option value="rising">中轨上行</option>
            <option value="falling">中轨下行</option>
          </select>
        </label>
        {config.middle_direction !== "any" &&
          numberInput("slope_periods", "中轨比较间隔（期）", 1, 60)}
      </div>
      <div className="boll-filter-switches">
        {config.mode !== "squeeze" && (
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={config.require_squeeze === true}
              aria-label={`${label}BOLL此前出现收口`}
              onChange={(event) =>
                patch("require_squeeze", event.target.checked)
              }
            />
            要求信号前出现收口
          </label>
        )}
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={config.require_expansion === true}
            aria-label={`${label}BOLL带宽扩大`}
            onChange={(event) =>
              patch("require_expansion", event.target.checked)
            }
          />
          要求当期带宽较前期扩大
        </label>
      </div>
      {needsSqueeze && (
        <div className="boll-parameter-grid boll-squeeze-parameters">
          {config.mode !== "squeeze" &&
            numberInput("recent_squeeze_periods", "此前收口窗口（期）", 1, 60)}
          {numberInput("rank_window", "分位回看窗口（期）", 10, 750)}
          {numberInput("squeeze_percentile", "收口分位（%）", 1, 100, "any")}
        </div>
      )}
      <p className="boll-rule-preview">
        {bollSummary({ field: "boll_signal", value }, period)}
      </p>
      <small className="muted">
        {mode?.description || "请选择有效信号"}。上下轨 = 收盘均值 ± 标准差倍数
        × 总体标准差；带宽 =（上轨 − 下轨）/ 中轨。
        {needsSqueeze &&
          "每一期收口阈值只用该期之前的带宽计算；信号前收口不含信号当期。"}
        窗口均按所选周期计；分位回看须有完整历史，预热不足保留待核验。
        按收盘确认，周/月只用完整K线。可在同组继续添加放量、MACD、KDJ条件；满足全部或任意一项沿用条件组设置。
      </small>
      {error && (
        <small className="danger-text" role="alert">
          {error}；修正后才能保存。
        </small>
      )}
    </div>
  );
}

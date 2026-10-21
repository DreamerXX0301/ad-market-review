import type { Row } from "../../lib/api";

export default function PolicyScope({
  strategy,
  checked,
  onChange,
}: {
  strategy?: Row;
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <div>
      <label className="checkbox-label">
        <input
          type="checkbox"
          checked={checked}
          onChange={(event) => onChange(event.target.checked)}
        />
        同步到沿用默认政策的当前跟踪轮次
      </label>
      <p className="form-help">
        {checked
          ? `预计更新 ${strategy?.default_policy_tracking_count ?? 0} 个轮次，保留 ${strategy?.custom_policy_tracking_count ?? 0} 个单独设置过政策的轮次。`
          : "仅用于之后新入池的轮次，当前跟踪期限与条件保持原设置。"}
        {checked &&
          "从当前业务日生效，进度随新期限更新；入池日、价格及历史事件保留。已达到新期限时，手动核对后才归入结束历史。"}
      </p>
    </div>
  );
}

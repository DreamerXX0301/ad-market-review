import type { Row } from "../../lib/api";
import { candidatePriceBasis } from "./strategyPriceBasis";
import type { StockStrategyPriceBasis } from "./strategyPriceBasis";

export function StrategyPriceBasisSelect({
  value,
  onChange,
}: {
  value: StockStrategyPriceBasis;
  onChange: (value: StockStrategyPriceBasis) => void;
}) {
  return (
    <div className="form-grid">
      <label>
        技术指标价格口径
        <select
          value={value}
          onChange={(event) =>
            onChange(event.target.value === "forward" ? "forward" : "none")
          }
        >
          <option value="forward">本地前复权</option>
          <option value="none">未复权</option>
        </select>
      </label>
      <div className="form-help">
        {value === "forward"
          ? "以运行观察日为锚，技术指标与历史事件价格比较使用前复权价格；复权证据不足的股票保持待核验。"
          : "使用来源原始价格；遇除权派息会重新预热，周/月指标仍需完整可比周期。"}
        <br />
        图表偏好与策略版本分别保存，修改后创建新版本，已保存结果保持原口径。
      </div>
    </div>
  );
}

export function CandidatePriceBasis({
  row,
  run,
}: {
  row: Row;
  run: Row | null;
}) {
  const view = candidatePriceBasis(row, run);
  return (
    <span
      className={view.unavailable ? "muted" : undefined}
      title={view.reason}
    >
      {view.label}
    </span>
  );
}

import { useState } from "react";
import { useApi, list } from "../lib/api";
import type { Row } from "../lib/api";
import type { ChartEventBaseline } from "./chartDisplay";
import {
  resolveStockEventBaseline,
  resolveStockHistoricalReference,
} from "./eventBaselineSelection";
import { num } from "../lib/format";
import "./StockEventBaseline.css";

export function useStockEventBaseline(
  id: string,
  asOf: string | undefined,
  enabled: boolean,
  explicit?: ChartEventBaseline,
  navigationHistoricalChecks?: unknown,
  contextKey = "",
) {
  const scope = `${id}:${asOf || "latest"}:${explicit?.roundId || ""}:${contextKey}`;
  const [selection, setSelection] = useState({
    scope: "",
    value: "",
    referenceId: "",
    referenceChanged: false,
  });
  const result = useApi<Row>(
    enabled
      ? `/instruments/${encodeURIComponent(id)}/tracking-baselines${asOf ? "?as_of=" + encodeURIComponent(asOf) : ""}`
      : null,
  );
  const payload =
    result.data?.instrument_id === id.toUpperCase() ? result.data : null;
  const items = list(payload);
  const selectedId =
    selection.scope === scope
      ? selection.value
      : explicit
        ? explicit.roundId || "context"
        : payload?.default_round_id;
  const baseline = resolveStockEventBaseline(items, selectedId, explicit);
  // Hiding a visual reference must not discard its frozen collection source.
  const sourceId =
    selection.scope === scope && selectedId === "none"
      ? selection.referenceId ||
        (explicit ? explicit.roundId || "context" : payload?.default_round_id)
      : selectedId;
  const sourceBaseline = resolveStockEventBaseline(items, sourceId, explicit);
  const historicalReference = resolveStockHistoricalReference(
    items,
    sourceId,
    navigationHistoricalChecks,
    selection.scope === scope && selection.referenceChanged,
  );
  return {
    items,
    selectedId,
    baseline,
    sourceBaseline,
    historicalChecks: historicalReference.checks,
    historicalChecksReason: historicalReference.reason,
    explicit,
    loading: result.loading,
    error: result.error,
    truncated: payload?.truncated,
    onChange: (value: string) =>
      setSelection({
        scope,
        value,
        referenceId: value === "none" ? sourceId || "" : value,
        referenceChanged:
          value !== "none" ||
          (selection.scope === scope && selection.referenceChanged),
      }),
  };
}

export default function StockEventBaseline({
  state,
}: {
  state: ReturnType<typeof useStockEventBaseline>;
}) {
  if (!state.items.length && !state.explicit && !state.loading && !state.error)
    return null;
  const missingExplicit =
    state.explicit &&
    (!state.explicit.roundId ||
      !state.items.some((item) => item.round_id === state.explicit?.roundId));
  return (
    <div className="stock-event-picker">
      <label>
        <span>事件参照</span>
        <select
          aria-label="选择个股跟踪事件基准"
          value={state.selectedId || "none"}
          disabled={state.loading && !state.explicit}
          onChange={(event) => state.onChange(event.target.value)}
        >
          <option value="none">不显示事件参照</option>
          {missingExplicit && (
            <option value={state.explicit!.roundId || "context"}>
              当前轮次 · {state.explicit!.date} ·{" "}
              {num(state.explicit!.rawClose)} 元
            </option>
          )}
          {state.items.map((round) => (
            <option key={round.round_id} value={round.round_id}>
              {round.strategy_name} · {round.entry_business_date} ·{" "}
              {num(round.entry_price)} 元
              {round.status === "tracking" ? " · 跟踪中" : " · 历史轮次"}
            </option>
          ))}
        </select>
      </label>
      <small role={state.error ? "status" : undefined}>
        {state.error
          ? `事件记录读取失败：${state.error}`
          : state.loading
            ? "正在读取本股票的跟踪记录…"
            : state.truncated
              ? "最近100个轮次 · 冻结入池收盘价"
              : state.selectedId === "none" && state.sourceBaseline
                ? "图线已隐藏，收藏仍保留此轮次的事件基准"
                : "已保存的跟踪轮次 · 冻结入池收盘价"}
      </small>
    </div>
  );
}

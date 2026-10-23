import { useEffect, useRef, useState } from "react";
import { api } from "../../lib/api";
import type { Row } from "../../lib/api";

export function backtestStrategyOptions(rows: Row[]): Row[] {
  return rows.filter((row) => row.strategy_id && !row.deleted && !row.archived);
}
export function resolvedBacktestStrategyId(
  rows: Row[],
  current: string,
): string {
  const options = backtestStrategyOptions(rows);
  return options.some((row) => row.strategy_id === current)
    ? current
    : options[0]?.strategy_id || "";
}

/** Bind bounds and failures to the current visible strategy and data generation. */
export default function useBacktestAvailability({
  strategy,
  ready,
  dataVersion,
}: {
  strategy?: Row;
  ready: boolean;
  dataVersion?: string;
}) {
  const identity =
    ready && strategy?.strategy_id
      ? `${strategy.strategy_id}:${strategy.version || ""}`
      : "";
  const scope = identity ? JSON.stringify([identity, dataVersion || ""]) : "";
  const currentScope = useRef(scope);
  currentScope.current = scope;
  const successfulIdentity = useRef("");
  const [response, setResponse] = useState<{ scope: string; data: Row } | null>(
    null,
  );
  const [problem, setProblem] = useState<{
    scope: string;
    message: string;
  } | null>(null);
  const [pending, setPending] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  useEffect(() => {
    const request = new AbortController();
    setResponse(null);
    setProblem(null);
    setPending(scope);
    if (!scope) return () => request.abort();
    const keepDates = successfulIdentity.current === identity;
    const current = () =>
      !request.signal.aborted && currentScope.current === scope;
    api<Row>("/backtests/availability", {
      method: "POST",
      body: JSON.stringify({ strategy_id: strategy!.strategy_id }),
      signal: request.signal,
    })
      .then((data) => {
        if (!current()) return;
        setResponse({ scope, data });
        successfulIdentity.current = identity;
        setStart((value) =>
          keepDates && value && value >= data.min_date && value <= data.max_date
            ? value
            : data.default_start || data.min_date || "",
        );
        setEnd((value) =>
          keepDates && value && value >= data.min_date && value <= data.max_date
            ? value
            : data.default_end || data.max_date || "",
        );
      })
      .catch((error) => {
        if (current()) setProblem({ scope, message: error.message });
      })
      .finally(() => {
        if (current()) setPending("");
      });
    return () => request.abort();
  }, [scope, identity]);
  const availability =
    scope && response?.scope === scope ? response.data : null;
  const error = scope && problem?.scope === scope ? problem.message : "";
  const checking = !!scope && (pending === scope || (!availability && !error));
  return {
    availability,
    error,
    checking,
    start,
    setStart,
    end,
    setEnd,
    canRun:
      ready &&
      !!strategy?.strategy_id &&
      !checking &&
      availability?.available === true,
  };
}

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../lib/api";
import type { Row } from "../../lib/api";

/** Restore frozen results with GET only; strategy edits and daily changes never run selection. */
export default function useSavedStrategyRun(
  target: "stock" | "sector",
  strategy?: Row,
) {
  const [storedRun, setStoredRun] = useState<Row | null>(null);
  const [restoring, setRestoring] = useState(false);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const currentStrategy = useRef(strategy?.strategy_id);
  currentStrategy.current = strategy?.strategy_id;
  const currentRun = useRef<Row | null>(storedRun);
  currentRun.current = storedRun;
  const automatic = useRef<AbortController | null>(null);
  const strategyId = strategy?.strategy_id;
  const latestRunId = strategy?.latest_run?.run_id;
  const setRun = useCallback((value: Row | null) => {
    if (value && value.strategy_id !== currentStrategy.current) return;
    automatic.current?.abort();
    setStoredRun(value);
    currentRun.current = value;
    setRestoring(false);
    setError("");
  }, []);
  const retry = useCallback(() => setRevision((value) => value + 1), []);
  useEffect(() => {
    setError("");
    if (
      !strategyId ||
      !latestRunId ||
      currentRun.current?.run_id === latestRunId
    ) {
      setRestoring(false);
      return;
    }
    const abort = new AbortController();
    automatic.current = abort;
    setRestoring(true);
    const path =
      target === "sector" ? "/sector-strategy-runs/" : "/strategy-runs/";
    api<Row>(path + encodeURIComponent(latestRunId), { signal: abort.signal })
      .then((result) => {
        if (!abort.signal.aborted && currentStrategy.current === strategyId) {
          if (result.strategy_id !== strategyId)
            throw new Error("已保存运行与当前策略不一致，请重新读取");
          setStoredRun(result);
          currentRun.current = result;
        }
      })
      .catch((problem: any) => {
        if (!abort.signal.aborted)
          setError(`读取已保存结果失败：${problem.message}`);
      })
      .finally(() => {
        if (!abort.signal.aborted) setRestoring(false);
      });
    return () => abort.abort();
  }, [target, strategyId, latestRunId, revision]);
  const run = storedRun?.strategy_id === strategyId ? storedRun : null;
  return {
    run,
    setRun,
    restoring: restoring || (!!latestRunId && !run && !error),
    error,
    retry,
  };
}

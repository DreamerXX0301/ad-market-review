import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../lib/api";
import type { Row } from "../../lib/api";
import {
  invalidateSavedStrategy,
  readSavedRun,
  rememberSavedRun,
  savedRunPath,
  savedStrategyScope,
} from "./savedRunCache";
import type { SavedRunTarget } from "./savedRunCache";

/** Only GET saved results; opening a page or changing a tab never runs selection. */
export default function useSavedStrategyRun(
  target: SavedRunTarget,
  strategy?: Row,
  decision = "matched",
  inputKey = "",
) {
  const [stored, setStored] = useState<{
    target: SavedRunTarget;
    value: Row;
  } | null>(null);
  const [manual, setManual] = useState<{
    context: string;
    id: string;
    nonce: number;
  } | null>(null);
  const [verified, setVerified] = useState<{
    context: string;
    request: string;
    scope: string;
    latest?: string;
  } | null>(null);
  const [restoring, setRestoring] = useState(false);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const strategyId = strategy?.strategy_id;
  const context = `${target}:${strategyId || ""}`;
  const request =
    savedStrategyScope(target, strategy, inputKey) + ":" + revision;
  const current = useRef({ context, strategyId, target, decision, request });
  current.current = { context, strategyId, target, decision, request };
  const automatic = useRef<AbortController | null>(null);
  const verification = useRef<AbortController | null>(null);
  const verifiedScope = useRef<{ context: string; scope: string } | null>(null);
  const incrementBlocked = useRef(false);
  const desiredRunId =
    manual?.context === context
      ? manual.id
      : verified?.context === context
        ? verified.latest
        : undefined;
  const chosenManual = manual?.context === context ? manual : null;

  const loadRun = useCallback((id: string) => {
    if (!current.current.strategyId || !id) return;
    automatic.current?.abort();
    setError("");
    setRestoring(true);
    setManual((old) => ({
      context: current.current.context,
      id,
      nonce: (old?.nonce || 0) + 1,
    }));
  }, []);
  const setRun = useCallback((value: Row | null) => {
    if (value && value.strategy_id !== current.current.strategyId) return;
    automatic.current?.abort();
    if (!value) {
      setStored(null);
      return;
    }
    const { target: activeTarget, context: activeContext } = current.current;
    const scope =
      verifiedScope.current?.context === activeContext
        ? verifiedScope.current.scope
        : "";
    const part = rememberSavedRun(activeTarget, value, "matched", scope);
    setStored({ target: activeTarget, value: part });
    setManual((old) => ({
      context: activeContext,
      id: value.run_id,
      nonce: (old?.nonce || 0) + 1,
    }));
    setRestoring(false);
    setError("");
  }, []);
  const retry = useCallback(() => {
    if (current.current.strategyId)
      invalidateSavedStrategy(
        current.current.target,
        current.current.strategyId,
      );
    setRevision((value) => value + 1);
  }, []);
  useEffect(() => {
    const unlocked = () => {
      if (incrementBlocked.current) {
        incrementBlocked.current = false;
        retry(); // Only the saved-result GETs below; never selection POST.
      }
    };
    window.addEventListener("workbench-increment-unlocked", unlocked);
    return () =>
      window.removeEventListener("workbench-increment-unlocked", unlocked);
  }, [retry]);

  // Recheck deletion/current latest with a small GET before cached results can
  // be reused after switching strategies or changing the current inputs.
  useEffect(() => {
    automatic.current?.abort();
    setError("");
    setManual((old) => (old?.context === context ? old : null));
    if (!strategyId) {
      setVerified(null);
      setRestoring(false);
      return;
    }
    const abort = new AbortController();
    verification.current = abort;
    setRestoring(true);
    const path = target === "sector" ? "/sector-strategies/" : "/strategies/";
    api<Row>(path + encodeURIComponent(strategyId), { signal: abort.signal })
      .then((value) => {
        if (
          abort.signal.aborted ||
          current.current.context !== context ||
          current.current.request !== request
        )
          return;
        incrementBlocked.current = false;
        if (value.strategy_id !== strategyId || value.deleted_at_ms) {
          setStored(null);
          throw new Error("策略已变化或移入回收站，请刷新列表");
        }
        const scope = savedStrategyScope(target, value, inputKey);
        if (
          verifiedScope.current?.context === context &&
          verifiedScope.current.scope !== scope
        )
          invalidateSavedStrategy(target, strategyId);
        verifiedScope.current = { context, scope };
        setVerified({
          context,
          request,
          scope,
          latest: value.latest_run?.run_id,
        });
        if (!value.latest_run?.run_id && !chosenManual) {
          setStored(null);
          setRestoring(false);
        }
      })
      .catch((problem: any) => {
        if (!abort.signal.aborted && current.current.context === context) {
          if (problem.incrementLocked) incrementBlocked.current = true;
          invalidateSavedStrategy(target, strategyId);
          if (problem.status === 404 || problem.status === 410) {
            setStored(null);
            setVerified(null);
          }
          setError(`读取已保存结果失败：${problem.message}`);
          setRestoring(false);
        }
      });
    return () => abort.abort();
  }, [target, strategyId, context, request, inputKey]);

  useEffect(() => {
    if (
      !strategyId ||
      verified?.context !== context ||
      verified.request !== request ||
      !desiredRunId
    )
      return;
    const cached = readSavedRun(target, desiredRunId, decision, verified.scope);
    if (cached) {
      setStored({ target, value: cached });
      setRestoring(false);
      setError("");
      return;
    }
    const abort = new AbortController();
    automatic.current = abort;
    setRestoring(true);
    setError("");
    api<Row>(savedRunPath(target, desiredRunId, decision), {
      signal: abort.signal,
    })
      .then((value) => {
        if (
          abort.signal.aborted ||
          current.current.context !== context ||
          current.current.decision !== decision ||
          current.current.request !== request
        )
          return;
        if (value.strategy_id !== strategyId || value.run_id !== desiredRunId)
          throw new Error("已保存运行与当前策略不一致，请重新读取");
        const part = rememberSavedRun(target, value, decision, verified.scope);
        setStored({ target, value: part });
      })
      .catch((problem: any) => {
        if (!abort.signal.aborted) {
          if (problem.incrementLocked) incrementBlocked.current = true;
          if (problem.status === 404 || problem.status === 410) {
            invalidateSavedStrategy(target, strategyId);
            setStored(null);
          }
          setError(`读取已保存结果失败：${problem.message}`);
        }
      })
      .finally(() => {
        if (!abort.signal.aborted) setRestoring(false);
      });
    return () => abort.abort();
  }, [
    target,
    strategyId,
    context,
    request,
    desiredRunId,
    chosenManual?.nonce,
    decision,
    verified,
  ]);

  useEffect(
    () => () => {
      automatic.current?.abort();
      verification.current?.abort();
    },
    [],
  );
  const run =
    stored?.target === target && stored.value.strategy_id === strategyId
      ? stored.value
      : null;
  const candidatesReady =
    !!run &&
    verified?.context === context &&
    verified.request === request &&
    run.run_id === desiredRunId &&
    run.loaded_decision === decision;
  return {
    run,
    setRun,
    loadRun,
    candidatesReady,
    restoring:
      restoring ||
      (!!strategyId &&
        !error &&
        (verified?.request !== request ||
          (!!desiredRunId && !candidatesReady))),
    error,
    retry,
  };
}

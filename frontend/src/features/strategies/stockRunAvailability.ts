type Row = Record<string, any>;

/** The server binds this state to the published business day and effective inputs. */
export function stockRunGate(
  strategy?: Row,
  businessDate?: string,
  savedRun?: Row | null,
) {
  if (!strategy || strategy.archived)
    return {
      blocked: true,
      completed: false,
      reason: strategy ? "策略已归档，不能运行" : "请选择策略",
      runId: null,
    };
  const state = strategy.run_availability;
  if (state && typeof state === "object") {
    const completed = state.state === "completed";
    const ready = state.state === "ready" && state.can_run === true;
    return {
      blocked: !ready && !completed,
      completed,
      reason:
        state.reason ||
        (completed
          ? "本行情日相同规则已运行，可查看已保存结果；修改条件后可以重新运行。"
          : ready
            ? ""
            : "暂时不能运行，请刷新策略状态"),
      runId:
        typeof state.completed_run_id === "string"
          ? state.completed_run_id
          : null,
    };
  }
  // A full saved receipt can improve an older API's read-only preview. New
  // services always supply the authoritative state above, including input changes.
  const completed =
    !!businessDate &&
    savedRun?.strategy_id === strategy.strategy_id &&
    Number.isInteger(savedRun?.strategy_version) &&
    savedRun?.strategy_version === strategy.version &&
    savedRun?.business_date === businessDate &&
    savedRun?.status === "completed";
  return {
    blocked: false,
    completed,
    reason: completed
      ? "本行情日当前规则已运行，可查看已保存结果；修改条件后可以重新运行。"
      : "",
    runId: completed ? savedRun!.run_id || null : null,
  };
}

export function stockRunnableStrategies(strategies: Row[]) {
  return strategies.filter((item) => {
    const state = stockRunGate(item);
    return !state.blocked && !state.completed;
  });
}

export function stockSelectableStrategies(strategies: Row[]) {
  return strategies.filter((item) => !stockRunGate(item).blocked);
}

type Receipt = Record<string, any>;
export function incrementReceiptForSession(
  data: Receipt | null,
  session: { requestKey: string; taskId?: string } | null,
) {
  return session &&
    data?.request_key === session.requestKey &&
    (!session.taskId || data.root_task_id === session.taskId)
    ? data
    : null;
}
export function incrementProgressView(data: Receipt | null) {
  const tasks: Receipt[] = Array.isArray(data?.tasks) ? data.tasks : [];
  const done = data?.can_acknowledge === true;
  const active =
    tasks.find((task) => task.status === "running") ||
    tasks.find((task) => task.status === "queued");
  const finished = tasks.filter((task) =>
    ["succeeded", "no_change", "partial"].includes(task.status),
  ).length;
  return {
    tasks,
    done,
    active,
    title: !done
      ? "正在更新盘后数据"
      : (
          {
            succeeded: "盘后增量已完成",
            no_change: "数据已是最新",
            partial: "增量已结束，部分数据仍有缺口",
            failed: "增量任务失败",
            cancelled: "增量任务已取消",
            paused: "增量任务已暂停",
            blocked: "后续任务未能完成排队",
          } as Receipt
        )[data?.status] || "本次增量已结束",
    completedLabel: `已完成 ${finished} / ${tasks.length} 项已排队任务`,
    progress:
      active &&
      typeof active.progress === "number" &&
      Number.isFinite(active.progress) &&
      active.progress >= 0 &&
      active.progress <= 100
        ? active.progress
        : null,
    estimate:
      !done &&
      typeof data?.estimate?.remaining_max_ms === "number" &&
      data.estimate.remaining_max_ms > 0
        ? data.estimate
        : null,
  };
}

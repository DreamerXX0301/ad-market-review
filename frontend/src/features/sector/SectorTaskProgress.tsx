import { useEffect } from "react";
import { list, useApi } from "../../lib/api";
import type { Row } from "../../lib/api";
import { Spinner } from "../../components/UI";

export default function SectorTaskProgress({
  requestKey,
}: {
  requestKey: string;
}) {
  const tasks = useApi<Row>(requestKey ? "/tasks?limit=20" : null);
  useEffect(() => {
    if (!requestKey) return;
    const timer = window.setInterval(tasks.reload, 2000);
    return () => window.clearInterval(timer);
  }, [requestKey, tasks.reload]);
  const task = list(tasks.data).find(
    (item) =>
      item.request_key === requestKey &&
      item.parameters?.target_type === "sector",
  );
  const progress =
    typeof task?.progress === "number" && Number.isFinite(task.progress)
      ? Math.max(0, Math.min(100, task.progress))
      : null;
  const count = task?.actual_completed_batches,
    total = task?.actual_total_batches;
  return (
    <div className="sector-task-progress" role="status">
      <div>
        <Spinner />
        <strong>{task?.stage || "正在创建板块计算任务"}</strong>
        {progress != null && <b>{progress}%</b>}
      </div>
      {progress != null && <progress max={100} value={progress} />}
      <small>
        {task?.message || "等待服务端返回真实进度"}
        {typeof count === "number" && typeof total === "number"
          ? ` · 已核验 ${count}/${total} 个板块`
          : ""}
      </small>
      {tasks.error && (
        <small>进度暂未读取：{tasks.error}。运行请求仍在等待结果。</small>
      )}
    </div>
  );
}

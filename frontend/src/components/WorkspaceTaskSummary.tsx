import type { Row } from "../lib/api";

export default function WorkspaceTaskSummary({
  workspace,
}: {
  workspace?: Row | null;
}) {
  if (!workspace || workspace.state === "unknown") return null;
  return (
    <section
      className="increment-workspace-summary"
      aria-label="本次策略执行结果"
    >
      <strong>{workspace.message}</strong>
      <p>
        已完成 {workspace.completed_strategies} 条策略
        {workspace.state === "running" ? "，后续数量以实际执行回执为准" : ""}
      </p>
      {workspace.strategies?.map((row: Row) => (
        <p key={row.strategy_id}>
          <b>{row.name || row.strategy_id}</b> ·{" "}
          {row.error ||
            `命中 ${row.matched ?? "—"}，待核验 ${row.unknown ?? "—"}，新入池 ${row.added_count ?? "—"}`}
        </p>
      ))}
      {workspace.tracking && (
        <small>
          跟踪核对 {workspace.tracking.reviewed ?? "—"} 条 · 跳过{" "}
          {workspace.tracking.skipped ?? "—"} 条
        </small>
      )}
    </section>
  );
}

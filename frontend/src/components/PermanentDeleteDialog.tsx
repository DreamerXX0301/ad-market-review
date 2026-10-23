import { useEffect, useRef, useState } from "react";
import { RotateCcw, ShieldCheck, Trash2 } from "lucide-react";
import { list, post, useApi } from "../lib/api";
import type { Row } from "../lib/api";
import { ErrorBox, Loading, Modal, Spinner } from "./UI";
import {
  canConfirmPurge,
  hasWatchlistProtection,
  matchingPurgeStatus,
  purgeBytes,
  purgeCount,
  purgeIsWorking,
  purgePhase,
  purgeScopeRows,
  recyclePageOffset,
} from "./permanentDeletePresentation";
import type { PurgeTarget } from "./permanentDeletePresentation";
import "./PermanentDeleteDialog.css";

export function PermanentDeletePreview({
  preview,
  onStock,
}: {
  preview: Row;
  onStock?: (id: string) => void;
}) {
  const protectedByWatchlist = hasWatchlistProtection(preview);
  return (
    <>
      <div className="purge-target-name">{preview.name}</div>
      {protectedByWatchlist ? (
        <div className="purge-protection" role="status">
          <strong>
            <ShieldCheck size={16} />
            自选保护，无法永久删除
          </strong>
          <p>
            该策略历史或当前跟踪中，有{" "}
            {purgeCount(preview.protected_watchlist_count)}{" "}
            只股票仍在自选中。为保留相关事件和基准价，整条策略保持原状。
          </p>
          <details>
            <summary>查看受保护股票</summary>
            {preview.protected_watchlist_count >
              list(preview.protected_watchlist).length && (
              <p>
                当前展示前 {list(preview.protected_watchlist).length} 只；全部{" "}
                {purgeCount(preview.protected_watchlist_count)} 只均受到保护。
              </p>
            )}
            <ul>
              {list(preview.protected_watchlist).map((stock) => (
                <li key={stock.instrument_id}>
                  {onStock ? (
                    <button
                      type="button"
                      className="purge-stock-link"
                      onClick={() => onStock(stock.instrument_id)}
                    >
                      {stock.name || stock.instrument_id}
                      {stock.name && stock.name !== stock.instrument_id && (
                        <span>{stock.instrument_id}</span>
                      )}
                    </button>
                  ) : stock.name && stock.name !== stock.instrument_id ? (
                    `${stock.name} ${stock.instrument_id}`
                  ) : (
                    stock.instrument_id
                  )}
                </li>
              ))}
            </ul>
          </details>
        </div>
      ) : preview.can_purge !== true ? (
        <div className="purge-protection" role="status">
          <strong>当前无法永久删除</strong>
          <p>{preview.blocked_reason || "请等待相关任务结束后重新核验。"}</p>
        </div>
      ) : (
        <p className="purge-warning">
          将永久删除以下专属记录及可回收文件，完成后无法从回收站恢复。
        </p>
      )}
      <dl
        className="purge-scope"
        aria-label={
          protectedByWatchlist || !preview.can_purge
            ? "关联记录（未执行删除）"
            : "本次永久删除范围"
        }
      >
        {purgeScopeRows(preview).map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
      <div className="purge-file-scope">
        <span>可回收专属文件</span>
        <strong>
          {purgeCount(preview.files?.exclusive_count)} 个 ·{" "}
          {purgeBytes(preview.files?.exclusive_bytes)}
        </strong>
        <small>
          共享文件 {purgeCount(preview.files?.shared_count)}{" "}
          个保留；移除文件以清理结果为准，备份保留，SQLite 不会自动缩小。
        </small>
      </div>
      <div className="purge-retained">
        <strong>继续保留</strong>
        <ul>
          {(Array.isArray(preview.retained) ? preview.retained : []).map(
            (value: string, index: number) => (
              <li key={index}>{value}</li>
            ),
          )}
        </ul>
      </div>
    </>
  );
}

export function PermanentDeleteStatus({ job }: { job: Row }) {
  return (
    <div
      className={`purge-job-status ${job.status === "completed" ? "complete" : ""}`}
      role="status"
      aria-live="polite"
    >
      <strong>
        {purgeIsWorking(job.status) && <Spinner />}
        {purgePhase(job.status)}
      </strong>
      <p>
        {job.status === "completed"
          ? "专属业务记录与本次可回收文件已清理完成。"
          : job.business_deleted === true
            ? job.status === "failed"
              ? "业务记录已永久删除，文件清理尚未完成；重试将继续清理原范围内的文件。"
              : "业务记录已永久删除，后台正在继续清理本次范围内的文件。"
            : job.status === "failed"
              ? "业务记录尚未删除。可重试原范围；若范围已变化，请重新核验并确认。"
              : "由后台核验并清理本次范围，关闭此窗口后仍会继续。"}
      </p>
      <dl className="purge-scope">
        <div>
          <dt>已清理文件</dt>
          <dd>{purgeCount(job.removed_file_count)}</dd>
        </div>
        <div>
          <dt>实际移除文件大小</dt>
          <dd>{purgeBytes(job.removed_file_bytes)}</dd>
        </div>
        <div>
          <dt>待清理文件</dt>
          <dd>{purgeCount(job.remaining_file_count)}</dd>
        </div>
      </dl>
      {job.status === "failed" && (
        <ErrorBox message={job.error || "清理未完成，请重试原清理任务。"} />
      )}
    </div>
  );
}

export default function PermanentDeleteDialog({
  target,
  onClose,
  onChanged,
  onStock,
}: {
  target: PurgeTarget;
  onClose: () => void;
  onChanged: () => void;
  onStock?: (id: string) => void;
}) {
  const [purgeId, setPurgeId] = useState(target.purgeId || "");
  const [typed, setTyped] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [actionError, setActionError] = useState("");
  const [now, setNow] = useState(Date.now);
  const [previewInvalidated, setPreviewInvalidated] = useState(false);
  const preview = useApi<Row>(
    !purgeId
      ? `/${target.kind === "strategy" ? "strategies" : "backtests"}/${encodeURIComponent(target.id)}/purge-preview`
      : null,
  );
  const status = useApi<Row>(
    purgeId ? `/workspace-purges/${encodeURIComponent(purgeId)}` : null,
  );
  const job = matchingPurgeStatus(status.data, purgeId);
  const notified = useRef("");
  const changed = useRef(onChanged);
  changed.current = onChanged;
  const mutationPending = useRef(false);
  useEffect(() => {
    if (!preview.data || purgeId) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [preview.data, purgeId]);
  useEffect(() => {
    if (!job || !purgeIsWorking(job.status)) return;
    const timer = setTimeout(status.reload, 1500);
    return () => clearTimeout(timer);
  }, [job, status.reload]);
  useEffect(() => {
    if (!job) return;
    const milestone =
      job.status === "completed"
        ? "completed"
        : job.business_deleted === true
          ? "business-deleted"
          : "";
    if (milestone && notified.current !== milestone) {
      notified.current = milestone;
      changed.current();
    }
  }, [job]);
  function refreshPreview() {
    setTyped("");
    setActionError("");
    setPreviewInvalidated(false);
    setNow(Date.now());
    preview.reload();
  }
  async function confirm() {
    if (
      mutationPending.current ||
      preview.loading ||
      preview.error ||
      previewInvalidated ||
      !canConfirmPurge(preview.data, target, typed, Date.now())
    )
      return;
    mutationPending.current = true;
    setSubmitting(true);
    setActionError("");
    try {
      const accepted = await post<Row>(
        `/${target.kind === "strategy" ? "strategies" : "backtests"}/${encodeURIComponent(target.id)}/purge`,
        {
          confirmation_token: preview.data!.confirmation_token,
          confirmation_text: typed,
        },
      );
      if (!accepted?.purge_id)
        throw new Error(
          "尚未取得清理任务编号，请关闭后刷新回收站核验任务状态。",
        );
      setPurgeId(accepted.purge_id);
      setTyped("");
      changed.current();
    } catch (problem: any) {
      setActionError(problem.message);
      // A failed/unknown request must never silently reuse or resubmit a destructive scope.
      setPreviewInvalidated(true);
      setTyped("");
      changed.current();
    } finally {
      mutationPending.current = false;
      setSubmitting(false);
    }
  }
  async function retry() {
    if (
      mutationPending.current ||
      status.loading ||
      status.error ||
      job?.status !== "failed" ||
      job.can_retry !== true
    )
      return;
    mutationPending.current = true;
    setSubmitting(true);
    setActionError("");
    try {
      const accepted = await post<Row>(
        `/workspace-purges/${encodeURIComponent(purgeId)}/retry`,
      );
      if (accepted?.purge_id === purgeId) status.setData(accepted);
      status.reload();
      changed.current();
    } catch (problem: any) {
      setActionError(problem.message);
      status.reload();
    } finally {
      mutationPending.current = false;
      setSubmitting(false);
    }
  }
  const expired =
    !!preview.data &&
    typeof preview.data.expires_at_ms === "number" &&
    now >= preview.data.expires_at_ms;
  const canType =
    !!preview.data &&
    preview.data.can_purge === true &&
    !hasWatchlistProtection(preview.data);
  return (
    <Modal
      title={purgeId ? "永久删除 · 清理状态" : "永久删除确认"}
      onClose={() => !submitting && onClose()}
      escapeUnderStock
    >
      <div className="modal-body purge-dialog-body">
        {purgeId ? (
          <>
            <div className="purge-target-name">{job?.name || target.name}</div>
            {status.error && (
              <ErrorBox message={status.error} retry={status.reload} />
            )}
            {job ? (
              <PermanentDeleteStatus job={job} />
            ) : (
              !status.error && <Loading />
            )}
          </>
        ) : (
          <>
            {preview.error && (
              <ErrorBox message={preview.error} retry={refreshPreview} />
            )}
            {preview.loading && !preview.data ? (
              <Loading />
            ) : (
              preview.data && (
                <PermanentDeletePreview
                  preview={preview.data}
                  onStock={onStock}
                />
              )
            )}
            {canType && (
              <label className="purge-confirm-label">
                输入“{preview.data!.confirmation_text}”确认删除
                <input
                  value={typed}
                  onChange={(event) => setTyped(event.target.value)}
                  disabled={
                    submitting ||
                    preview.loading ||
                    previewInvalidated ||
                    expired
                  }
                  autoComplete="off"
                  spellCheck={false}
                  aria-label="永久删除确认名称"
                  placeholder={preview.data!.confirmation_text}
                />
              </label>
            )}
            {(expired || previewInvalidated) && (
              <p className="purge-refresh-note">
                {expired
                  ? "本次删除范围已过期。"
                  : "请重新核验删除范围与清理状态。"}
                <button
                  type="button"
                  className="btn small"
                  disabled={submitting || preview.loading}
                  onClick={refreshPreview}
                >
                  重新核验
                </button>
              </p>
            )}
          </>
        )}
        {actionError && <ErrorBox message={actionError} />}
      </div>
      <div className="modal-footer">
        <button
          type="button"
          className="btn"
          disabled={submitting}
          onClick={onClose}
        >
          {purgeId
            ? purgeIsWorking(job?.status)
              ? "关闭，后台继续"
              : "关闭"
            : "取消"}
        </button>
        {!purgeId && canType && (
          <button
            type="button"
            className="btn danger"
            disabled={
              submitting ||
              preview.loading ||
              !!preview.error ||
              previewInvalidated ||
              !canConfirmPurge(preview.data, target, typed, now)
            }
            onClick={confirm}
          >
            {submitting ? <Spinner /> : <Trash2 size={14} />}永久删除
          </button>
        )}
        {job?.status === "failed" && job.can_retry === true && (
          <button
            type="button"
            className="btn"
            disabled={submitting || status.loading || !!status.error}
            onClick={retry}
          >
            {submitting ? <Spinner /> : <RotateCcw size={14} />}重试清理
          </button>
        )}
        {job?.status === "failed" &&
          job.business_deleted === false &&
          job.can_reconfirm === true && (
            <button
              type="button"
              className="btn"
              disabled={submitting || status.loading || !!status.error}
              onClick={() => {
                setTyped("");
                setActionError("");
                setPreviewInvalidated(false);
                setNow(Date.now());
                setPurgeId("");
                notified.current = "";
              }}
            >
              重新核验范围
            </button>
          )}
      </div>
    </Modal>
  );
}

/** Persisted jobs keep retries accessible after the original business row is gone. */
export function PendingPurgeJobs({
  kind,
  refresh,
  onOpen,
  onChanged,
}: {
  kind: PurgeTarget["kind"];
  refresh: number;
  onOpen: (target: PurgeTarget) => void;
  onChanged?: () => void;
}) {
  const [offset, setOffset] = useState(0);
  const result = useApi<Row>(
    `/workspace-purges?status=unfinished&target_kind=${kind}&limit=20&offset=${offset}&_refresh=${refresh}`,
  );
  const rows = list(result.data).filter(
    (job) => job.target_kind === kind && job.status !== "completed",
  );
  const previousJobs = useRef<string | null>(null);
  const changed = useRef(onChanged);
  changed.current = onChanged;
  useEffect(() => {
    if (!result.data) return;
    const signature = JSON.stringify(
      rows.map((job) => [job.purge_id, job.status, job.business_deleted]),
    );
    const previous = previousJobs.current;
    previousJobs.current = signature;
    // The row may disappear after business deletion while its file cleanup continues.
    // Refresh the authoritative trash page when background state changes as well.
    if (
      (previous !== null && previous !== signature) ||
      (previous === null && rows.length > 0)
    )
      changed.current?.();
  }, [result.data]);
  useEffect(
    () =>
      setOffset((current) =>
        recyclePageOffset(result.data?.total, current, 20),
      ),
    [result.data?.total],
  );
  useEffect(() => {
    if (!rows.some((job) => purgeIsWorking(job.status))) return;
    const timer = setTimeout(result.reload, 2000);
    return () => clearTimeout(timer);
  }, [result.data, result.reload]);
  if (result.error)
    return (
      <ErrorBox
        message={`清理任务读取失败：${result.error}`}
        retry={result.reload}
      />
    );
  if (!rows.length && !offset) return null;
  return (
    <section className="purge-pending-jobs" aria-label="未完成的永久删除任务">
      <strong>未完成的清理</strong>
      {rows.map((job) => (
        <button
          type="button"
          className="purge-pending-job"
          key={job.purge_id}
          onClick={() =>
            onOpen({
              kind,
              id: job.target_id,
              name: job.name,
              purgeId: job.purge_id,
            })
          }
        >
          <span>{job.name}</span>
          <small>
            {purgePhase(job.status)} · 查看
            {job.status === "failed" && job.can_retry === true
              ? " / 重试"
              : job.can_reconfirm === true
                ? " / 重新核验"
                : ""}
          </small>
        </button>
      ))}
      {(offset > 0 ||
        result.data?.has_more ||
        result.data?.total > offset + 20) && (
        <div className="strategy-recycle-pagination">
          <button
            type="button"
            className="btn small"
            disabled={!offset || result.loading}
            onClick={() => setOffset(Math.max(0, offset - 20))}
          >
            上一页
          </button>
          <span>第 {Math.floor(offset / 20) + 1} 页</span>
          <button
            type="button"
            className="btn small"
            disabled={
              result.loading ||
              !(result.data?.has_more || result.data?.total > offset + 20)
            }
            onClick={() => setOffset(offset + 20)}
          >
            下一页
          </button>
        </div>
      )}
    </section>
  );
}

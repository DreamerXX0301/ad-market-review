import type { Row } from "../lib/api";
import { ErrorBox, Modal, Spinner } from "./UI";
import "./RecycleConfirm.css";

export default function RecycleConfirm({
  kind,
  item,
  busy,
  error,
  onConfirm,
  onClose,
}: {
  kind: "strategy" | "backtest";
  item: Row;
  busy: boolean;
  error?: string;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const strategy = kind === "strategy";
  const count = item.cascade_backtest_count;
  return (
    <Modal
      title={strategy ? "删除策略与关联回测" : "删除回测记录"}
      onClose={() => !busy && onClose()}
    >
      <div className="modal-body recycle-confirm-body">
        <p>
          将<strong>{item.name || item.strategy_name || "本条回测"}</strong>
          {strategy ? "移到策略回收站" : "这次回测移到回收站"}？
        </p>
        {strategy ? (
          <>
            <p>
              {Number.isInteger(count) && count >= 0
                ? `本次会将 ${count} 条尚未删除的关联回测一并移到回收站。`
                : "尚未删除的关联回测会一并移到回收站。"}
              恢复策略时，仅恢复本次连带移入的回测，之前独立删除的回测保持原状。
            </p>
            <p>
              已有跟踪记录和基准价保留，可在跟踪页独立管理。原始行情、已保存选股结果与任务记录保留。
            </p>
          </>
        ) : (
          <>
            <p>
              {item.start_date} → {item.end_date}
            </p>
            <p>
              删除后不再显示在正常历史中，可从回收站恢复。所属策略、已有跟踪和原始行情保留。
            </p>
          </>
        )}
        {error && <ErrorBox message={error} />}
      </div>
      <div className="modal-footer">
        <button className="btn" disabled={busy} onClick={onClose}>
          取消
        </button>
        <button className="btn danger" disabled={busy} onClick={onConfirm}>
          {busy && <Spinner />}移到回收站
        </button>
      </div>
    </Modal>
  );
}

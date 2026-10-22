import { useEffect, useRef, useState } from "react";
import { BookmarkCheck, History, Save } from "lucide-react";
import { api, useApi } from "../../lib/api";
import type { Row } from "../../lib/api";
import { ErrorBox, Modal } from "../../components/UI";
import { sentimentKnownTime } from "./sentimentPresentation";

function captureTime(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) && value > 0
    ? new Date(value).toLocaleString("zh-CN", {
        timeZone: "Asia/Shanghai",
        hour12: false,
      }) + " 北京时间"
    : "时间未登记";
}

/** Saving is explicit. Merely browsing or changing dates never creates a record. */
export default function SentimentSnapshots({
  date,
  fingerprint,
}: {
  date?: string;
  fingerprint?: string;
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [saved, setSaved] = useState<Row | null>(null),
    [selected, setSelected] = useState<string | null>(null),
    [offset, setOffset] = useState(0);
  const alive = useRef(true),
    inFlight = useRef(false);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const list = useApi<Row>(
    date
      ? `/market/sentiment-snapshots?date=${encodeURIComponent(date)}&limit=10&offset=${offset}`
      : null,
  );
  const detail = useApi<Row>(
    selected
      ? `/market/sentiment-snapshots/${encodeURIComponent(selected)}`
      : null,
  );
  async function capture() {
    if (!date || !fingerprint || inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    try {
      const result = await api<Row>("/market/sentiment-snapshots", {
        method: "POST",
        body: JSON.stringify({ date, expected_input_fingerprint: fingerprint }),
      });
      if (!alive.current) return;
      setSaved(result);
      setOffset(0);
      list.reload();
    } catch (error) {
      if (alive.current) setError((error as Error).message);
    } finally {
      inFlight.current = false;
      if (alive.current) setBusy(false);
    }
  }
  const rows: Row[] = Array.isArray(list.data?.items) ? list.data.items : [];
  const archived =
    detail.data?.date === date && detail.data?.snapshot_id === selected
      ? detail.data
      : null;
  const payload = archived?.payload;
  return (
    <div className="sentiment-snapshots">
      <div className="sentiment-snapshot-action">
        <div>
          <strong>留存本次复盘</strong>
          <p>
            按当前输入版本保存。系统记录真实留存时间，不回填历史来源首知时间。
          </p>
        </div>
        <button
          className="btn"
          disabled={busy || !date || !fingerprint}
          onClick={() => void capture()}
        >
          <Save size={13} />
          {busy ? "正在留存…" : "留存本次复盘"}
        </button>
      </div>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {saved && (
        <p className="sentiment-snapshot-success">
          <BookmarkCheck size={13} />
          {saved.reused ? "相同输入已留存，沿用原记录" : "已留存"} ·{" "}
          {captureTime(saved.recorded_at_ms)}
        </p>
      )}
      <details className="sentiment-snapshot-history">
        <summary>
          <History size={12} />
          本日已留存 {list.data?.total ?? "—"} 份复盘
        </summary>
        {list.error && <ErrorBox message={list.error} retry={list.reload} />}
        {list.loading && <p>正在读取留存记录…</p>}
        {!list.loading && !list.error && !rows.length && (
          <p>当前观察日尚无手动留存记录。</p>
        )}
        <ul>
          {rows.map((record) => (
            <li key={record.snapshot_id}>
              <div>
                <strong>{captureTime(record.recorded_at_ms)}</strong>
                <small>
                  规则 {record.rule_version} ·{" "}
                  <code>{record.input_fingerprint}</code>
                </small>
              </div>
              <button
                className="btn small"
                onClick={() => setSelected(record.snapshot_id)}
              >
                查看留存
              </button>
            </li>
          ))}
        </ul>
        {(offset > 0 || offset + rows.length < (list.data?.total ?? 0)) && (
          <div className="button-group">
            <button
              className="btn small"
              disabled={offset === 0 || list.loading}
              onClick={() => setOffset(Math.max(0, offset - 10))}
            >
              上一页
            </button>
            <button
              className="btn small"
              disabled={
                offset + rows.length >= (list.data?.total ?? 0) || list.loading
              }
              onClick={() => setOffset(offset + 10)}
            >
              下一页
            </button>
          </div>
        )}
      </details>
      {selected && (
        <Modal title={`已留存复盘 · ${date}`} onClose={() => setSelected(null)}>
          <div className="modal-body sentiment-snapshot-detail">
            {detail.error && (
              <ErrorBox message={detail.error} retry={detail.reload} />
            )}
            {!archived && !detail.error && <p>正在读取原始留存…</p>}
            {archived && (
              <>
                <p>
                  实际留存于 {captureTime(archived.recorded_at_ms)}。这不是
                  {archived.date}当天已发出信号的证明。
                </p>
                <dl>
                  <div>
                    <dt>规则版本</dt>
                    <dd>{archived.rule_version}</dd>
                  </div>
                  <div>
                    <dt>输入指纹</dt>
                    <dd>
                      <code>{archived.input_fingerprint}</code>
                    </dd>
                  </div>
                  <div>
                    <dt>内容SHA</dt>
                    <dd>
                      <code>{archived.content_sha256}</code>
                    </dd>
                  </div>
                  <div>
                    <dt>历史源首知</dt>
                    <dd>
                      {sentimentKnownTime(payload?.provenance?.first_known_at)}
                    </dd>
                  </div>
                </dl>
                <div className="sentiment-snapshot-axes">
                  {[
                    ["指数环境", payload?.index_environment],
                    ["个股广度", payload?.market],
                    ["短线接力 · 全样本", payload?.short_term],
                  ].map(([label, axis]) => (
                    <section key={String(label)}>
                      <h3>{String(label)}</h3>
                      <p>
                        {typeof axis === "object" && axis
                          ? axis.state_label
                          : "待核验"}
                      </p>
                    </section>
                  ))}
                </div>
                <details>
                  <summary>查看完整原始复盘与来源证据</summary>
                  <pre>{JSON.stringify(payload, null, 2)}</pre>
                </details>
              </>
            )}
          </div>
        </Modal>
      )}
    </div>
  );
}

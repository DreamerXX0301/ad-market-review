import { useRef, useState } from "react";
import { FilePenLine, RefreshCw } from "lucide-react";
import { post, requestKey, useApi } from "../../lib/api";
import type { Row } from "../../lib/api";
import { dateTime, integer } from "../../lib/format";
import { Badge, ErrorBox, Loading, Modal, Spinner } from "../../components/UI";
import CopyStockCode from "../../components/CopyStockCode";
import {
  listingConfirmationDraft,
  listingIdentityView,
  listingEvidenceLines,
} from "./dataPresentation";

const identityLabels: Record<string, string> = {
  pending: "待上市",
  listed: "已上市",
  quoted_unknown_date: "已有报价 · 日期待核验",
  unknown: "身份待核验",
};
const evidenceLabels: Record<string, string> = {
  source_list_date: "来源上市日期",
  manual_user_confirmation: "人工核验确认",
  quote_presence: "本代实际报价",
  unverified: "尚未核验",
};

export function ListingIdentitySummary({ identities }: { identities?: Row }) {
  const summary = listingIdentityView(identities);
  return (
    <div className="coverage-identity-summary">
      <div>
        <small>名录总量</small>
        <strong>
          {integer(summary.catalog)}
          <span>只股票</span>
        </strong>
      </div>
      <div>
        <small>已上市日期核验</small>
        <strong>
          {integer(summary.listed)}
          <span> / {integer(summary.expected)}</span>
        </strong>
        {summary.complete && <Badge tone="good">100%</Badge>}
      </div>
      <div>
        <small>有依据的待上市</small>
        <strong>
          {integer(summary.pending)}
          <span>只 · 暂不计入分母</span>
        </strong>
      </div>
      <div>
        <small>仍需核验</small>
        <strong>
          {integer(summary.unknown)}
          <span>只 · 保留在分母</span>
        </strong>
      </div>
    </div>
  );
}

export function ListingIdentityRows({
  rows,
  onEdit,
}: {
  rows: Row[];
  onEdit: (row: Row) => void;
}) {
  if (!rows.length) return <p className="muted">本分类暂无记录。</p>;
  return (
    <div className="coverage-gap-list coverage-identity-table">
      <table className="coverage-table">
        <thead>
          <tr>
            <th>股票</th>
            <th>身份与日期</th>
            <th>核验依据</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.instrument_id}>
              <td>
                <strong>{row.name || row.instrument_id}</strong>
                <CopyStockCode
                  instrumentId={row.instrument_id}
                  variant="full"
                />
              </td>
              <td>
                <Badge
                  tone={row.listing_status === "pending" ? "neutral" : "warn"}
                >
                  {identityLabels[row.listing_status] || "身份待核验"}
                </Badge>
                <small>
                  {row.source_list_date
                    ? `来源上市日 ${row.source_list_date}`
                    : "来源上市日期未提供"}
                </small>
              </td>
              <td>
                <strong>
                  {evidenceLabels[row.evidence_kind] || "依据待核验"}
                </strong>
                <span>{row.reason || "尚无可确认的身份依据"}</span>
                <small>
                  {row.source || "来源待核验"}
                  {row.effective_business_date || row.confirmed_date
                    ? ` · 对应业务日 ${row.effective_business_date || row.confirmed_date}`
                    : ""}
                  {row.confirmed_at_ms
                    ? ` · 记录于 ${dateTime(row.confirmed_at_ms)}`
                    : ""}
                </small>
                {listingEvidenceLines(row).length > 0 && (
                  <details>
                    <summary>查看补充依据</summary>
                    {listingEvidenceLines(row).map((text, index) => (
                      <p key={index}>{text}</p>
                    ))}
                  </details>
                )}
              </td>
              <td>
                {row.can_edit === true &&
                row.evidence_kind !== "source_list_date" ? (
                  <button className="link" onClick={() => onEdit(row)}>
                    {row.listing_status === "pending"
                      ? "更正状态"
                      : row.listing_status === "quoted_unknown_date"
                        ? "补充人工说明"
                        : "标记待上市"}
                  </button>
                ) : (
                  <small>依来源证据更新</small>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function ListingIdentityPanel({
  onSaved,
  disabled = false,
}: {
  onSaved: () => void;
  disabled?: boolean;
}) {
  const result = useApi<Row>("/data/listing-status");
  const [editing, setEditing] = useState<Row | null>(null);
  const [codes, setCodes] = useState(""),
    [status, setStatus] = useState("pending"),
    [reason, setReason] = useState(""),
    [evidence, setEvidence] = useState("");
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [saved, setSaved] = useState(false);
  const request = useRef<{ signature: string; key: string } | null>(null),
    inFlight = useRef(false);
  const identities = result.data?.identities;
  const open = (row: Row = {}) => {
    setEditing(row);
    setCodes(row.instrument_id || "");
    setStatus(
      row.listing_status === "pending" ||
        row.listing_status === "quoted_unknown_date"
        ? "unknown"
        : "pending",
    );
    setReason("");
    setEvidence("");
    setError("");
    request.current = null;
  };
  const submit = async () => {
    if (inFlight.current || disabled) return;
    const draft = listingConfirmationDraft(
      codes,
      status,
      reason,
      evidence,
      result.data?.business_date,
    );
    if (draft.error || !draft.payload)
      return setError(draft.error || "核验参数不完整");
    const signature = JSON.stringify(draft.payload);
    const key =
      request.current?.signature === signature
        ? request.current.key
        : requestKey();
    request.current = { signature, key };
    inFlight.current = true;
    setBusy(true);
    setError("");
    try {
      const response = await post<Row>("/data/listing-status/confirm", {
        ...draft.payload,
        request_key: key,
      });
      result.setData(response);
      result.reload();
      setEditing(null);
      setSaved(true);
      request.current = null;
      onSaved();
    } catch (problem: any) {
      setError(problem.message);
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  };
  return (
    <div className="coverage-listing-panel">
      <div className="coverage-section-heading">
        <div>
          <h3>上市身份与核验依据</h3>
          <p>
            业务日 {result.data?.business_date || "读取中…"} ·
            指数不计入股票名录分母
          </p>
        </div>
        <button
          className="btn small"
          disabled={disabled || busy || result.loading}
          onClick={() => open()}
        >
          <FilePenLine size={14} />
          人工核验标记
        </button>
        <button
          className="icon-btn"
          aria-label="刷新上市身份"
          onClick={result.reload}
        >
          <RefreshCw size={14} />
        </button>
      </div>
      {result.error && (
        <ErrorBox message={result.error} retry={result.reload} />
      )}
      {result.loading && !result.data ? (
        <Loading />
      ) : identities ? (
        <>
          <ListingIdentitySummary identities={identities} />
          {result.data?.summary_is_current === false && (
            <p className="coverage-plan-caveat">
              行情或人工确认已有更新；以下为保守身份状态，请刷新或重新核验，旧代无报价记录不用于排除分母。
            </p>
          )}
          {saved && (
            <p className="coverage-save-result" role="status">
              上市身份已保存。原本地覆盖报告已过期，请重新“核验本地数据”更新逐股范围。
            </p>
          )}
          <p className="muted">
            待上市需有来源未来上市日或人工确认及当前无报价证据，来源日期缺失本身不代表待上市。上市后下一次盘后增量会重新核验并纳入当期范围。
          </p>
          <h3>待上市 · {integer(identities.pending_listing_count)} 只</h3>
          <ListingIdentityRows
            rows={
              Array.isArray(identities.pending_listing)
                ? identities.pending_listing
                : []
            }
            onEdit={open}
          />
          <h3>仍需核验 · {integer(identities.unknown_count)} 只</h3>
          <ListingIdentityRows
            rows={Array.isArray(identities.unknown) ? identities.unknown : []}
            onEdit={open}
          />
          {Array.isArray(result.data?.confirmations) &&
            result.data.confirmations.length > 0 && (
              <details className="coverage-confirmation-history">
                <summary>
                  最近人工核验记录 · {result.data.confirmations.length} 条
                </summary>
                <ul>
                  {result.data.confirmations.map((item: Row, index: number) => (
                    <li key={item.confirmation_id || index}>
                      <strong>
                        {item.instrument_id ||
                          item.instrument_ids?.join("、") ||
                          "人工确认"}
                      </strong>
                      <span>
                        {identityLabels[item.status] || item.status} ·{" "}
                        {item.reason}
                      </span>
                      <small>
                        {dateTime(
                          item.confirmed_at_ms ||
                            item.created_at_ms ||
                            item.confirmed_date,
                        )}
                      </small>
                    </li>
                  ))}
                </ul>
              </details>
            )}
        </>
      ) : null}
      {editing && (
        <Modal
          title={
            editing.instrument_id
              ? `${editing.name || editing.instrument_id} · 人工身份核验`
              : "人工核验上市身份"
          }
          onClose={() => {
            if (!busy) setEditing(null);
          }}
        >
          <div className="coverage-listing-form">
            <label>
              完整股票代码（最多128只）
              <textarea
                aria-label="人工核验股票代码"
                value={codes}
                maxLength={1800}
                disabled={busy}
                onChange={(event) => setCodes(event.target.value)}
                placeholder="例如000001.SZ；多只可用空格、逗号或换行分隔"
              />
            </label>
            <label>
              确认状态
              <select
                aria-label="人工上市身份状态"
                value={status}
                disabled={busy}
                onChange={(event) => setStatus(event.target.value)}
              >
                <option
                  value="pending"
                  disabled={editing.listing_status === "quoted_unknown_date"}
                >
                  待上市（有明确依据且尚无报价）
                </option>
                <option value="unknown">恢复待核验／更正原确认</option>
              </select>
            </label>
            <label>
              核验说明（必填）
              <textarea
                aria-label="上市身份核验说明"
                value={reason}
                maxLength={1000}
                disabled={busy}
                onChange={(event) => setReason(event.target.value)}
                placeholder="说明已核对的上市安排，或需要更正原确认的原因"
              />
            </label>
            <label>
              补充依据（选填）
              <textarea
                aria-label="上市身份补充依据"
                value={evidence}
                maxLength={4000}
                disabled={busy}
                onChange={(event) => setEvidence(event.target.value)}
                placeholder="可填写公开来源、公告链接或人工核验备注"
              />
            </label>
            <p className="muted">
              本次观察业务日 {result.data?.business_date || "待读取"}
              ；确认时间由服务器记录。此操作保存身份依据，不填写或推测上市日期，不补造行情。已有实际报价的股票不能标记待上市。
            </p>
            {error && <ErrorBox message={error} />}
            <div className="coverage-form-actions">
              <button
                className="btn"
                disabled={busy}
                onClick={() => setEditing(null)}
              >
                取消
              </button>
              <button
                className="btn primary"
                disabled={busy || disabled || !result.data?.business_date}
                onClick={() => void submit()}
              >
                {busy ? <Spinner /> : <FilePenLine size={14} />}保存人工核验
              </button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}

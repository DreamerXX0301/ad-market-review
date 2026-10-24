import { useState } from "react";
import type { FormEvent } from "react";
import { FileText, Plus, RefreshCw, Save, X } from "lucide-react";
import { api, post, useApi } from "../../lib/api";
import type { Row } from "../../lib/api";
import { dateTime, percent, tone } from "../../lib/format";
import { Empty, ErrorBox, Loading, Modal } from "../../components/UI";
import CopyStockCode from "../../components/CopyStockCode";
import BoardTags from "./BoardTags";
import { ladderCardAttribution } from "./ladderBoardPresentation";
import {
  buildDailyMainlineGroups,
  editableAttribution,
  emptyMainlineAttribution,
  mainlineEvidenceUrl,
  mainlineRoleLabels,
  mainlineStateLabels,
  mainlineStatusLabels,
  mainlineWritePayload,
} from "./dailyMainlinePresentation";
import type {
  MainlineAttribution,
  MainlineRecord,
} from "./dailyMainlinePresentation";
import "./DailyMainlines.css";

type MainlineList = {
  date: string;
  items: MainlineRecord[];
  total: number;
  notice: string;
};
type MainlineHistory = MainlineList & { limit: number; offset: number };

export function MainlineEvidence({ item }: { item: MainlineAttribution }) {
  const link = mainlineEvidenceUrl(item.evidence_ref);
  return (
    <div className="daily-mainline-evidence">
      <div className="daily-mainline-tags">
        <strong>{item.mainline_name}</strong>
        <span>{mainlineStatusLabels[item.evidence_status]}</span>
        {item.is_display_primary && <span>选定展示主组</span>}
        <span>{mainlineRoleLabels[item.role]}</span>
      </div>
      <dl>
        <div>
          <dt>录入来源</dt>
          <dd>{item.source_name} · 用户手工录入</dd>
        </div>
        <div>
          <dt>来源原因原文</dt>
          <dd>{item.source_reason || "未提供"}</dd>
        </div>
        <div>
          <dt>催化线索</dt>
          <dd>{item.catalyst_note || "未提供；不从原因关键词推断"}</dd>
        </div>
        <div>
          <dt>证据引用</dt>
          <dd>
            {link ? (
              <a href={link} target="_blank" rel="noopener noreferrer">
                {item.evidence_ref}
              </a>
            ) : (
              item.evidence_ref
            )}
          </dd>
        </div>
        <div>
          <dt>来源发布时间</dt>
          <dd>{item.source_published_at || "未知"}</dd>
        </div>
        <div>
          <dt>本陈述版本首见</dt>
          <dd>
            {item.first_known_at_ms
              ? dateTime(item.first_known_at_ms)
              : "未记录"}
          </dd>
        </div>
        <div>
          <dt>证据 SHA256</dt>
          <dd>{item.evidence_sha256 || "未登记；未验证引用内容"}</dd>
        </div>
        {item.user_note && (
          <div>
            <dt>用户说明</dt>
            <dd>{item.user_note}</dd>
          </div>
        )}
      </dl>
    </div>
  );
}

export function MainlineEditor({
  date,
  stock,
  record,
  onSaved,
  onClose,
}: {
  date: string;
  stock: Row;
  record?: MainlineRecord;
  onSaved: () => void;
  onClose: () => void;
}) {
  const symbol = stock.instrument_id;
  const [values, setValues] = useState<MainlineAttribution[]>(
    record?.attributions.length
      ? record.attributions.map(editableAttribution)
      : [emptyMainlineAttribution()],
  );
  const [expected, setExpected] = useState(record?.revision || 0);
  const [action, setAction] = useState<"replace" | "revoke">("replace");
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [conflict, setConflict] = useState<MainlineRecord | null>(null);
  const [historyPage, setHistoryPage] = useState(0);
  const history = useApi<MainlineHistory>(
    `/market/mainlines/history?date=${encodeURIComponent(date)}&instrument_id=${encodeURIComponent(symbol)}&limit=20&offset=${historyPage * 20}`,
  );
  const update = (index: number, change: Partial<MainlineAttribution>) =>
    setValues((before) =>
      before.map((value, i) => ({
        ...value,
        ...(change.is_display_primary
          ? { is_display_primary: i === index }
          : {}),
        ...(i === index ? change : {}),
      })),
    );
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (saving || conflict) return;
    setSaving(true);
    setError("");
    try {
      await post<MainlineRecord>(
        "/market/mainlines/revisions",
        mainlineWritePayload(date, symbol, expected, action, reason, values),
      );
      onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存未完成");
      // A stale editor and a lost success response both require an explicit
      // comparison. Keep the draft; never silently overwrite the latest state.
      try {
        const latest = await api<MainlineList>(
          `/market/mainlines?date=${encodeURIComponent(date)}`,
        );
        const current = latest.items.find(
          (item) => item.instrument_id === symbol,
        );
        if (current && current.revision !== expected) {
          setConflict(current);
          history.reload();
        }
      } catch {
        /* Keep the original error and draft if the refresh also fails. */
      }
    } finally {
      setSaving(false);
    }
  }
  return (
    <Modal
      wide
      title={`当日主线 · ${stock.name || symbol} · ${date}`}
      onClose={() => {
        if (!saving) onClose();
      }}
    >
      <div className="daily-mainline-editor">
        <p className="daily-mainline-notice">
          手工整理该日的来源陈述与用户判断。证据引用只保存定位，不抓取网页或上传文件；来源转述不代表已验证的公司事实。系统实际记录时间不可回填。
        </p>
        {error && <ErrorBox message={error} />}
        {conflict && (
          <div className="daily-mainline-conflict" role="alert">
            <strong>草稿已保留；当前记录是修订 {conflict.revision}</strong>
            <p>
              {conflict.change_reason} · {dateTime(conflict.recorded_at_ms)}
            </p>
            {conflict.attributions.map((item) => (
              <MainlineEvidence key={item.assertion_id} item={item} />
            ))}
            <button
              className="btn small"
              onClick={() => {
                setExpected(conflict.revision);
                setConflict(null);
                setError(
                  "已按当前修订更新保存基准，请核对保留的草稿与理由后再次保存。",
                );
              }}
            >
              已核对当前修订，保留草稿继续编辑
            </button>
          </div>
        )}
        <form onSubmit={submit}>
          <fieldset disabled={saving}>
            <div className="daily-mainline-form-header">
              <label>
                本次操作
                <select
                  value={action}
                  onChange={(event) =>
                    setAction(event.target.value as "replace" | "revoke")
                  }
                >
                  <option value="replace">
                    {expected ? "更正全部归属" : "新增有据归属"}
                  </option>
                  {expected > 0 && (
                    <option value="revoke">撤销该日全部归属（保留历史）</option>
                  )}
                </select>
              </label>
              <span>
                保存基准：修订 {expected} · {symbol}
              </span>
            </div>
            {action === "replace" &&
              values.map((value, index) => (
                <section className="daily-mainline-form-item" key={index}>
                  <header>
                    <strong>归属 {index + 1}</strong>
                    {values.length > 1 && (
                      <button
                        type="button"
                        className="btn small"
                        onClick={() =>
                          setValues((before) =>
                            before.filter((_, i) => i !== index),
                          )
                        }
                      >
                        <X size={12} />
                        移除此条
                      </button>
                    )}
                  </header>
                  <div className="daily-mainline-fields">
                    <label>
                      当日主线名称
                      <input
                        required
                        maxLength={80}
                        value={value.mainline_name}
                        onChange={(e) =>
                          update(index, { mainline_name: e.target.value })
                        }
                      />
                    </label>
                    <label>
                      证据状态
                      <select
                        value={value.evidence_status}
                        onChange={(e) =>
                          update(index, {
                            evidence_status: e.target
                              .value as MainlineAttribution["evidence_status"],
                            ...(["unverified", "conflict"].includes(
                              e.target.value,
                            )
                              ? { is_display_primary: false }
                              : {}),
                          })
                        }
                      >
                        {Object.entries(mainlineStatusLabels).map(
                          ([key, name]) => (
                            <option key={key} value={key}>
                              {name}
                            </option>
                          ),
                        )}
                      </select>
                    </label>
                    <label>
                      来源名称
                      <input
                        required
                        maxLength={200}
                        value={value.source_name}
                        onChange={(e) =>
                          update(index, { source_name: e.target.value })
                        }
                        placeholder="例如：手工归档的复盘材料"
                      />
                    </label>
                    <label>
                      角色标记
                      <select
                        value={value.role}
                        onChange={(e) =>
                          update(index, {
                            role: e.target.value as MainlineAttribution["role"],
                          })
                        }
                      >
                        {Object.entries(mainlineRoleLabels).map(
                          ([key, name]) => (
                            <option key={key} value={key}>
                              {name}
                            </option>
                          ),
                        )}
                      </select>
                    </label>
                    <label className="full">
                      证据引用
                      <input
                        required
                        maxLength={2000}
                        value={value.evidence_ref}
                        onChange={(e) =>
                          update(index, { evidence_ref: e.target.value })
                        }
                        placeholder="网页URL、附件ID或本地归档引用及图内位置"
                      />
                    </label>
                    <label className="full">
                      来源原因原文
                      <textarea
                        maxLength={2000}
                        rows={2}
                        value={value.source_reason}
                        onChange={(e) =>
                          update(index, { source_reason: e.target.value })
                        }
                      />
                    </label>
                    <label className="full">
                      催化线索 / 核验说明
                      <textarea
                        maxLength={2000}
                        rows={2}
                        value={value.catalyst_note}
                        onChange={(e) =>
                          update(index, { catalyst_note: e.target.value })
                        }
                        placeholder="保留线索与核验范围，不将转述当作已核实因果"
                      />
                    </label>
                    <label>
                      来源发布时间（可空）
                      <input
                        maxLength={50}
                        value={value.source_published_at || ""}
                        onChange={(e) =>
                          update(index, {
                            source_published_at: e.target.value || null,
                          })
                        }
                        placeholder="2026-01-05T18:30:00+08:00"
                      />
                    </label>
                    <label>
                      证据 SHA256（可空）
                      <input
                        maxLength={64}
                        pattern="[0-9a-fA-F]{64}"
                        value={value.evidence_sha256 || ""}
                        onChange={(e) =>
                          update(index, {
                            evidence_sha256: e.target.value || null,
                          })
                        }
                      />
                    </label>
                    <label className="full">
                      用户说明
                      <textarea
                        maxLength={2000}
                        rows={2}
                        value={value.user_note}
                        onChange={(e) =>
                          update(index, { user_note: e.target.value })
                        }
                      />
                    </label>
                  </div>
                  <label className="daily-mainline-primary">
                    <input
                      type="checkbox"
                      checked={value.is_display_primary}
                      disabled={["unverified", "conflict"].includes(
                        value.evidence_status,
                      )}
                      onChange={(e) =>
                        update(index, { is_display_primary: e.target.checked })
                      }
                    />
                    选作唯一展示主组，其余归属仍保留
                  </label>
                </section>
              ))}
            {action === "replace" && values.length < 12 && (
              <button
                type="button"
                className="btn small"
                onClick={() =>
                  setValues((before) => [...before, emptyMainlineAttribution()])
                }
              >
                <Plus size={13} />
                增加一条有据归属
              </button>
            )}
            {action === "revoke" && (
              <p className="daily-mainline-notice">
                保存后恢复未归因展示；旧归属、来源与更正记录仍可在下方审计中查阅。
              </p>
            )}
            <label className="daily-mainline-change-reason">
              本次录入 / 更正 / 撤销理由
              <textarea
                required
                maxLength={1000}
                rows={2}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
              />
            </label>
            <p className="daily-mainline-muted">
              只有一个可用主线时按该组展示；多组未选主次显示“主组待定”，存在显式冲突时显示“来源冲突”。未核验陈述保留在证据中。
            </p>
            <div className="daily-mainline-actions">
              <button type="button" className="btn" onClick={onClose}>
                取消
              </button>
              <button
                type="submit"
                className="btn primary"
                disabled={!!conflict}
              >
                <Save size={14} />
                {saving
                  ? "保存中…"
                  : action === "revoke"
                    ? "追加撤销记录"
                    : "保存归属修订"}
              </button>
            </div>
          </fieldset>
        </form>
        <section className="daily-mainline-history">
          <h3>来源与修订审计</h3>
          {history.loading && <Loading />}
          {history.error && (
            <ErrorBox message={history.error} retry={history.reload} />
          )}
          {history.data?.items.map((revision) => (
            <details key={revision.revision_id}>
              <summary>
                修订 {revision.revision} ·{" "}
                {revision.action === "revoke"
                  ? "撤销"
                  : mainlineStateLabels[revision.display_state]}{" "}
                · {dateTime(revision.recorded_at_ms)}
              </summary>
              <p>用户手工录入 · {revision.change_reason}</p>
              {revision.attributions.map((item) => (
                <MainlineEvidence key={item.assertion_id} item={item} />
              ))}
            </details>
          ))}
          {history.data?.total === 0 && (
            <p className="daily-mainline-muted">
              该日尚无记录，未从行业、概念或来源原因自动填充。
            </p>
          )}
          {!!history.data?.total && (
            <div className="daily-mainline-actions">
              <button
                className="btn small"
                disabled={historyPage === 0}
                onClick={() => setHistoryPage((page) => page - 1)}
              >
                上一页
              </button>
              <span>
                第 {historyPage + 1} 页 · 共 {history.data.total} 次修订
              </span>
              <button
                className="btn small"
                disabled={(historyPage + 1) * 20 >= history.data.total}
                onClick={() => setHistoryPage((page) => page + 1)}
              >
                下一页
              </button>
            </div>
          )}
        </section>
      </div>
    </Modal>
  );
}

export default function DailyMainlines({
  date,
  rows,
  query = "",
  onStock,
  snapshotId,
}: {
  date: string;
  rows: Row[];
  query?: string;
  onStock: (id: string) => void;
  snapshotId?: unknown;
}) {
  const result = useApi<MainlineList>(
    date ? `/market/mainlines?date=${encodeURIComponent(date)}` : null,
  );
  const [editing, setEditing] = useState<{
    stock: Row;
    record?: MainlineRecord;
  } | null>(null);
  const plan = buildDailyMainlineGroups(
    date,
    rows,
    result.data?.date === date ? result.data.items : [],
  );
  const search = query.trim().toLocaleLowerCase();
  const matches = (stock: Row, record?: MainlineRecord) =>
    !search ||
    [
      stock.instrument_id,
      stock.code,
      stock.name,
      record?.display_mainline,
      ...(record?.attributions.map((item) => item.mainline_name) || []),
    ]
      .join(" ")
      .toLocaleLowerCase()
      .includes(search);
  const visibleGroups = plan.groups
    .map((group) => ({
      ...group,
      visible: group.items.filter(({ stock, record }) =>
        matches(stock, record),
      ),
    }))
    .filter((group) => group.visible.length > 0);
  return (
    <section className="daily-mainlines">
      <header className="daily-mainline-heading">
        <div>
          <h2>当日主线复盘</h2>
          <p>{date} · 每股一个展示位置，次级归属与原始证据并存</p>
        </div>
        <button className="btn small" onClick={result.reload}>
          <RefreshCw size={13} />
          刷新归因
        </button>
      </header>
      <p className="daily-mainline-notice">
        按该交易日的手工证据组织，与目录行业、概念关联独立。未归因、主组待定和冲突都是正常状态；不按人数、公司名或原因关键词推测主线。
      </p>
      {result.error && (
        <ErrorBox message={result.error} retry={result.reload} />
      )}
      {result.error && result.data && (
        <p className="daily-mainline-muted">
          当前保留上次读取的结果；读取成功前暂停编辑。
        </p>
      )}
      {result.loading ? (
        <Loading />
      ) : (
        <>
          <div className="daily-mainline-summary">
            <span>
              已返回确认涨停样本 <b>{plan.total}</b>
            </span>
            <span>
              已录入展示归因 <b>{result.data ? plan.attributed : "待核验"}</b>
            </span>
            <span>计数按证券去重；仅表示当前样本</span>
          </div>
          {result.data && !plan.total && (
            <Empty
              title="当前没有可展示的确认涨停样本"
              description="主线记录不补造涨停事件；池外记录在下方单独保留。"
            />
          )}
          {result.data && plan.total > 0 && !visibleGroups.length && (
            <Empty
              title="没有匹配的当日主线样本"
              description="请调整股票名称、代码或主线关键词；上方总数仍为当日完整样本。"
            />
          )}
          {result.data &&
            visibleGroups.map((group) => {
              return (
                <section className="daily-mainline-group" key={group.key}>
                  <header>
                    <h3>{group.name}</h3>
                    <span>
                      本组 {group.visible.length}
                      {search ? ` / ${group.items.length}` : ""} 只
                    </span>
                  </header>
                  <div className="daily-mainline-grid">
                    {group.visible.map(({ stock, record }) => (
                      <article
                        className="daily-mainline-card"
                        key={stock.instrument_id}
                      >
                        <div className="daily-mainline-card-title">
                          <button
                            className="daily-mainline-stock-name"
                            onClick={() => onStock(stock.instrument_id)}
                          >
                            <strong>{stock.name || stock.instrument_id}</strong>
                          </button>
                          <CopyStockCode
                            code={stock.code}
                            instrumentId={stock.instrument_id}
                          />
                          <span
                            className={`daily-mainline-change ${tone(stock.change_pct)}`}
                          >
                            {percent(stock.change_pct)}
                          </span>
                        </div>
                        <BoardTags
                          row={stock}
                          date={date}
                          snapshotId={snapshotId}
                        />
                        <div className="daily-mainline-tags">
                          <span>
                            {
                              mainlineStateLabels[
                                record?.display_state || "unattributed"
                              ]
                            }
                          </span>
                          {record?.secondary_mainlines.map((name) => (
                            <span key={name}>次级：{name}</span>
                          ))}
                        </div>
                        <p className="daily-mainline-muted">
                          来源原因：
                          {ladderCardAttribution(stock).sourceReason ||
                            "待核验"}
                          （池中原文，与手工主线分开）
                        </p>
                        {record?.attributions.length ? (
                          <details>
                            <summary>
                              <FileText size={12} />
                              来源与归属证据 · {record.attributions.length} 条
                            </summary>
                            {record.attributions.map((item) => (
                              <MainlineEvidence
                                key={item.assertion_id}
                                item={item}
                              />
                            ))}
                          </details>
                        ) : (
                          <p className="daily-mainline-muted">
                            {record?.action === "revoke"
                              ? "当前当日主线归属已撤销；历史证据与修订可在下方审计中查阅。"
                              : "尚无当日主线证据，行业和概念不会代填。"}
                          </p>
                        )}
                        <button
                          className="btn small"
                          disabled={!result.data || !!result.error}
                          onClick={() => setEditing({ stock, record })}
                        >
                          {record ? "更正 / 撤销 / 审计" : "有据录入主线"}
                        </button>
                      </article>
                    ))}
                  </div>
                </section>
              );
            })}
          {!!plan.outside.length && (
            <details className="daily-mainline-outside">
              <summary>
                当日其他已存记录 {plan.outside.length} 只 ·
                未纳入当前确认涨停样本统计
              </summary>
              {plan.outside.map((record) => (
                <div key={record.instrument_id}>
                  <span>
                    {record.instrument_name} · {record.instrument_id} ·{" "}
                    {mainlineStateLabels[record.display_state]}
                  </span>
                  <button
                    className="btn small"
                    disabled={!!result.error}
                    onClick={() =>
                      setEditing({
                        stock: {
                          instrument_id: record.instrument_id,
                          name: record.instrument_name,
                        },
                        record,
                      })
                    }
                  >
                    查看 / 更正
                  </button>
                </div>
              ))}
            </details>
          )}
        </>
      )}
      {editing && (
        <MainlineEditor
          key={date + editing.stock.instrument_id}
          date={date}
          stock={editing.stock}
          record={editing.record}
          onClose={() => setEditing(null)}
          onSaved={result.reload}
        />
      )}
    </section>
  );
}

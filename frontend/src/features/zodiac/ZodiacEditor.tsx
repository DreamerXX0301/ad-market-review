import ZodiacSelect from "./ZodiacSelect";
import { useEffect, useRef, useState } from "react";
import { post, patch } from "../../lib/api";
import type { Row } from "../../lib/api";
import { Modal, ErrorBox } from "../../components/UI";
import InstrumentSearch from "../../components/InstrumentSearch";
import { Star, Link2, CalendarDays, ChevronRight, X } from "lucide-react";
import { relations, yearLabel } from "./zodiacPresentation";
import { additionNotice, toggleStockSelection } from "./zodiacInteractions";

type Props = {
  year: number;
  head: Row;
  item?: Row;
  mode: "add" | "batch" | "edit" | "end";
  onClose: () => void;
  onSaved: (message: string) => void;
};
function Metadata({
  value,
  onChange,
}: {
  value: Row;
  onChange: (value: Row) => void;
}) {
  return (
    <div className="zodiac-form-grid">
      <label>
        <span>关联类型</span>
        <ZodiacSelect
          aria-label="生肖关联"
          value={value.relation_type}
          onChange={(e) =>
            onChange({ ...value, relation_type: e.target.value })
          }
        >
          {Object.entries(relations).map(([key, label]) => (
            <option key={key} value={key}>
              {label}
            </option>
          ))}
        </ZodiacSelect>
      </label>
      {value.relation_type === "custom" && (
        <label>
          <span>
            自定义名称 <em>选填</em>
          </span>
          <input
            placeholder="例如：品牌与生肖有关"
            maxLength={30}
            value={value.custom_label}
            onChange={(e) =>
              onChange({ ...value, custom_label: e.target.value })
            }
          />
        </label>
      )}
      <label className="zodiac-form-wide zodiac-reason-field">
        <span>
          观察理由 <em>选填</em>
        </span>
        <textarea
          placeholder="记录你关注它的原因，以及后续想验证的变化…"
          rows={4}
          maxLength={1000}
          value={value.reason}
          onChange={(e) => onChange({ ...value, reason: e.target.value })}
        />
        <small>{value.reason.length} / 1000</small>
      </label>
      <label className="zodiac-focus-control">
        <Star size={18} fill={value.focused ? "currentColor" : "none"} />
        <span>
          <strong>重点关注</strong>
          <small>在观察列表中优先显示</small>
        </span>
        <input
          role="switch"
          aria-label="重点关注"
          type="checkbox"
          checked={value.focused}
          onChange={(e) => onChange({ ...value, focused: e.target.checked })}
        />
      </label>
      <details
        className="zodiac-links-field"
        open={value.linksText ? true : undefined}
      >
        <summary>
          <Link2 size={15} />
          <span>添加依据链接</span>
          <small>选填 · 最多3个</small>
          <ChevronRight size={14} />
        </summary>
        <label>
          <textarea
            aria-label="依据链接"
            placeholder="每行一个 https:// 链接"
            rows={2}
            value={value.linksText}
            onChange={(e) => onChange({ ...value, linksText: e.target.value })}
          />
          <small>仅保存你提供的链接，不自动核实内容。</small>
        </label>
      </details>
    </div>
  );
}
export default function ZodiacEditor({
  year,
  head,
  item,
  mode,
  onClose,
  onSaved,
}: Props) {
  const [selections, setSelections] = useState<Row[]>(item ? [item] : []),
    [text, setText] = useState(""),
    [preview, setPreview] = useState<Row[] | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [endReason, setEndReason] = useState("");
  const [inputMethod, setInputMethod] = useState<"search" | "paste">("search");
  const adding = mode === "add" || mode === "batch";
  const searchMode = adding && inputMethod === "search";
  const pasteMode = adding && inputMethod === "paste";
  const [checked, setChecked] = useState<{ signature: string; items: Row[] }>(),
    [checkError, setCheckError] = useState(""),
    [checkAttempt, setCheckAttempt] = useState(0);
  const signature = selections.map((r) => r.instrument_id).join(" ");
  useEffect(() => {
    if (!searchMode || !signature) return;
    let cancelled = false;
    setCheckError("");
    const timer = setTimeout(() => {
      post(`/zodiac-pools/${year}/preview`, { text: signature })
        .then((result) => {
          if (!cancelled) setChecked({ signature, items: result.items });
        })
        .catch((e) => {
          if (!cancelled) setCheckError((e as Error).message);
        });
    }, 150);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [searchMode, year, signature, checkAttempt]);
  const currentChecks = checked?.signature === signature ? checked.items : [];
  const searchReady =
    !!selections.length &&
    !checkError &&
    currentChecks.length === selections.length &&
    currentChecks.every((r) => r.state === "ready" || r.state === "active");
  const [meta, setMeta] = useState<Row>({
    relation_type: item?.relation_type || "custom",
    custom_label: item?.custom_label || "",
    reason: item?.reason || "",
    linksText: (item?.links || []).join("\n"),
    focused: item?.focused || false,
  });
  const request = useRef<{ body: string; key: string } | null>(null);
  const serialize = (value: Row) => ({
    relation_type: value.relation_type,
    custom_label: value.custom_label,
    reason: value.reason,
    focused: value.focused,
    links: String(value.linksText || "")
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean),
  });
  const chosen = preview?.filter((r) => r.selected && r.instrument_id) || [];
  async function inspect() {
    setBusy(true);
    setError("");
    try {
      const data = await post(`/zodiac-pools/${year}/preview`, { text });
      setPreview(
        data.items.map((r: Row) => ({
          ...r,
          selected: r.state === "ready",
          meta: { ...meta },
        })),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function save() {
    if (busy || (searchMode && !searchReady) || (pasteMode && !chosen.length))
      return;
    setBusy(true);
    setError("");
    const base = {
      expected_business_date: head.business_date,
      expected_generation: head.generation,
    };
    const content =
      mode === "end"
        ? { ...base, revision: item!.revision, end_reason: endReason }
        : mode === "edit"
          ? { ...base, revision: item!.revision, ...serialize(meta) }
          : {
              ...base,
              items: pasteMode
                ? chosen.map((r) => ({
                    instrument_id: r.instrument_id,
                    ...serialize(r.meta),
                  }))
                : selections.map((selection) => ({
                    instrument_id: selection.instrument_id,
                    ...serialize(meta),
                  })),
            };
    const body = JSON.stringify(content);
    if (request.current?.body !== body)
      request.current = { body, key: crypto.randomUUID() };
    const payload = { ...content, request_key: request.current!.key };
    try {
      if (mode === "edit")
        await patch(`/zodiac-pool-members/${item!.member_id}`, payload);
      else if (mode === "end")
        await post(`/zodiac-pool-members/${item!.member_id}/end`, payload);
      else {
        const receipt = await post(`/zodiac-pools/${year}/members`, payload);
        onSaved(additionNotice(receipt));
        return;
      }
      onSaved(
        mode === "edit" ? "观察信息已保存，原基准保留" : "本轮观察已移入历史",
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const title = {
    add: "添加股票",
    batch: "批量添加",
    edit: "编辑观察理由",
    end: "移出观察",
  }[mode];
  return (
    <Modal
      title={title}
      onClose={() => {
        if (!busy) onClose();
      }}
      wide={mode === "batch" || pasteMode}
    >
      <form
        className="zodiac-editor"
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <div className="zodiac-editor-body">
          <div className="zodiac-editor-context">
            <span>
              <CalendarDays size={14} />
              {yearLabel(year)}
            </span>
            <small>
              基准行情日{" "}
              {(mode === "edit" || mode === "end"
                ? item?.added_business_date
                : head.business_date) || "待发布"}
            </small>
          </div>
          {adding && (
            <div
              className="zodiac-input-method"
              role="group"
              aria-label="添加方式"
            >
              <button
                type="button"
                className={searchMode ? "active" : ""}
                aria-pressed={searchMode}
                disabled={busy}
                onClick={() => setInputMethod("search")}
              >
                搜索多选
              </button>
              <button
                type="button"
                className={pasteMode ? "active" : ""}
                aria-pressed={pasteMode}
                disabled={busy}
                onClick={() => setInputMethod("paste")}
              >
                粘贴名单
              </button>
              <small>仅提交当前方式中勾选的股票</small>
            </div>
          )}
          {searchMode && (
            <>
              <InstrumentSearch
                placeholder="搜索A股名称 / 代码 / 拼音 / 首字母"
                stocksOnly
                disabled={busy}
                multiple={{
                  selectedIds: selections.map((r) => r.instrument_id),
                  limit: 100,
                }}
                onSelect={(id, row) => {
                  if (
                    !["stock", "equity", "a_share", "A"].includes(
                      row.kind || row.asset_type || "stock",
                    )
                  ) {
                    setError("请选择A股股票，指数不加入生肖观察池");
                    return;
                  }
                  setSelections((current) =>
                    toggleStockSelection(current, {
                      ...row,
                      instrument_id: id,
                    }),
                  );
                  setError("");
                }}
              />
              <section
                className="zodiac-selected-stocks"
                aria-label="本次已选股票"
              >
                <div className="zodiac-selected-heading">
                  <strong>已选 {selections.length} / 100 只</strong>
                  <small>可继续搜索，重复点击取消选择</small>
                </div>
                {!!selections.length ? (
                  <ul>
                    {selections.map((selection) => {
                      const check = currentChecks.find(
                        (r) => r.instrument_id === selection.instrument_id,
                      );
                      return (
                        <li key={selection.instrument_id}>
                          <div>
                            <strong>{selection.name}</strong>
                            <small>
                              {selection.instrument_id} ·{" "}
                              {check?.state === "active"
                                ? "已在池内，提交不覆盖"
                                : check?.state === "ready"
                                  ? "待加入"
                                  : checkError
                                    ? "核验失败"
                                    : currentChecks.length
                                      ? "无法核验，请移除后重选"
                                      : "核验中…"}
                            </small>
                          </div>
                          <button
                            type="button"
                            className="icon-btn"
                            disabled={busy}
                            aria-label={`取消选择 ${selection.name}`}
                            onClick={() =>
                              setSelections((current) =>
                                current.filter(
                                  (r) =>
                                    r.instrument_id !== selection.instrument_id,
                                ),
                              )
                            }
                          >
                            <X size={14} />
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                ) : (
                  <p>从搜索结果中勾选股票，一次提交到当前年度。</p>
                )}
                {checkError && (
                  <ErrorBox
                    message={checkError}
                    retry={() => setCheckAttempt((n) => n + 1)}
                  />
                )}
              </section>
              <p className="zodiac-selection-hint">
                下面的关联与观察理由将用于本次所选股票；已有成员保留原信息。
              </p>
            </>
          )}
          {(mode === "edit" || mode === "end") && (
            <div className="zodiac-selection">
              <span className="zodiac-stock-avatar">
                {item?.name?.slice(0, 1) || "股"}
              </span>
              <div>
                <strong>{item?.name}</strong>
                <small>
                  {item?.instrument_id} · 第{item?.round_number}轮观察
                </small>
              </div>
              <span className="zodiac-status-pill">
                {item?.status === "ended" ? "已移出" : "观察中"}
              </span>
            </div>
          )}
          {mode === "end" ? (
            <>
              <p>
                本轮归入已移出历史，基准和记录保留。以后重新加入会建立新一轮。
              </p>
              <label>
                移出原因
                <textarea
                  maxLength={1000}
                  value={endReason}
                  onChange={(e) => setEndReason(e.target.value)}
                />
              </label>
            </>
          ) : (
            <>
              <Metadata value={meta} onChange={setMeta} />
              {pasteMode && (
                <>
                  <label>
                    粘贴名称、代码、拼音或首字母（最多100项）
                    <textarea
                      rows={5}
                      disabled={busy}
                      value={text}
                      onChange={(e) => {
                        setText(e.target.value);
                        setPreview(null);
                      }}
                      placeholder="一行一个，也支持空格或逗号"
                    />
                  </label>
                  <button
                    type="button"
                    className="btn"
                    disabled={busy || !text.trim()}
                    onClick={() => void inspect()}
                  >
                    解析并预览
                  </button>
                  {preview && (
                    <div className="zodiac-batch-preview">
                      <p>
                        勾选 {chosen.length} / {preview.length}{" "}
                        项。未识别、歧义和重复项不会静默提交；共享备注在解析时复制，下面可逐项修改。
                      </p>
                      {preview.map((row, index) => (
                        <div className="zodiac-batch-row" key={index}>
                          <label className="zodiac-check">
                            <input
                              type="checkbox"
                              disabled={
                                !row.instrument_id || row.state === "duplicate"
                              }
                              checked={row.selected}
                              onChange={(e) =>
                                setPreview(
                                  preview.map((r, i) =>
                                    i === index
                                      ? { ...r, selected: e.target.checked }
                                      : r,
                                  ),
                                )
                              }
                            />
                            <strong>{row.input}</strong>
                            <span>
                              {
                                (
                                  {
                                    ready: "已核验",
                                    active: "已在池内（提交不覆盖）",
                                    duplicate: "本批重复",
                                    ambiguous: "请核对并选择候选",
                                    unrecognized: "未识别",
                                  } as Row
                                )[row.state]
                              }
                            </span>
                          </label>
                          {row.state === "ambiguous" ? (
                            <ZodiacSelect
                              aria-label={`为${row.input}选择证券`}
                              value={row.instrument_id || ""}
                              onChange={(e) =>
                                setPreview(
                                  preview.map((r, i) =>
                                    i === index
                                      ? {
                                          ...r,
                                          instrument_id: e.target.value,
                                          selected: !!e.target.value,
                                        }
                                      : r,
                                  ),
                                )
                              }
                            >
                              <option value="">请选择准确证券</option>
                              {row.candidates.map((c: Row) => (
                                <option
                                  key={c.instrument_id}
                                  value={c.instrument_id}
                                >
                                  {c.name} {c.instrument_id}
                                  {c.is_suggestion ? "（近似建议）" : ""}
                                </option>
                              ))}
                            </ZodiacSelect>
                          ) : (
                            <small>{row.instrument_id}</small>
                          )}
                          {row.candidate_count > 20 && (
                            <small>
                              共{row.candidate_count}
                              项匹配，显示前20项；可改用搜索多选或输入更完整的名称。
                            </small>
                          )}
                          {row.instrument_id && row.state !== "duplicate" && (
                            <details>
                              <summary>本项关联与备注</summary>
                              <Metadata
                                value={row.meta}
                                onChange={(value) =>
                                  setPreview(
                                    preview.map((r, i) =>
                                      i === index ? { ...r, meta: value } : r,
                                    ),
                                  )
                                }
                              />
                            </details>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </>
              )}
            </>
          )}
          {error && <ErrorBox message={error} />}
        </div>
        <div className="zodiac-dialog-actions">
          <small>
            {mode === "edit"
              ? "仅更新观察信息，原基准保留"
              : mode === "end"
                ? "历史记录与基准保留"
                : "按实际收盘保存独立观察基准"}
          </small>
          <button
            type="button"
            className="btn"
            disabled={busy}
            onClick={onClose}
          >
            取消
          </button>
          <button
            className="btn primary"
            disabled={
              busy ||
              !head.business_date ||
              (searchMode && !searchReady) ||
              (pasteMode && !chosen.length)
            }
          >
            {busy
              ? "保存中…"
              : pasteMode
                ? `添加勾选的 ${chosen.length} 项`
                : searchMode
                  ? `将 ${selections.length} 只加入 ${year} 观察池`
                  : mode === "end"
                    ? "结束本轮观察"
                    : "保存修改"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

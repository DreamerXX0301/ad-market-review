import {
  createContext,
  useContext,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";
import type { ReactNode } from "react";
import {
  AlertCircle,
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  ChevronLeft,
  ChevronRight,
  Inbox,
  LoaderCircle,
  RefreshCw,
  X,
} from "lucide-react";
import {
  amount,
  num,
  percent,
  tone,
  textValue,
  label,
  statusText,
} from "../lib/format";
import type { Row } from "../lib/api";
import { isHiddenIndicatorField } from "../lib/indicatorVisibility";
import { isSortValue, sortTableRows } from "./tableSort";
import type { SortValue, TableSort } from "./tableSort";
import { canRestoreModalFocus, createModalLayers } from "./modalLayers";
import CopyStockCode from "./CopyStockCode";
const modalLayers = createModalLayers();
const ModalParentContext = createContext<string | null>(null);
export const ToastContext = createContext<
  (message: string, kind?: "success" | "error") => void
>(() => {});
export const useToast = () => useContext(ToastContext);
export const STOCK_DRAWER_VISIBILITY_EVENT =
  "workbench-stock-drawer-visibility";
export function Spinner() {
  return <LoaderCircle className="spin" size={17} />;
}
export function Badge({
  children,
  tone: color = "neutral",
}: {
  children: ReactNode;
  tone?: string;
}) {
  return <span className={"badge " + color}>{children}</span>;
}
export function Status({ value }: { value: any }) {
  return (
    <Badge
      tone={
        ["failed", "rejected", "permission_denied"].includes(value)
          ? "danger"
          : [
                "completed",
                "success",
                "succeeded",
                "no_change",
                "ready",
                "available",
                "filled",
                "matched",
                "tracking",
              ].includes(value)
            ? "good"
            : [
                  "running",
                  "queued",
                  "paused",
                  "partial",
                  "unknown",
                  "unverified",
                ].includes(value)
              ? "warn"
              : "neutral"
      }
    >
      {statusText[value] || value || "未知"}
    </Badge>
  );
}
export function Empty({
  title = "暂无数据",
  description = "完成数据初始化后，这里会显示实际结果。",
  action,
}: {
  title?: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty">
      <Inbox size={29} />
      <h3>{title}</h3>
      <p>{description}</p>
      {action}
    </div>
  );
}
export function ErrorBox({
  message,
  retry,
}: {
  message: string;
  retry?: () => void;
}) {
  return (
    <div className="error-box">
      <AlertCircle size={18} />
      <div>
        <strong>请求未完成</strong>
        <p>{message}</p>
      </div>
      {retry && (
        <button onClick={retry} className="btn small">
          <RefreshCw size={13} />
          重试
        </button>
      )}
    </div>
  );
}
export function Loading() {
  return (
    <div className="loading">
      <Spinner />
      正在读取实际数据…
    </div>
  );
}
export function Panel({
  title,
  subtitle,
  actions,
  children,
  className = "",
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={"panel " + className}>
      {(title || actions) && (
        <div className="panel-head">
          <div>
            <h2>{title}</h2>
            {subtitle && <p>{subtitle}</p>}
          </div>
          <div className="panel-actions">{actions}</div>
        </div>
      )}
      {children}
    </section>
  );
}
export function Warning({ children }: { children: ReactNode }) {
  return (
    <div className="notice">
      <AlertCircle size={15} />
      <div>{children}</div>
    </div>
  );
}
export function Warnings({ items }: { items: any }) {
  if (!items) return null;
  const values = Array.isArray(items) ? items : [items];
  return (
    <>
      {values.filter(Boolean).map((x: any, i: number) => (
        <Warning key={i}>
          {typeof x === "object"
            ? x.message || x.reason || JSON.stringify(x)
            : x}
        </Warning>
      ))}
    </>
  );
}
export function Stat({
  label: caption,
  value,
  sub,
  className = "",
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  className?: string;
}) {
  return (
    <div className={"stat " + className}>
      <span className="stat-label">{caption}</span>
      <strong>{value}</strong>
      {sub && <span className="stat-sub">{sub}</span>}
    </div>
  );
}
export function Modal({
  title,
  children,
  onClose,
  wide = false,
  escapeUnderStock = false,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  wide?: boolean;
  escapeUnderStock?: boolean;
}) {
  const parentLayer = useContext(ModalParentContext);
  const layerId = useId();
  const modalRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const capturedOpener = useRef(false);
  const openerRef = useRef<HTMLElement | null>(null);
  const [topLayer, setTopLayer] = useState<string | null>(modalLayers.top);
  const [stockDrawerOpen, setStockDrawerOpen] = useState(
    () =>
      escapeUnderStock &&
      typeof document !== "undefined" &&
      !!document.querySelector(".stock-drawer"),
  );
  useEffect(() => {
    if (!capturedOpener.current) {
      capturedOpener.current = true;
      openerRef.current =
        document.activeElement instanceof HTMLElement
          ? document.activeElement
          : null;
    }
    const unsubscribe = modalLayers.subscribe(() =>
      setTopLayer(modalLayers.top()),
    );
    const remove = modalLayers.register(layerId, parentLayer);
    return () => {
      unsubscribe();
      remove();
      requestAnimationFrame(() => {
        // A StrictMode replay or a replacement layer already owns focus.
        if (modalLayers.has(layerId)) return;
        const currentId = modalLayers.top();
        const current = currentId
          ? ([
              ...document.querySelectorAll<HTMLElement>("[data-modal-layer]"),
            ].find((node) => node.dataset.modalLayer === currentId) ?? null)
          : null;
        const opener = openerRef.current;
        if (opener !== document.body && canRestoreModalFocus(opener, current)) {
          opener?.focus({ preventScroll: true });
        } else if (!current && !document.querySelector(".stock-drawer")) {
          const main = document.querySelector<HTMLElement>("main");
          if (!main) return;
          const prior = main.getAttribute("tabindex");
          main.tabIndex = -1;
          main.focus({ preventScroll: true });
          if (prior === null) main.removeAttribute("tabindex");
          else main.setAttribute("tabindex", prior);
        }
      });
    };
  }, [layerId, parentLayer]);
  useEffect(() => {
    if (topLayer === layerId && !(escapeUnderStock && stockDrawerOpen))
      closeRef.current?.focus({ preventScroll: true });
  }, [topLayer, layerId, escapeUnderStock, stockDrawerOpen]);
  useEffect(() => {
    if (!escapeUnderStock) {
      setStockDrawerOpen(false);
      return;
    }
    setStockDrawerOpen(!!document.querySelector(".stock-drawer"));
    const changed = (event: Event) =>
      setStockDrawerOpen((event as CustomEvent<{ open: boolean }>).detail.open);
    window.addEventListener(STOCK_DRAWER_VISIBILITY_EVENT, changed);
    return () =>
      window.removeEventListener(STOCK_DRAWER_VISIBILITY_EVENT, changed);
  }, [escapeUnderStock]);
  useEffect(() => {
    const f = (e: KeyboardEvent) => {
      if (
        e.key === "Escape" &&
        !(escapeUnderStock && stockDrawerOpen) &&
        modalLayers.isTopForEvent(layerId, e)
      ) {
        e.preventDefault();
        e.stopImmediatePropagation();
        onClose();
      }
    };
    // Read this layer's visibility at the start of the keyboard event, before
    // an upper drawer can synchronously unmount and change the DOM underneath.
    document.addEventListener("keydown", f, escapeUnderStock);
    return () => document.removeEventListener("keydown", f, escapeUnderStock);
  }, [onClose, escapeUnderStock, stockDrawerOpen, layerId]);
  return (
    <ModalParentContext.Provider value={layerId}>
      <div
        className="modal-backdrop"
        onMouseDown={(e) => {
          if (
            e.target === e.currentTarget &&
            !(escapeUnderStock && stockDrawerOpen) &&
            modalLayers.isTopForEvent(layerId, e.nativeEvent)
          )
            onClose();
        }}
      >
        <div
          ref={modalRef}
          data-modal-layer={layerId}
          role="dialog"
          aria-modal={
            !stockDrawerOpen && topLayer === layerId ? true : undefined
          }
          aria-hidden={stockDrawerOpen || undefined}
          inert={stockDrawerOpen || undefined}
          aria-label={title}
          className={"modal " + (wide ? "wide" : "")}
        >
          <div className="modal-title">
            <h2>{title}</h2>
            <button
              ref={closeRef}
              className="icon-btn"
              aria-label="关闭"
              onClick={() => {
                if (
                  modalLayers.top() === layerId &&
                  !(escapeUnderStock && stockDrawerOpen)
                )
                  onClose();
              }}
            >
              <X size={20} />
            </button>
          </div>
          {children}
        </div>
      </div>
    </ModalParentContext.Provider>
  );
}
export type Column = {
  key: string;
  title?: ReactNode;
  render?: (value: any, row: Row, index: number) => ReactNode;
  align?: string;
  sortable?: boolean;
  sortValue?: (row: Row) => SortValue;
};
export function Cell({ field, value }: { field: string; value: any }) {
  if (isHiddenIndicatorField(field)) return null;
  if (value == null) return <span className="muted">—</span>;
  if (field === "status" || field === "decision")
    return <Status value={value} />;
  if (
    field.includes("pct") ||
    field === "turnover_rate" ||
    field === "return_30d" ||
    ["total_return", "annual_return", "win_rate", "max_drawdown"].includes(
      field,
    )
  )
    return <span className={tone(value)}>{percent(value)}</span>;
  if (
    [
      "amount",
      "net_inflow",
      "net_pnl",
      "pnl",
      "final_equity",
      "market_value",
      "seal_money",
      "fees",
      "fee",
      "total_fees",
      "initial_cash",
      "equity",
    ].includes(field)
  )
    return (
      <span
        className={field === "net_pnl" || field === "pnl" ? tone(value) : ""}
      >
        {amount(value)}
      </span>
    );
  if (field === "topics" || field === "risk_labels" || field === "topic_names")
    return (
      <div className="tags">
        {(Array.isArray(value) ? value : [value])
          .slice(0, 4)
          .map((v: any, i: number) => (
            <Badge key={i} tone={field === "risk_labels" ? "warn" : "neutral"}>
              {textValue(v)}
            </Badge>
          ))}
      </div>
    );
  return (
    <>
      {typeof value === "number"
        ? num(value, Number.isInteger(value) ? 0 : 2)
        : textValue(value)}
    </>
  );
}
export function Table({
  rows,
  columns,
  onStock,
  onStockNavigate,
  emptyTitle = "暂无记录",
  emptyDescription,
  pageSize = 15,
  selection,
  onSelect,
  rowKey,
  sorting = "auto",
}: {
  rows: Row[];
  columns: Column[];
  onStock?: (id: string) => void;
  /** Optional stock browsing uses the filtered/sorted list before pagination. */
  onStockNavigate?: (id: string, orderedRows: Row[], clickedRow: Row) => void;
  emptyTitle?: string;
  emptyDescription?: string;
  pageSize?: number;
  selection?: Set<string>;
  onSelect?: (id: string) => void;
  rowKey?: string;
  /** external: the caller already sorts the full server-side result. */
  sorting?: "auto" | "local" | "external" | "none";
}) {
  rows = rows.filter((row) => !isHiddenIndicatorField(row.field));
  columns = columns.filter((column) => !isHiddenIndicatorField(column.key));
  const [page, setPage] = useState(0);
  const [sort, setSort] = useState<TableSort | null>(null);
  const customHeadings = columns.some(
    (column) =>
      column.title != null &&
      !["string", "number"].includes(typeof column.title),
  );
  const localSorting =
    sorting === "local" || (sorting === "auto" && !customHeadings);
  const sortable = (column: Column) =>
    localSorting &&
    column.sortable !== false &&
    !["actions", "action", "checks", "evidence"].includes(column.key) &&
    (column.title == null ||
      ["string", "number"].includes(typeof column.title)) &&
    (column.sortValue != null ||
      column.sortable === true ||
      (rows.some((row) => row[column.key] != null) &&
        rows.every((row) => isSortValue(row[column.key]))));
  const sortColumn = columns.find((column) => column.key === sort?.key);
  const sorted =
    sort && sortColumn && sortable(sortColumn)
      ? sortTableRows(rows, sort, (row) =>
          sortColumn.sortValue
            ? sortColumn.sortValue(row)
            : row[sortColumn.key],
        )
      : rows;
  const rowSignature = rows
    .map(
      (r, i) =>
        String(
          r[rowKey || "instrument_id"] ||
            r.round_id ||
            r.event_id ||
            r.topic_id ||
            r.order_id ||
            r.field ||
            r.name ||
            i,
        ) +
        ":" +
        String(r.business_date || r.date || r.entry_date || ""),
    )
    .join("|");
  useEffect(() => setPage(0), [rowSignature, sort?.key, sort?.order]);
  const safePage = Math.min(
    page,
    Math.max(0, Math.ceil(rows.length / pageSize) - 1),
  );
  const start = safePage * pageSize,
    part = sorted.slice(start, start + pageSize);
  if (!rows.length)
    return <Empty title={emptyTitle} description={emptyDescription} />;
  return (
    <>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              {onSelect && <th className="check-cell"></th>}
              {columns.map((c) => (
                <th
                  key={c.key}
                  className={c.align === "right" ? "text-right" : ""}
                  aria-sort={
                    sortable(c)
                      ? sort?.key === c.key
                        ? sort?.order === "asc"
                          ? "ascending"
                          : "descending"
                        : "none"
                      : undefined
                  }
                >
                  {sortable(c)
                    ? (() => {
                        const active = sort?.key === c.key;
                        const Icon = active
                          ? sort?.order === "asc"
                            ? ArrowUp
                            : ArrowDown
                          : ArrowUpDown;
                        const caption = c.title || label[c.key] || c.key;
                        return (
                          <button
                            className="link"
                            style={{
                              display: "inline-flex",
                              alignItems: "center",
                              gap: 4,
                              fontSize: "inherit",
                              fontWeight: "inherit",
                              color: "inherit",
                              whiteSpace: "nowrap",
                            }}
                            title="按原始值排序全部已加载记录后分页；未知值始终排末"
                            aria-label={
                              "按" +
                              String(caption) +
                              (active && sort?.order === "desc"
                                ? "升序"
                                : "降序") +
                              "排列"
                            }
                            onClick={() => {
                              setSort({
                                key: c.key,
                                order:
                                  active && sort?.order === "desc"
                                    ? "asc"
                                    : "desc",
                              });
                              setPage(0);
                            }}
                          >
                            {caption}
                            <Icon size={12} />
                          </button>
                        );
                      })()
                    : c.title || label[c.key] || c.key}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {part.map((r, i) => {
              const id =
                r[rowKey || "instrument_id"] ||
                r.round_id ||
                r.topic_id ||
                String(start + i);
              return (
                <tr key={id + "-" + i}>
                  {onSelect && (
                    <td>
                      <input
                        type="checkbox"
                        aria-label={"选择" + (r.name || r.code || id)}
                        checked={selection?.has(id)}
                        onChange={() => onSelect(id)}
                      />
                    </td>
                  )}
                  {columns.map((c) => (
                    <td
                      key={c.key}
                      className={c.align === "right" ? "text-right" : ""}
                    >
                      {c.render ? (
                        c.render(r[c.key], r, start + i)
                      ) : c.key === "name" &&
                        (onStock || onStockNavigate) &&
                        r.instrument_id ? (
                        <div className="stock-identity-cell">
                          <button
                            className="stock-link"
                            onClick={() =>
                              onStockNavigate
                                ? onStockNavigate(r.instrument_id, sorted, r)
                                : onStock?.(r.instrument_id)
                            }
                          >
                            <strong>{r.name || r.code}</strong>
                          </button>
                          <CopyStockCode
                            code={r.code}
                            instrumentId={r.instrument_id}
                          />
                        </div>
                      ) : ["code", "instrument_id"].includes(c.key) &&
                        r.instrument_id ? (
                        <CopyStockCode
                          code={r[c.key]}
                          instrumentId={r.instrument_id}
                          variant={c.key === "instrument_id" ? "full" : "short"}
                        />
                      ) : (
                        <Cell field={c.key} value={r[c.key]} />
                      )}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {rows.length > pageSize && (
        <div className="table-footer">
          <span>
            共 {rows.length.toLocaleString()} 条 · {start + 1}–
            {Math.min(start + pageSize, rows.length)}
          </span>
          <div className="button-group">
            <button
              className="icon-btn"
              aria-label="上一页"
              disabled={!safePage}
              onClick={() => setPage((p) => p - 1)}
            >
              <ChevronLeft size={15} />
            </button>
            <span>
              {safePage + 1} / {Math.ceil(rows.length / pageSize)}
            </span>
            <button
              className="icon-btn"
              aria-label="下一页"
              disabled={start + pageSize >= rows.length}
              onClick={() => setPage((p) => p + 1)}
            >
              <ChevronRight size={15} />
            </button>
          </div>
        </div>
      )}
    </>
  );
}
export function inferColumns(rows: Row[], prefer: string[] = []): Column[] {
  const banned = new Set([
    "instrument_id",
    "checks",
    "evidence",
    "snapshot_id",
    "raw",
    "metadata",
    "id",
  ]);
  const keys = rows.length
    ? [...new Set(rows.slice(0, 6).flatMap(Object.keys))].filter(
        (x) => !banned.has(x),
      )
    : prefer;
  const ordered = [
    ...prefer.filter((k) => keys.includes(k)),
    ...keys.filter((k) => !prefer.includes(k)),
  ]
    .filter((key) => !isHiddenIndicatorField(key))
    .slice(0, 13);
  return ordered.map((key) => ({ key }));
}

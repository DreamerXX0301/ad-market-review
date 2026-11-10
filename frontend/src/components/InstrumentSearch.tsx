import {
  Fragment,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import { Check, Search, X } from "lucide-react";
import { list, useApi } from "../lib/api";
import type { Row } from "../lib/api";
import { Spinner } from "./UI";
import { instrumentSearchLayout } from "./instrumentSearchLayout";
import {
  instrumentSearchRequest,
  loadRecentStocks,
  rememberRecentStock,
  searchResultView,
} from "./searchPresentation";
import "./InstrumentSearch.css";
import CopyStockCode from "./CopyStockCode";
export default function InstrumentSearch({
  onSelect,
  placeholder = "搜索股票 / 指数 / 代码",
  compact = false,
  includeSectors = false,
  onSector,
  stocksOnly = false,
  multiple,
  disabled = false,
  autoFocus = false,
  recentStocks = false,
}: {
  onSelect: (id: string, row: Row) => void;
  placeholder?: string;
  compact?: boolean;
  includeSectors?: boolean;
  onSector?: (row: Row) => void;
  stocksOnly?: boolean;
  multiple?: { selectedIds: readonly string[]; limit: number };
  disabled?: boolean;
  autoFocus?: boolean;
  recentStocks?: boolean;
}) {
  const [query, setQuery] = useState(""),
    [debounced, setDebounced] = useState(""),
    [open, setOpen] = useState(false),
    [activeIndex, setActiveIndex] = useState(0),
    [portalHost, setPortalHost] = useState<Element | null>(null),
    [layout, setLayout] =
      useState<ReturnType<typeof instrumentSearchLayout>>(null);
  const container = useRef<HTMLDivElement>(null);
  const popup = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const composingRef = useRef(false);
  const [composing, setComposing] = useState(false);
  const [recent, setRecent] = useState<ReturnType<typeof loadRecentStocks>>([]);
  const listId = useId();
  const text = query.trim();
  const hasQuery = !!text;
  const waiting = composing || text !== debounced;
  const showingRecent = recentStocks && !hasQuery && !composing;
  const visible =
    !disabled && open && (hasQuery || (showingRecent && !!recent.length));
  useEffect(() => {
    if (composing) return;
    const t = setTimeout(() => setDebounced(query.trim()), 200);
    return () => clearTimeout(t);
  }, [query, composing]);
  const result = useApi<Row>(
    waiting ? null : instrumentSearchRequest(text, includeSectors, stocksOnly),
  );
  // A newly typed query must not select the previous query's still-loaded rows.
  const searching = hasQuery && (waiting || result.loading);
  const searchError = hasQuery ? result.error : "";
  const rows: Row[] = showingRecent
    ? recent
    : !hasQuery || searching || searchError
      ? []
      : list(result.data).filter((row) => {
          const view = searchResultView(row);
          return (
            view.selectable &&
            (!stocksOnly || view.kind === "stock") &&
            (!view.sector || (includeSectors && !!onSector))
          );
        });
  useLayoutEffect(() => {
    if (!visible) return;
    // Keep a floating list within its dialog's accessibility tree, where present.
    setPortalHost(
      container.current?.closest('[role="dialog"]') || document.body,
    );
    const update = () => {
      const anchor = container.current?.getBoundingClientRect();
      if (!anchor) return;
      const next = instrumentSearchLayout(anchor, {
        width: window.innerWidth,
        height: window.innerHeight,
      });
      setLayout(next);
      if (!next) setOpen(false);
    };
    const outside = (event: PointerEvent) => {
      const target = event.target as Node | null;
      if (
        target &&
        !container.current?.contains(target) &&
        !popup.current?.contains(target)
      )
        setOpen(false);
    };
    // Capture before an outer modal handles Escape, so only this list closes.
    const escape = (event: KeyboardEvent) => {
      if (
        event.key === "Escape" &&
        !event.isComposing &&
        event.keyCode !== 229 &&
        !composingRef.current &&
        (container.current?.contains(event.target as Node) ||
          popup.current?.contains(event.target as Node))
      ) {
        event.preventDefault();
        event.stopPropagation();
        setOpen(false);
        input.current?.focus();
      }
    };
    update();
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    document.addEventListener("pointerdown", outside);
    window.addEventListener("keydown", escape, true);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
      document.removeEventListener("pointerdown", outside);
      window.removeEventListener("keydown", escape, true);
    };
  }, [visible]);
  useEffect(() => {
    if (visible && rows[activeIndex])
      document
        .getElementById(`${listId}-${activeIndex}`)
        ?.scrollIntoView({ block: "nearest" });
  }, [visible, activeIndex, debounced, result.data, recent]);
  function openSearch() {
    if (showingRecent) {
      setRecent(loadRecentStocks());
      setActiveIndex(0);
    }
    setOpen(true);
  }
  function choose(row: Row) {
    const view = searchResultView(row);
    if (disabled || !view.selectable || optionDisabled(row)) return;
    if (view.sector) {
      if (!includeSectors || !onSector) return;
      onSector(row);
    } else {
      if (recentStocks && view.kind === "stock") {
        const updated = rememberRecentStock(row);
        if (updated) setRecent(updated);
      }
      onSelect(row.instrument_id, row);
    }
    if (multiple) {
      input.current?.focus();
    } else {
      setQuery("");
      setOpen(false);
    }
  }
  const selected = (row: Row) =>
    !!multiple?.selectedIds.includes(row.instrument_id);
  const optionDisabled = (row: Row) =>
    !!multiple &&
    !selected(row) &&
    multiple.selectedIds.length >= multiple.limit;
  return (
    <div
      ref={container}
      className={"instrument-search " + (compact ? "compact" : "")}
    >
      <Search size={15} />
      <input
        ref={input}
        autoFocus={autoFocus}
        disabled={disabled}
        role="combobox"
        aria-label={placeholder}
        aria-autocomplete="list"
        aria-expanded={visible}
        aria-controls={visible ? listId : undefined}
        aria-activedescendant={
          visible && rows[activeIndex] ? `${listId}-${activeIndex}` : undefined
        }
        placeholder={placeholder}
        maxLength={100}
        value={query}
        onFocus={openSearch}
        onClick={() => {
          if (showingRecent && !open) openSearch();
        }}
        onCompositionStart={() => {
          composingRef.current = true;
          setComposing(true);
        }}
        onCompositionEnd={(e) => {
          composingRef.current = false;
          setQuery(e.currentTarget.value);
          setComposing(false);
        }}
        onChange={(e) => {
          setQuery(e.target.value);
          if (recentStocks && hasQuery && !e.target.value.trim())
            setRecent(loadRecentStocks());
          setActiveIndex(0);
          setOpen(true);
        }}
        onKeyDown={(e) => {
          if (composingRef.current || e.nativeEvent.isComposing) return;
          if (e.key === "Tab") {
            const copyButton =
              visible && !e.shiftKey
                ? popup.current?.querySelector<HTMLButtonElement>(
                    `[data-search-index="${activeIndex}"] [data-copy-stock-code]`,
                  )
                : null;
            if (copyButton) {
              e.preventDefault();
              copyButton.focus();
            } else setOpen(false);
          }
          if (e.key === "Escape") {
            e.stopPropagation();
            setOpen(false);
          }
          if (e.key === "ArrowDown" || e.key === "ArrowUp") {
            e.preventDefault();
            if (showingRecent && !open) {
              const history = loadRecentStocks();
              setRecent(history);
              setActiveIndex(
                e.key === "ArrowUp" ? Math.max(0, history.length - 1) : 0,
              );
              setOpen(true);
              return;
            }
            setOpen(true);
            if (rows.length)
              setActiveIndex((index) =>
                e.key === "ArrowDown"
                  ? visible
                    ? (index + 1) % rows.length
                    : 0
                  : visible
                    ? (index + rows.length - 1) % rows.length
                    : rows.length - 1,
              );
          }
          if (e.key === "Enter" && visible) {
            e.preventDefault();
            if (rows[activeIndex]) choose(rows[activeIndex]);
          }
        }}
      />
      {query && (
        <button
          type="button"
          aria-label="清空搜索"
          className="icon-btn"
          onClick={() => {
            setQuery("");
            if (recentStocks) {
              setRecent(loadRecentStocks());
              setActiveIndex(0);
              setOpen(true);
            }
            input.current?.focus();
          }}
        >
          <X size={13} />
        </button>
      )}
      {visible &&
        layout &&
        portalHost &&
        createPortal(
          <div
            ref={popup}
            id={listId}
            role="listbox"
            aria-multiselectable={multiple ? true : undefined}
            aria-label={
              showingRecent
                ? "近 7 天查看的股票"
                : stocksOnly
                  ? "匹配的A股股票"
                  : includeSectors
                    ? "匹配的股票、指数、行业与概念"
                    : "匹配的股票与指数"
            }
            aria-busy={searching}
            className="instrument-search-popover"
            style={layout}
            onMouseDown={(event) => event.preventDefault()}
            onBlur={(event) => {
              const next = event.relatedTarget as Node | null;
              if (
                next &&
                !popup.current?.contains(next) &&
                !container.current?.contains(next)
              )
                setOpen(false);
            }}
          >
            {showingRecent && (
              <div className="search-recent-heading">最近查看 · 近 7 天</div>
            )}
            {searching ? (
              <div className="search-hint">
                <Spinner />
                正在检索…
              </div>
            ) : searchError ? (
              <div className="search-hint">{searchError}</div>
            ) : !rows.length ? (
              <div className="search-hint">没有匹配的标的</div>
            ) : (
              rows.map((r, index) => (
                <Fragment key={searchResultView(r).key}>
                  {r.is_suggestion === true &&
                    rows[index - 1]?.is_suggestion !== true && (
                      <div className="search-suggestion-heading">
                        可能想找 · 请核对名称和代码
                      </div>
                    )}
                  <div
                    className="instrument-search-result"
                    key={searchResultView(r).key}
                    data-search-index={index}
                    data-active={index === activeIndex}
                    onMouseEnter={() => setActiveIndex(index)}
                  >
                    <button
                      type="button"
                      className="instrument-search-choose"
                      title={searchResultView(r).title}
                      aria-label={searchResultView(r).title}
                      id={`${listId}-${index}`}
                      role="option"
                      aria-selected={
                        multiple ? selected(r) : index === activeIndex
                      }
                      aria-disabled={optionDisabled(r) || undefined}
                      tabIndex={-1}
                      onClick={() => choose(r)}
                    >
                      {multiple && (
                        <span
                          className="search-selection-mark"
                          aria-hidden="true"
                        >
                          {selected(r) && <Check size={13} />}
                        </span>
                      )}
                      <span>
                        <strong>{r.name}</strong>
                        <small
                          className={`search-result-type type-${searchResultView(r).kind}`}
                        >
                          {searchResultView(r).label}
                        </small>
                        {searchResultView(r).matchLabel && (
                          <small
                            className={`search-match-label${r.is_suggestion === true ? " is-suggestion" : ""}`}
                          >
                            {searchResultView(r).matchLabel}
                          </small>
                        )}
                      </span>
                      {searchResultView(r).sector && (
                        <code>{r.code || r.topic_code}</code>
                      )}
                    </button>
                    {!searchResultView(r).sector && (
                      <CopyStockCode
                        code={r.code}
                        instrumentId={r.instrument_id}
                      />
                    )}
                  </div>
                </Fragment>
              ))
            )}
            {multiple && (
              <div className="search-multiple-footer">
                <span>
                  已选 {multiple.selectedIds.length} / {multiple.limit} 只 ·
                  可继续搜索
                </span>
                <button
                  type="button"
                  className="btn small"
                  onClick={() => {
                    setOpen(false);
                    input.current?.focus();
                  }}
                >
                  完成选择
                </button>
              </div>
            )}
          </div>,
          portalHost,
        )}
    </div>
  );
}

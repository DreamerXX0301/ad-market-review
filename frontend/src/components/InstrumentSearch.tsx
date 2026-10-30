import {
  Fragment,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import { Search, X } from "lucide-react";
import { list, useApi } from "../lib/api";
import type { Row } from "../lib/api";
import { Spinner } from "./UI";
import { instrumentSearchLayout } from "./instrumentSearchLayout";
import {
  instrumentSearchRequest,
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
}: {
  onSelect: (id: string, row: Row) => void;
  placeholder?: string;
  compact?: boolean;
  includeSectors?: boolean;
  onSector?: (row: Row) => void;
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
  const listId = useId();
  const visible = open && !!query.trim();
  useEffect(() => {
    if (composing) return;
    const t = setTimeout(() => setDebounced(query.trim()), 200);
    return () => clearTimeout(t);
  }, [query, composing]);
  const result = useApi<Row>(
    composing ? null : instrumentSearchRequest(debounced, includeSectors),
  );
  // A newly typed query must not select the previous query's still-loaded rows.
  const searching = composing || query.trim() !== debounced || result.loading;
  const rows =
    searching || result.error
      ? []
      : list(result.data).filter((row) => {
          const view = searchResultView(row);
          return (
            view.selectable && (!view.sector || (includeSectors && !!onSector))
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
  }, [visible, activeIndex, debounced, result.data]);
  function choose(row: Row) {
    const view = searchResultView(row);
    if (!view.selectable) return;
    if (view.sector) {
      if (!includeSectors || !onSector) return;
      onSector(row);
    } else onSelect(row.instrument_id, row);
    setQuery("");
    setOpen(false);
  }
  return (
    <div
      ref={container}
      className={"instrument-search " + (compact ? "compact" : "")}
    >
      <Search size={15} />
      <input
        ref={input}
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
        onFocus={() => setOpen(true)}
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
          if (e.key === "Enter" && visible && rows[activeIndex]) {
            e.preventDefault();
            choose(rows[activeIndex]);
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
            aria-label={
              includeSectors
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
            {searching ? (
              <div className="search-hint">
                <Spinner />
                正在检索…
              </div>
            ) : result.error ? (
              <div className="search-hint">{result.error}</div>
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
                      aria-selected={index === activeIndex}
                      tabIndex={-1}
                      onClick={() => choose(r)}
                    >
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
          </div>,
          portalHost,
        )}
    </div>
  );
}

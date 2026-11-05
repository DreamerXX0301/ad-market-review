import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import PageScrollEdges from "../../components/PageScrollEdges";
import { revealEmotionSection } from "./emotionNavigation";
import {
  noonReadingAtLine,
  noonManualScrollKey,
  noonReadingScrollOwner,
} from "./noonReadingScroll";
import {
  intradayReadingScrollTop,
  intradayReadingSections,
  isIntradayPageScroll,
} from "./intradayReadingNavigation";
import type { IntradayReadingKey } from "./intradayReadingNavigation";
import "./IntradayPageNavigation.css";

type Entry = { id: string; key: IntradayReadingKey; node: HTMLElement };

export function renderedIntradaySections(main: HTMLElement): Entry[] {
  const seen = new Set<string>();
  return [...main.querySelectorAll<HTMLElement>("[data-intraday-reading]")]
    .filter((node) => {
      const key = node.dataset.intradayReading || "";
      if (
        !Object.hasOwn(intradayReadingSections, key) ||
        !node.id ||
        seen.has(node.id)
      )
        return false;
      // A wrapper around a component returning null is not an available section.
      if (!node.firstElementChild || !node.getClientRects().length)
        return false;
      seen.add(node.id);
      return true;
    })
    .map((node) => ({
      id: node.id,
      key: node.dataset.intradayReading as IntradayReadingKey,
      node,
    }));
}

/** A directory for the current observation. It never selects a different slot. */
export default function IntradayPageNavigation({
  scopeKey,
  label,
  children,
}: {
  scopeKey: string;
  label?: string;
  children: ReactNode;
}) {
  const root = useRef<HTMLDivElement>(null);
  const main = useRef<HTMLDivElement>(null);
  const menu = useRef<HTMLElement>(null);
  const pinned = useRef<string | null>(null);
  const pinUntil = useRef(0);
  const [entries, setEntries] = useState<
    Array<{ id: string; key: IntradayReadingKey }>
  >([]);
  const [active, setActive] = useState("");

  useEffect(() => {
    const boundary = root.current;
    const content = main.current;
    if (!boundary || !content) return;
    pinned.current = null;
    let frame = 0;
    const document = boundary.ownerDocument;
    const owner = noonReadingScrollOwner(boundary);
    const header = boundary
      .closest(".app-main")
      ?.querySelector<HTMLElement>(".topbar");
    const update = () => {
      frame = 0;
      const targets = renderedIntradaySections(content);
      setEntries((previous) =>
        previous.map((row) => row.id).join("|") ===
        targets.map((row) => row.id).join("|")
          ? previous
          : targets.map(({ id, key }) => ({ id, key })),
      );
      const viewportTop = owner?.getBoundingClientRect().top || 0;
      const viewportBottom =
        owner?.getBoundingClientRect().bottom || window.innerHeight;
      const top = Math.max(
        viewportTop,
        header?.getBoundingClientRect().bottom || 0,
      );
      const compact = window.matchMedia("(max-width: 1100px)").matches;
      const directoryHeight = compact
        ? menu.current?.getBoundingClientRect().height || 0
        : 0;
      boundary.style.setProperty(
        "--intraday-directory-top",
        `${Math.max(0, top - viewportTop) + 10}px`,
      );
      boundary.style.setProperty(
        "--intraday-directory-anchor-offset",
        `${Math.max(0, top - viewportTop) + directoryHeight + 20}px`,
      );
      const selected = targets.find((row) => row.id === pinned.current);
      if (
        selected &&
        (performance.now() < pinUntil.current ||
          (selected.node.getBoundingClientRect().bottom > top &&
            selected.node.getBoundingClientRect().top < viewportBottom))
      )
        return;
      pinned.current = null;
      const scrolling = owner || document.scrollingElement;
      const offset = owner?.scrollTop ?? window.scrollY;
      const height = owner?.clientHeight ?? window.innerHeight;
      const atEnd =
        !!scrolling && scrolling.scrollHeight - offset - height <= 2;
      const next = noonReadingAtLine(
        targets.map(({ id, node }) => ({
          id,
          top: node.getBoundingClientRect().top,
        })),
        top + directoryHeight + 24,
        atEnd ? viewportBottom : undefined,
      );
      setActive(
        (previous) =>
          next || (targets.some((row) => row.id === previous) ? previous : ""),
      );
    };
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(update);
    };
    const pageScroll = (event: Event) => {
      // The brief, tables and chart drawers have their own scroll containers.
      if (isIntradayPageScroll(event.target, document, owner)) schedule();
    };
    const manualScroll = (event: Event) => {
      const target = event.target as Node | null;
      if (target && content.contains(target)) {
        const inside = (target as HTMLElement).closest?.(
          ".ibt-scroll, .intraday-table-scroll",
        );
        if (inside) return;
      }
      pinned.current = null;
      schedule();
    };
    const keyboardScroll = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const editable =
        !!target?.isContentEditable ||
        ["INPUT", "TEXTAREA", "SELECT"].includes(target?.tagName || "");
      if (noonManualScrollKey(event, editable)) manualScroll(event);
    };
    const scrollbarScroll = (event: PointerEvent) => {
      if (event.target === owner || event.target === document.documentElement)
        manualScroll(event);
    };
    document.addEventListener("scroll", pageScroll, true);
    document.addEventListener("wheel", manualScroll, {
      capture: true,
      passive: true,
    });
    document.addEventListener("touchmove", manualScroll, {
      capture: true,
      passive: true,
    });
    document.addEventListener("toggle", schedule, true);
    document.addEventListener("keydown", keyboardScroll, true);
    document.addEventListener("pointerdown", scrollbarScroll, true);
    window.addEventListener("resize", schedule);
    const mutation =
      typeof MutationObserver === "function"
        ? new MutationObserver(schedule)
        : null;
    mutation?.observe(content, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["open", "hidden"],
    });
    const resize =
      typeof ResizeObserver === "function"
        ? new ResizeObserver(schedule)
        : null;
    for (const node of [content, header, menu.current])
      if (node) resize?.observe(node);
    update();
    return () => {
      cancelAnimationFrame(frame);
      mutation?.disconnect();
      resize?.disconnect();
      document.removeEventListener("scroll", pageScroll, true);
      document.removeEventListener("wheel", manualScroll, true);
      document.removeEventListener("touchmove", manualScroll, true);
      document.removeEventListener("toggle", schedule, true);
      document.removeEventListener("keydown", keyboardScroll, true);
      document.removeEventListener("pointerdown", scrollbarScroll, true);
      window.removeEventListener("resize", schedule);
    };
    // API refreshes keep the same scope and must not reset the user's position.
  }, [scopeKey]);

  useEffect(() => {
    const nav = menu.current;
    const button = nav?.querySelector<HTMLElement>(
      `[aria-controls="${active}"]`,
    );
    if (!nav || !button || !window.matchMedia("(max-width: 1100px)").matches)
      return;
    const bounds = nav.getBoundingClientRect();
    const selected = button.getBoundingClientRect();
    if (selected.left < bounds.left)
      nav.scrollLeft -= bounds.left - selected.left + 6;
    else if (selected.right > bounds.right)
      nav.scrollLeft += selected.right - bounds.right + 6;
  }, [active, entries]);

  function navigate(id: string) {
    const boundary = root.current;
    const content = main.current;
    const target =
      content &&
      renderedIntradaySections(content).find((row) => row.id === id)?.node;
    if (
      !boundary ||
      !content ||
      !target ||
      !revealEmotionSection(target, content)
    )
      return;
    setActive(id);
    pinned.current = id;
    pinUntil.current = performance.now() + 1600;
    requestAnimationFrame(() => {
      if (!content.contains(target)) return;
      const owner = noonReadingScrollOwner(boundary);
      const viewportTop = owner?.getBoundingClientRect().top || 0;
      const header = boundary
        .closest(".app-main")
        ?.querySelector<HTMLElement>(".topbar");
      const compactHeight = window.matchMedia("(max-width: 1100px)").matches
        ? menu.current?.getBoundingClientRect().height || 0
        : 0;
      const activationTop =
        Math.max(viewportTop, header?.getBoundingClientRect().bottom || 0) +
        compactHeight +
        16;
      const behavior = window.matchMedia("(prefers-reduced-motion: reduce)")
        .matches
        ? "auto"
        : "smooth";
      target.setAttribute("tabindex", "-1");
      target.focus({ preventScroll: true });
      const top = intradayReadingScrollTop(
        target.getBoundingClientRect().top,
        owner?.scrollTop ?? window.scrollY,
        activationTop,
      );
      if (owner) owner.scrollTo({ top, left: owner.scrollLeft, behavior });
      else window.scrollTo({ top, left: window.scrollX, behavior });
    });
  }

  return (
    <div
      className={`intraday-page-reader${entries.length ? "" : " is-empty"}`}
      ref={root}
    >
      <PageScrollEdges
        label="盘中观察"
        sections={entries.map(({ id, key }) => ({
          id,
          label: intradayReadingSections[key].label,
        }))}
        activeId={active}
        onNavigate={navigate}
      />
      {entries.length > 0 && (
        <nav
          className="intraday-page-directory"
          aria-label="盘中观察页内目录"
          ref={menu}
        >
          <div className="intraday-directory-heading">
            <strong>本档目录</strong>
            {label && <small>{label}</small>}
          </div>
          <div className="intraday-directory-items">
            {entries.map(({ id, key }, index) => (
              <button
                type="button"
                key={id}
                aria-controls={id}
                aria-current={active === id ? "location" : undefined}
                title={intradayReadingSections[key].description}
                onClick={() => navigate(id)}
              >
                <span>{String(index + 1).padStart(2, "0")}</span>
                {intradayReadingSections[key].label}
              </button>
            ))}
          </div>
        </nav>
      )}
      <div className="intraday-page-reader-main" ref={main}>
        {children}
      </div>
    </div>
  );
}

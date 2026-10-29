import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { revealEmotionSection } from "./emotionNavigation";
import {
  noonReadingActivationLine,
  noonReadingAtLine,
  noonReadingScrollOwner,
} from "./noonReadingScroll";
import "./AuctionPageNavigation.css";

export type AuctionPageSection = {
  id: string;
  label: string;
  description?: string;
};

/** The supplied IDs belong to this page's children; navigation never changes routes or dates. */
export default function AuctionPageNavigation({
  sections,
  children,
}: {
  sections: AuctionPageSection[];
  children: ReactNode;
}) {
  const signature = JSON.stringify(sections);
  const entries = useMemo(() => {
    const seen = new Set<string>();
    return sections
      .filter(({ id, label }) => {
        if (!id || !label || seen.has(id)) return false;
        seen.add(id);
        return true;
      })
      .slice(0, 24);
  }, [signature]);
  const root = useRef<HTMLDivElement>(null);
  const menu = useRef<HTMLElement>(null);
  const pinned = useRef<string | null>(null);
  const pinUntil = useRef(0);
  const [active, setActive] = useState(entries[0]?.id || "");
  const [available, setAvailable] = useState(entries.map(({ id }) => id));

  useEffect(() => {
    const boundary = root.current;
    if (!boundary) return;
    let frame = 0;
    const owner = noonReadingScrollOwner(boundary);
    const main = boundary.closest(".app-main");
    const header = main?.querySelector<HTMLElement>(".topbar");
    const update = () => {
      frame = 0;
      const targets = entries
        .map(({ id }) => ({
          id,
          node: boundary.ownerDocument.getElementById(id),
        }))
        .filter(
          (item): item is { id: string; node: HTMLElement } =>
            !!item.node && boundary.contains(item.node),
        );
      const ids = targets.map(({ id }) => id);
      setAvailable((current) =>
        current.join("|") === ids.join("|") ? current : ids,
      );
      const viewportTop = owner?.getBoundingClientRect().top || 0;
      const viewportBottom =
        owner?.getBoundingClientRect().bottom || window.innerHeight;
      const headerBottom =
        header?.getBoundingClientRect().bottom || viewportTop;
      const headerOffset = Math.max(0, headerBottom - viewportTop);
      const compact = window.matchMedia("(max-width: 1100px)").matches;
      const guideHeight = compact
        ? menu.current?.getBoundingClientRect().height || 0
        : 0;
      boundary.style.setProperty(
        "--auction-directory-top",
        `${headerOffset + 12}px`,
      );
      boundary.style.setProperty(
        "--auction-directory-anchor-offset",
        `${headerOffset + guideHeight + 20}px`,
      );
      const anchors = targets
        .filter(({ node }) => node.getClientRects().length > 0)
        .map(({ id, node }) => ({ id, top: node.getBoundingClientRect().top }));
      const clicked =
        pinned.current && targets.find(({ id }) => id === pinned.current);
      if (clicked && performance.now() < pinUntil.current) return;
      if (clicked && clicked.node.getClientRects().length > 0) {
        const rect = clicked.node.getBoundingClientRect();
        if (rect.bottom > headerBottom && rect.top < viewportBottom) return;
      }
      pinned.current = null;
      const scroll = owner || boundary.ownerDocument.scrollingElement;
      const offset = owner ? owner.scrollTop : window.scrollY;
      const height = owner ? owner.clientHeight : window.innerHeight;
      const atEnd = !!scroll && scroll.scrollHeight - offset - height <= 2;
      const next = noonReadingAtLine(
        anchors,
        noonReadingActivationLine(viewportTop, headerBottom, guideHeight),
        atEnd ? viewportBottom : undefined,
      );
      if (next) setActive(next);
    };
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(update);
    };
    const unpin = () => {
      pinned.current = null;
      schedule();
    };
    const key = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (
        target?.isContentEditable ||
        ["INPUT", "TEXTAREA", "SELECT"].includes(target?.tagName || "")
      )
        return;
      if (
        [
          "ArrowUp",
          "ArrowDown",
          "PageUp",
          "PageDown",
          "Home",
          "End",
          " ",
        ].includes(event.key)
      )
        unpin();
    };
    const pointer = (event: PointerEvent) => {
      if (
        event.target === owner ||
        event.target === boundary.ownerDocument.documentElement
      )
        unpin();
    };
    // Capture observes document scrolling and an independently scrolling main container.
    boundary.ownerDocument.addEventListener("scroll", schedule, true);
    boundary.ownerDocument.addEventListener("toggle", schedule, true);
    boundary.ownerDocument.addEventListener("wheel", unpin, {
      capture: true,
      passive: true,
    });
    boundary.ownerDocument.addEventListener("touchmove", unpin, {
      capture: true,
      passive: true,
    });
    boundary.ownerDocument.addEventListener("keydown", key, true);
    boundary.ownerDocument.addEventListener("pointerdown", pointer, true);
    window.addEventListener("resize", schedule);
    const resize =
      typeof ResizeObserver === "function"
        ? new ResizeObserver(schedule)
        : null;
    for (const node of [boundary, menu.current, header])
      if (node) resize?.observe(node);
    update();
    return () => {
      cancelAnimationFrame(frame);
      resize?.disconnect();
      boundary.ownerDocument.removeEventListener("scroll", schedule, true);
      boundary.ownerDocument.removeEventListener("toggle", schedule, true);
      boundary.ownerDocument.removeEventListener("wheel", unpin, true);
      boundary.ownerDocument.removeEventListener("touchmove", unpin, true);
      boundary.ownerDocument.removeEventListener("keydown", key, true);
      boundary.ownerDocument.removeEventListener("pointerdown", pointer, true);
      window.removeEventListener("resize", schedule);
    };
  }, [signature, children]);

  function navigate(id: string) {
    const boundary = root.current;
    const target = boundary?.ownerDocument.getElementById(id);
    if (!boundary || !target || !revealEmotionSection(target, boundary)) return;
    setActive(id);
    pinned.current = id;
    pinUntil.current = performance.now() + 1600;
    requestAnimationFrame(() => {
      if (!boundary.contains(target)) return;
      target.setAttribute("tabindex", "-1");
      target.focus({ preventScroll: true });
      target.scrollIntoView({
        block: "start",
        behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
          ? "auto"
          : "smooth",
      });
    });
  }

  return (
    <div
      className={`auction-page-reader${entries.length ? "" : " auction-page-reader-empty"}`}
      ref={root}
    >
      {entries.length > 0 && (
        <nav
          className="auction-page-directory"
          aria-label="盘前竞价页内目录"
          ref={menu}
        >
          <strong>本页目录</strong>
          <div>
            {entries
              .filter(({ id }) => available.includes(id))
              .map(({ id, label, description }, index) => (
                <button
                  type="button"
                  key={id}
                  aria-controls={id}
                  aria-current={active === id ? "location" : undefined}
                  title={description}
                  onClick={() => navigate(id)}
                >
                  <span>{String(index + 1).padStart(2, "0")}</span>
                  {label}
                </button>
              ))}
          </div>
        </nav>
      )}
      <div className="auction-page-reader-main">{children}</div>
    </div>
  );
}

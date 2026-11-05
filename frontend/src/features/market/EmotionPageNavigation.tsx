import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import PageScrollEdges from "../../components/PageScrollEdges";
import { pageScrollOwner } from "../../components/pageScroll";
import {
  activeEmotionSection,
  emotionSections,
  keepEmotionSelection,
  revealEmotionSection,
} from "./emotionNavigation";
import type { EmotionScrollPosition } from "./emotionNavigation";
import "./EmotionPageNavigation.css";

export default function EmotionPageNavigation({
  date,
  outlook,
  statistics,
  feedback = false,
  brokenBoard = false,
  history,
  children,
}: {
  date: string;
  outlook: boolean;
  statistics: boolean;
  feedback?: boolean;
  brokenBoard?: boolean;
  history: boolean;
  children: ReactNode;
}) {
  const sections = useMemo(
    () =>
      emotionSections({ outlook, statistics, history, feedback, brokenBoard }),
    [outlook, statistics, history, feedback, brokenBoard],
  );
  const root = useRef<HTMLDivElement>(null);
  const selected = useRef<{
    id: string;
    anchor: EmotionScrollPosition | null;
    manualAnchor: EmotionScrollPosition | null;
  } | null>(null);
  const [active, setActive] = useState(sections[0]?.id || "");
  const [available, setAvailable] = useState(sections.map(({ id }) => id));

  useEffect(() => {
    selected.current = null;
    setActive(sections[0]?.id || "");
  }, [date, sections]);

  useEffect(() => {
    let frame = 0;
    let settling = 0;
    let gesture = 0;
    const scrollPosition = (boundary: HTMLElement): EmotionScrollPosition => {
      const scrolling = document.scrollingElement || document.documentElement;
      const positions = [
        {
          offset: window.scrollY,
          limit: Math.max(0, scrolling.scrollHeight - window.innerHeight),
        },
      ];
      let parent = boundary.parentElement;
      while (parent && parent !== document.body && parent !== scrolling) {
        positions.push({
          offset: parent.scrollTop,
          limit: Math.max(0, parent.scrollHeight - parent.clientHeight),
        });
        parent = parent.parentElement;
      }
      return positions;
    };
    const visible = (element: HTMLElement) => {
      const rect = element.getBoundingClientRect();
      return (
        element.getClientRects().length > 0 &&
        rect.bottom > 100 &&
        rect.top < window.innerHeight
      );
    };
    const finishScroll = () => {
      window.clearTimeout(settling);
      settling = 0;
      const clicked = selected.current;
      const boundary = root.current;
      if (!clicked || clicked.anchor || !boundary) return;
      const element = document.getElementById(clicked.id);
      if (element && boundary.contains(element) && visible(element)) {
        clicked.anchor = scrollPosition(boundary);
        setActive(clicked.id);
      } else {
        selected.current = null;
        schedule();
      }
    };
    const update = () => {
      frame = 0;
      const boundary = root.current;
      if (!boundary) return;
      const targets = sections
        .map((section) => ({
          ...section,
          element: document.getElementById(section.id),
        }))
        .filter(
          ({ element }) =>
            element &&
            boundary.contains(element) &&
            element.getClientRects().length > 0,
        );
      setAvailable((previous) => {
        const next = targets.map(({ id }) => id);
        return previous.join("|") === next.join("|") ? previous : next;
      });
      const clicked = selected.current;
      if (clicked) {
        const element = targets.find(({ id }) => id === clicked.id)?.element;
        if (element && clicked.anchor === null) {
          // This timer only detects the end of a smooth scroll. It never expires
          // an already anchored selection after a child control re-renders.
          window.clearTimeout(settling);
          settling = window.setTimeout(finishScroll, 140);
          return;
        }
        const position = scrollPosition(boundary);
        if (
          element &&
          clicked.anchor &&
          visible(element) &&
          (!clicked.manualAnchor ||
            keepEmotionSelection(clicked.manualAnchor, position, true, false))
        ) {
          // Child controls can change page height or scroll their own focus
          // into view. Keep the visible clicked section until a scroll gesture
          // actually moves it, or it leaves the viewport (including bar drags).
          clicked.anchor = position;
          return;
        }
        selected.current = null;
      }
      const id = activeEmotionSection(
        targets.map(({ id, element }) => ({
          id,
          top: element!.getBoundingClientRect().top,
          visible: element!.getClientRects().length > 0,
        })),
        window.innerWidth <= 1100 ? 154 : 108,
      );
      if (id) setActive(id);
    };
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(update);
    };
    const interruptScroll = () => {
      const clicked = selected.current;
      if (clicked?.anchor === null) {
        selected.current = null;
        schedule();
      } else if (clicked && root.current) {
        // A gesture at an already clamped edge does not count as movement.
        // Once it moves, do not mistake a new endpoint for layout reflow.
        clicked.manualAnchor ||= scrollPosition(root.current);
        window.clearTimeout(gesture);
        gesture = window.setTimeout(() => {
          if (selected.current === clicked) clicked.manualAnchor = null;
        }, 200);
        schedule();
      }
    };
    const interruptKey = (event: KeyboardEvent) => {
      const target = event.target;
      if (
        target instanceof HTMLElement &&
        (target.isContentEditable ||
          ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName))
      )
        return;
      if (
        [
          "PageUp",
          "PageDown",
          "Home",
          "End",
          "ArrowUp",
          "ArrowDown",
          " ",
        ].includes(event.key)
      )
        interruptScroll();
    };
    const pageScroll = (event: Event) => {
      const owner = root.current && pageScrollOwner(root.current);
      if (
        [document, document.documentElement, document.body, owner].includes(
          event.target as Document | HTMLElement | null,
        )
      )
        schedule();
    };
    // Briefs and stock tables must not move the page directory selection.
    document.addEventListener("scroll", pageScroll, true);
    document.addEventListener("toggle", schedule, true);
    document.addEventListener("scrollend", finishScroll, true);
    document.addEventListener("wheel", interruptScroll, {
      capture: true,
      passive: true,
    });
    document.addEventListener("touchmove", interruptScroll, {
      capture: true,
      passive: true,
    });
    document.addEventListener("keydown", interruptKey, true);
    window.addEventListener("resize", schedule);
    schedule();
    return () => {
      cancelAnimationFrame(frame);
      window.clearTimeout(settling);
      window.clearTimeout(gesture);
      document.removeEventListener("scroll", pageScroll, true);
      document.removeEventListener("toggle", schedule, true);
      document.removeEventListener("scrollend", finishScroll, true);
      document.removeEventListener("wheel", interruptScroll, true);
      document.removeEventListener("touchmove", interruptScroll, true);
      document.removeEventListener("keydown", interruptKey, true);
      window.removeEventListener("resize", schedule);
    };
  }, [date, sections, children]);

  function navigate(id: string) {
    const element = document.getElementById(id);
    const boundary = root.current;
    if (!element || !boundary || !revealEmotionSection(element, boundary))
      return;
    setActive(id);
    selected.current = { id, anchor: null, manualAnchor: null };
    requestAnimationFrame(() => {
      if (!boundary.contains(element)) return;
      element.setAttribute("tabindex", "-1");
      element.focus({ preventScroll: true });
      element.scrollIntoView({
        behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
          ? "auto"
          : "smooth",
        block: "start",
      });
    });
  }

  return (
    <div className="emotion-reader" ref={root}>
      <PageScrollEdges
        label="市场情绪"
        sections={sections.filter(({ id }) => available.includes(id))}
        activeId={active}
        onNavigate={navigate}
      />
      {sections.length > 0 && (
        <nav className="emotion-reader-nav" aria-label="市场情绪页内目录">
          <strong>本页目录</strong>
          <div>
            {sections
              .filter(({ id }) => available.includes(id))
              .map(({ id, label }, index) => (
                <button
                  type="button"
                  key={id}
                  aria-controls={id}
                  aria-current={active === id ? "location" : undefined}
                  onClick={() => navigate(id)}
                >
                  <span>{String(index + 1).padStart(2, "0")}</span>
                  {label}
                </button>
              ))}
          </div>
          <p>{date || "选择盘面日期"}</p>
        </nav>
      )}
      <div className="emotion-reader-main">{children}</div>
    </div>
  );
}

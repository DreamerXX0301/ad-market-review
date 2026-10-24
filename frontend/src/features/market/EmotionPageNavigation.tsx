import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  activeEmotionSection,
  emotionSections,
  revealEmotionSection,
} from "./emotionNavigation";
import "./EmotionPageNavigation.css";

export default function EmotionPageNavigation({
  date,
  outlook,
  statistics,
  history,
  children,
}: {
  date: string;
  outlook: boolean;
  statistics: boolean;
  history: boolean;
  children: ReactNode;
}) {
  const sections = useMemo(
    () => emotionSections({ outlook, statistics, history }),
    [outlook, statistics, history],
  );
  const root = useRef<HTMLDivElement>(null);
  const selected = useRef<{ id: string; until: number } | null>(null);
  const [active, setActive] = useState(sections[0]?.id || "");
  const [available, setAvailable] = useState(sections.map(({ id }) => id));

  useEffect(() => {
    selected.current = null;
    setActive(sections[0]?.id || "");
  }, [date, sections]);

  useEffect(() => {
    let frame = 0;
    const update = () => {
      frame = 0;
      const boundary = root.current;
      if (!boundary) return;
      const targets = sections
        .map((section) => ({
          ...section,
          element: document.getElementById(section.id),
        }))
        .filter(({ element }) => element && boundary.contains(element));
      setAvailable((previous) => {
        const next = targets.map(({ id }) => id);
        return previous.join("|") === next.join("|") ? previous : next;
      });
      if (selected.current && selected.current.until > performance.now())
        return;
      selected.current = null;
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
    // Capture nested scrolling containers as well as the document viewport.
    document.addEventListener("scroll", schedule, true);
    document.addEventListener("toggle", schedule, true);
    window.addEventListener("resize", schedule);
    schedule();
    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener("scroll", schedule, true);
      document.removeEventListener("toggle", schedule, true);
      window.removeEventListener("resize", schedule);
    };
  }, [date, sections, children]);

  function navigate(id: string) {
    const element = document.getElementById(id);
    const boundary = root.current;
    if (!element || !boundary || !revealEmotionSection(element, boundary))
      return;
    setActive(id);
    selected.current = { id, until: performance.now() + 900 };
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

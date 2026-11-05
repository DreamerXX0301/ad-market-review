import { ArrowDownToLine, ArrowUpToLine } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { pageScrollOwner, pageScrollState, scrollPageEdge } from "./pageScroll";
import type { PageEdge } from "./pageScroll";
import "./PageScrollEdges.css";

function pageHasModal(document: Document): boolean {
  return [
    ...document.querySelectorAll<HTMLElement>(
      'dialog[open], [role="dialog"][aria-modal="true"], .mobile-overlay.visible',
    ),
  ].some((node) => node.getClientRects().length > 0);
}

/** Place this marker inside a page; the controls follow its real scroll owner. */
export default function PageScrollEdges({
  label = "页面",
}: {
  label?: string;
}) {
  const marker = useRef<HTMLSpanElement>(null);
  const [document, setDocument] = useState<Document | null>(null);
  const [position, setPosition] = useState({
    visible: false,
    atTop: true,
    atBottom: false,
  });

  useEffect(() => {
    const node = marker.current;
    if (!node) return;
    const doc = node.ownerDocument;
    const view = doc.defaultView;
    if (!view) return;
    setDocument(doc);
    let frame = 0;
    const update = () => {
      frame = 0;
      const owner = pageScrollOwner(node);
      const documentOwner = doc.scrollingElement || doc.documentElement;
      const state = owner
        ? pageScrollState(
            owner.scrollTop,
            owner.scrollHeight,
            owner.clientHeight,
          )
        : pageScrollState(
            view.scrollY,
            documentOwner.scrollHeight,
            view.innerHeight,
          );
      const next = {
        visible: state.visible && !pageHasModal(doc),
        atTop: state.atTop,
        atBottom: state.atBottom,
      };
      setPosition((previous) =>
        previous.visible === next.visible &&
        previous.atTop === next.atTop &&
        previous.atBottom === next.atBottom
          ? previous
          : next,
      );
    };
    const schedule = () => {
      if (!frame) frame = view.requestAnimationFrame(update);
    };
    // Capture nested scroll events without taking over their wheel/keyboard input.
    doc.addEventListener("scroll", schedule, true);
    doc.addEventListener("toggle", schedule, true);
    view.addEventListener("resize", schedule);
    const resize =
      typeof ResizeObserver === "function"
        ? new ResizeObserver(schedule)
        : null;
    for (const ancestor of [
      node.parentElement,
      pageScrollOwner(node),
      doc.body,
    ])
      if (ancestor) resize?.observe(ancestor);
    const mutations = new MutationObserver(schedule);
    mutations.observe(doc.body, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["aria-modal", "hidden", "open", "style", "class"],
    });
    update();
    return () => {
      view.cancelAnimationFrame(frame);
      resize?.disconnect();
      mutations.disconnect();
      doc.removeEventListener("scroll", schedule, true);
      doc.removeEventListener("toggle", schedule, true);
      view.removeEventListener("resize", schedule);
    };
  }, []);

  function jump(edge: PageEdge) {
    const node = marker.current;
    if (node && !pageHasModal(node.ownerDocument)) scrollPageEdge(node, edge);
  }

  return (
    <>
      <span ref={marker} hidden />
      {document &&
        position.visible &&
        createPortal(
          <nav className="page-scroll-edges" aria-label={`${label}快速滚动`}>
            <button
              type="button"
              aria-label={`${label}回到顶部`}
              title="回到页面顶部"
              disabled={position.atTop}
              onClick={() => jump("top")}
            >
              <ArrowUpToLine size={18} aria-hidden="true" />
              <span>顶部</span>
            </button>
            <button
              type="button"
              aria-label={`${label}回到底部`}
              title="回到页面底部"
              disabled={position.atBottom}
              onClick={() => jump("bottom")}
            >
              <ArrowDownToLine size={18} aria-hidden="true" />
              <span>底部</span>
            </button>
          </nav>,
          document.body,
        )}
    </>
  );
}

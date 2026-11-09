import { ChevronDown, ChevronUp } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import FloatingScrollControls from "./FloatingScrollControls";
import { sectionNavigation } from "./sectionNavigation";
import type { ReadingSection } from "./sectionNavigation";

function pageHasModal(document: Document): boolean {
  return [
    ...document.querySelectorAll<HTMLElement>(
      'dialog[open], [role="dialog"][aria-modal="true"], .mobile-overlay.visible',
    ),
  ].some((node) => node.getClientRects().length > 0);
}

/** Controlled by the page directory, including its actual scroll-spy selection. */
export default function PageScrollEdges({
  label = "页面",
  sections,
  activeId,
  onNavigate,
  inline = false,
}: {
  label?: string;
  sections: ReadingSection[];
  activeId: string;
  onNavigate: (id: string) => void;
  inline?: boolean;
}) {
  const marker = useRef<HTMLSpanElement>(null);
  const [document, setDocument] = useState<Document | null>(null);
  const [visible, setVisible] = useState(false);
  const navigation = sectionNavigation(sections, activeId);

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
      setVisible(
        !!node.parentElement?.getClientRects().length && !pageHasModal(doc),
      );
    };
    const schedule = () => {
      if (!frame) frame = view.requestAnimationFrame(update);
    };
    // Visibility only; section position comes exclusively from the directory.
    doc.addEventListener("scroll", schedule, true);
    doc.addEventListener("toggle", schedule, true);
    view.addEventListener("resize", schedule);
    const resize =
      typeof ResizeObserver === "function"
        ? new ResizeObserver(schedule)
        : null;
    for (const ancestor of [node.parentElement, doc.body])
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

  function jump(id?: string) {
    const node = marker.current;
    if (id && node && !pageHasModal(node.ownerDocument)) onNavigate(id);
  }

  const controls = (visible || inline) && navigation.entries.length > 1 && (
    <FloatingScrollControls
      className={
        inline
          ? "page-scroll-edges is-inline" + (visible ? "" : " is-concealed")
          : "page-scroll-edges"
      }
      label={`${label}上下节快捷导航`}
      previous={{
        label: `${label}上一节`,
        caption: "上节",
        title: navigation.previous
          ? `上一节：${navigation.previous.label}`
          : "已到第一节",
        disabled: !navigation.previous,
        icon: <ChevronUp size={18} />,
        onClick: () => jump(navigation.previous?.id),
      }}
      next={{
        label: `${label}下一节`,
        caption: "下节",
        title: navigation.next
          ? `下一节：${navigation.next.label}`
          : "已到最后一节",
        disabled: !navigation.next,
        icon: <ChevronDown size={18} />,
        onClick: () => jump(navigation.next?.id),
      }}
      position={{
        text: `${navigation.index + 1}/${navigation.entries.length}`,
        label: `当前第 ${navigation.index + 1} 节，共 ${navigation.entries.length} 节：${navigation.current?.label || ""}`,
      }}
    />
  );
  return (
    <>
      <span ref={marker} hidden />
      {inline
        ? controls
        : document && controls && createPortal(controls, document.body)}
    </>
  );
}

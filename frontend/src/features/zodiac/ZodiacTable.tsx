import { useLayoutEffect, useRef } from "react";
import type { ReactNode, RefObject } from "react";

/** Keep the page scroll and the table's horizontal scroll independent. */
export default function ZodiacTable({
  active,
  scrollLeft,
  header,
  children,
}: {
  active: boolean;
  scrollLeft: RefObject<number>;
  header: (floating: boolean) => ReactNode;
  children: ReactNode;
}) {
  const scroll = useRef<HTMLDivElement>(null);
  const floating = useRef<HTMLDivElement>(null);
  const floatingScroll = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    const owner = scroll.current;
    const overlay = floating.current;
    const mirror = floatingScroll.current;
    const table = owner?.querySelector("table");
    const head = table?.tHead;
    const copy = mirror?.querySelector("table");
    if (!owner || !overlay || !mirror || !table || !head || !copy) return;
    overlay.hidden = true;
    if (!active) return;
    owner.scrollLeft = scrollLeft.current;
    const page = owner.closest(".zodiac-page");
    const topbar = owner.closest(".app-main")?.querySelector(".topbar");
    let frame = 0;
    const update = () => {
      frame = 0;
      const bounds = owner.getBoundingClientRect();
      const headerBounds = head.getBoundingClientRect();
      const top = Math.max(0, topbar?.getBoundingClientRect().bottom || 0);
      const bottom = table.getBoundingClientRect().bottom;
      if (!bounds.width || headerBounds.top >= top || bottom <= top) {
        overlay.hidden = true;
        return;
      }
      // Use measured column widths; auto layout would size a header-only table differently.
      const cells = Array.from(head.rows[0].cells);
      Array.from(copy.tHead!.rows[0].cells).forEach((cell, index) => {
        const width = `${cells[index].getBoundingClientRect().width}px`;
        cell.style.width = width;
        cell.style.minWidth = width;
        cell.style.maxWidth = width;
      });
      copy.style.width = `${table.getBoundingClientRect().width}px`;
      overlay.style.left = `${bounds.left + owner.clientLeft}px`;
      overlay.style.width = `${owner.clientWidth}px`;
      overlay.style.top = `${Math.min(top, bottom - headerBounds.height)}px`;
      overlay.hidden = false;
      mirror.scrollLeft = owner.scrollLeft;
    };
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(update);
    };
    const resize = new ResizeObserver(schedule);
    for (const node of [owner, table, head, page, topbar])
      if (node) resize.observe(node);
    document.addEventListener("scroll", schedule, true);
    window.addEventListener("resize", schedule);
    update();
    return () => {
      cancelAnimationFrame(frame);
      resize.disconnect();
      document.removeEventListener("scroll", schedule, true);
      window.removeEventListener("resize", schedule);
      overlay.hidden = true;
    };
  }, [active, header, children, scrollLeft]);

  return (
    <>
      <div
        className="zodiac-table-scroll"
        ref={scroll}
        onScroll={(event) => {
          scrollLeft.current = event.currentTarget.scrollLeft;
        }}
      >
        <table className="zodiac-table">
          <thead>{header(false)}</thead>
          <tbody>{children}</tbody>
        </table>
      </div>
      {/* The original table retains its semantic headers and keyboard controls. */}
      <div
        className="zodiac-floating-header"
        ref={floating}
        aria-hidden="true"
        hidden
        onMouseDown={(event) => {
          if ((event.target as HTMLElement).closest("button"))
            event.preventDefault();
        }}
      >
        <div
          className="zodiac-floating-header-scroll"
          ref={floatingScroll}
          onScroll={(event) => {
            if (scroll.current)
              scroll.current.scrollLeft = event.currentTarget.scrollLeft;
          }}
        >
          <table className="zodiac-table">
            <thead>{header(true)}</thead>
          </table>
        </div>
      </div>
    </>
  );
}

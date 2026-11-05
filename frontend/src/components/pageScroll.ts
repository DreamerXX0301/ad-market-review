export type PageEdge = "top" | "bottom";

/** The document viewport is null; only a genuinely scrolling ancestor owns a page. */
export function pageScrollOwner(marker: HTMLElement): HTMLElement | null {
  const document = marker.ownerDocument;
  for (let node = marker.parentElement; node; node = node.parentElement) {
    if (node === document.body || node === document.documentElement) break;
    const overflow = document.defaultView?.getComputedStyle(node).overflowY;
    if (
      overflow &&
      /^(auto|scroll|overlay)$/.test(overflow) &&
      node.scrollHeight > node.clientHeight + 1
    )
      return node;
  }
  return null;
}

export function pageScrollState(top: number, height: number, viewport: number) {
  const maximum = Math.max(0, height - viewport);
  return {
    visible:
      [top, height, viewport].every(Number.isFinite) &&
      viewport > 0 &&
      maximum > 120,
    atTop: top <= 2,
    atBottom: maximum - top <= 2,
    maximum,
  };
}

export function scrollPageEdge(marker: HTMLElement, edge: PageEdge): void {
  const document = marker.ownerDocument;
  const view = document.defaultView;
  if (!view) return;
  const owner = pageScrollOwner(marker);
  const documentOwner = document.scrollingElement || document.documentElement;
  const maximum = Math.max(
    0,
    owner
      ? owner.scrollHeight - owner.clientHeight
      : documentOwner.scrollHeight - view.innerHeight,
  );
  const behavior = view.matchMedia?.("(prefers-reduced-motion: reduce)").matches
    ? "auto"
    : "smooth";
  const top = edge === "top" ? 0 : maximum;
  if (owner) owner.scrollTo({ top, left: owner.scrollLeft, behavior });
  else view.scrollTo({ top, left: view.scrollX, behavior });
}

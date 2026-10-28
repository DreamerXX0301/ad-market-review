export type NoonReadingAnchor = { id: string; top: number };

/** Document scrolling is represented by null, rather than .app-main's moving rect. */
export function noonReadingScrollOwner(page: HTMLElement): HTMLElement | null {
  const document = page.ownerDocument;
  for (let node = page.parentElement; node; node = node.parentElement) {
    if (node === document.body || node === document.documentElement) break;
    const overflow = document.defaultView?.getComputedStyle(node).overflowY;
    if (
      overflow &&
      /^(auto|scroll|overlay)$/.test(overflow) &&
      node.scrollHeight > node.clientHeight
    )
      return node;
  }
  return null;
}

export function noonReadingActivationLine(
  viewportTop: number,
  headerBottom: number,
  guideHeight: number,
): number {
  return (
    Math.max(
      0,
      Number.isFinite(viewportTop) ? viewportTop : 0,
      Number.isFinite(headerBottom) ? headerBottom : 0,
    ) +
    Math.max(0, Number.isFinite(guideHeight) ? guideHeight : 0) +
    28
  );
}

/** At most the six reading anchors are considered; stock rows never participate. */
export function noonReadingAtLine(
  anchors: NoonReadingAnchor[],
  line: number,
  viewportBottom?: number,
): string | null {
  const present = anchors.filter((anchor) => Number.isFinite(anchor.top));
  if (!present.length) return null;
  if (viewportBottom != null && Number.isFinite(viewportBottom))
    return (
      present.filter((anchor) => anchor.top < viewportBottom - 24).at(-1)?.id ||
      present[0].id
    );
  return (
    present.filter((anchor) => anchor.top <= line).at(-1)?.id || present[0].id
  );
}

export function noonManualScrollKey(
  event: {
    key: string;
    altKey?: boolean;
    ctrlKey?: boolean;
    metaKey?: boolean;
  },
  editable = false,
): boolean {
  return (
    !editable &&
    !event.altKey &&
    !event.ctrlKey &&
    !event.metaKey &&
    [
      "ArrowUp",
      "ArrowDown",
      "PageUp",
      "PageDown",
      "Home",
      "End",
      " ",
      "Spacebar",
      "Tab",
    ].includes(event.key)
  );
}

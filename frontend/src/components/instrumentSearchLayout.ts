export type SearchAnchor = {
  left: number;
  top: number;
  bottom: number;
  width: number;
};

/** Fixed viewport coordinates keep the list outside the panel's clipping box. */
export function instrumentSearchLayout(
  anchor: SearchAnchor,
  viewport: { width: number; height: number },
) {
  const margin = 8;
  const gap = 6;
  if (
    !Object.values(anchor).every(Number.isFinite) ||
    !Number.isFinite(viewport.width) ||
    !Number.isFinite(viewport.height) ||
    viewport.width <= margin * 2 ||
    viewport.height <= margin * 2 ||
    anchor.bottom <= 0 ||
    anchor.top >= viewport.height ||
    anchor.width <= 0
  )
    return null;
  const width = Math.min(
    Math.max(270, anchor.width),
    viewport.width - margin * 2,
  );
  const left = Math.max(
    margin,
    Math.min(anchor.left, viewport.width - width - margin),
  );
  const below = Math.max(0, viewport.height - anchor.bottom - gap - margin);
  const above = Math.max(0, anchor.top - gap - margin);
  const openAbove = below < 240 && above > below;
  return {
    left,
    width,
    maxHeight: Math.min(420, openAbove ? above : below),
    ...(openAbove
      ? { bottom: viewport.height - anchor.top + gap }
      : { top: anchor.bottom + gap }),
  };
}

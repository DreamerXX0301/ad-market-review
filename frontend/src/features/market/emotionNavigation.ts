export type EmotionSection = { id: string; label: string };
export type EmotionScrollPosition = Array<{ offset: number; limit: number }>;

/** A clicked, visible section stays selected at its actual (possibly clamped) destination. */
export function keepEmotionSelection(
  anchor: EmotionScrollPosition,
  current: EmotionScrollPosition,
  visible: boolean,
  allowClampedReflow = true,
) {
  const valid = ({ offset, limit }: { offset: number; limit: number }) =>
    Number.isFinite(offset) && Number.isFinite(limit) && limit >= 0;
  if (!visible || !anchor.length || anchor.length !== current.length)
    return false;
  return anchor.every((previous, index) => {
    const next = current[index];
    return (
      valid(previous) &&
      valid(next) &&
      (Math.abs(previous.offset - next.offset) <= 2 ||
        (allowClampedReflow &&
          Math.abs(previous.offset - previous.limit) <= 2 &&
          Math.abs(next.offset - next.limit) <= 2))
    );
  });
}

/** These are page anchors, never routes or a second market-date scope. */
export function emotionSections(options: {
  outlook: boolean;
  statistics: boolean;
  feedback?: boolean;
  history: boolean;
}): EmotionSection[] {
  return [
    ...(options.outlook ? [{ id: "emotion-reading", label: "市场读数" }] : []),
    ...(options.statistics
      ? [{ id: "emotion-indices", label: "指数环境" }]
      : []),
    ...(options.outlook
      ? [
          { id: "emotion-plan", label: "执行计划" },
          { id: "emotion-entry", label: "开仓与量能" },
          { id: "emotion-watchlist", label: "核心方向" },
        ]
      : []),
    ...(options.statistics
      ? [{ id: "emotion-short-term", label: "短线接力" }]
      : []),
    ...(options.feedback
      ? [{ id: "emotion-ladder-feedback", label: "连板反馈" }]
      : []),
    ...(options.history ? [{ id: "emotion-history", label: "广度历史" }] : []),
    ...(options.statistics
      ? [{ id: "emotion-sources", label: "来源与留存" }]
      : []),
  ];
}

/** Open every containing disclosure before measuring or scrolling its target. */
export function revealEmotionSection(
  target: HTMLElement,
  boundary: HTMLElement,
) {
  if (!boundary.contains(target)) return false;
  const disclosures: HTMLDetailsElement[] = [];
  let element: HTMLElement | null = target;
  while (element && element !== boundary) {
    if (element.tagName === "DETAILS")
      disclosures.push(element as HTMLDetailsElement);
    element = element.parentElement;
  }
  for (const disclosure of disclosures.reverse()) disclosure.open = true;
  return true;
}

/** Hidden sections never win scroll-spy simply because their empty rect is 0. */
export function activeEmotionSection(
  sections: Array<{ id: string; top: number; visible: boolean }>,
  threshold: number,
) {
  const visible = sections
    .filter((section) => section.visible && Number.isFinite(section.top))
    .sort((left, right) => left.top - right.top);
  return (
    visible.filter((section) => section.top <= threshold).at(-1)?.id ||
    visible[0]?.id ||
    null
  );
}

export type EmotionSection = { id: string; label: string };

/** These are page anchors, never routes or a second market-date scope. */
export function emotionSections(options: {
  outlook: boolean;
  statistics: boolean;
  history: boolean;
}): EmotionSection[] {
  return [
    ...(options.outlook
      ? [
          { id: "emotion-reading", label: "市场读数" },
          { id: "emotion-plan", label: "执行计划" },
          { id: "emotion-entry", label: "开仓与量能" },
          { id: "emotion-watchlist", label: "核心方向" },
        ]
      : []),
    ...(options.statistics
      ? [
          { id: "emotion-indices", label: "指数环境" },
          { id: "emotion-short-term", label: "短线接力" },
        ]
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

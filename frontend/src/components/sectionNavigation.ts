export type ReadingSection = { id: string; label: string };

/** The directory owns the active section; floating controls only step its list. */
export function sectionNavigation(
  sections: ReadingSection[],
  activeId: string,
) {
  const seen = new Set<string>();
  const entries = sections.filter(({ id, label }) => {
    if (!id || !label || seen.has(id)) return false;
    seen.add(id);
    return true;
  });
  const index = Math.max(
    0,
    entries.findIndex(({ id }) => id === activeId),
  );
  return {
    entries,
    index,
    previous: entries[index - 1] || null,
    next: entries[index + 1] || null,
    current: entries[index] || null,
  };
}

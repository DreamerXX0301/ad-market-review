import type { ReactNode } from "react";
import "./FloatingScrollControls.css";

export type FloatingScrollAction = {
  label: string;
  caption: string;
  icon: ReactNode;
  onClick: () => void;
  disabled?: boolean;
  title?: string;
  shortcut?: string;
};

/** Shared presentation only: each reader keeps its own page/section semantics. */
export default function FloatingScrollControls({
  label,
  previous,
  next,
  position,
  className = "",
}: {
  label: string;
  previous: FloatingScrollAction;
  next: FloatingScrollAction;
  position?: { text: string; label: string };
  className?: string;
}) {
  const action = (value: FloatingScrollAction) => (
    <button
      type="button"
      aria-label={value.label}
      title={value.title || value.label}
      aria-keyshortcuts={value.shortcut}
      disabled={value.disabled}
      onClick={value.onClick}
    >
      <span className="floating-scroll-icon" aria-hidden="true">
        {value.icon}
      </span>
      <span className="floating-scroll-caption">{value.caption}</span>
    </button>
  );
  return (
    <nav
      className={`floating-scroll-controls ${className}`}
      aria-label={label}
      data-position={position ? "sections" : "edges"}
    >
      {action(previous)}
      {position && (
        <span className="floating-scroll-position" aria-label={position.label}>
          {position.text}
        </span>
      )}
      {action(next)}
    </nav>
  );
}

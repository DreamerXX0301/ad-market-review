import { ArrowRight, BookOpen, Route } from "lucide-react";
import {
  NOON_READING_SECTIONS,
  noonReadingSection,
} from "./noonReadGuidePresentation";
import "./NoonReadGuide.css";

export default function NoonReadGuide({
  activeSection,
  onNavigate,
}: {
  activeSection: string;
  onNavigate: (id: string) => void;
}) {
  const active = noonReadingSection(activeSection) || NOON_READING_SECTIONS[0];
  return (
    <nav className="noon-read-guide" aria-label="午评阅读导航">
      <div className="noon-read-guide-heading">
        <Route size={14} aria-hidden="true" />
        <span>午评怎么读</span>
      </div>
      <p className="noon-read-guide-intro">先看整体，再看接力和方向。</p>
      <ol className="noon-read-guide-list">
        {NOON_READING_SECTIONS.map((section) => {
          const selected = active.id === section.id;
          return (
            <li
              key={section.id}
              className={section.step ? undefined : "noon-read-guide-reference"}
            >
              <button
                type="button"
                aria-controls={section.id}
                aria-current={selected ? "location" : undefined}
                title={`${section.question} ${section.detail}`}
                onClick={() => onNavigate(section.id)}
              >
                <span className="noon-read-guide-number" aria-hidden="true">
                  {section.step || <BookOpen size={13} />}
                </span>
                <span>{section.title}</span>
                {selected && (
                  <ArrowRight
                    className="noon-read-guide-arrow"
                    size={12}
                    aria-hidden="true"
                  />
                )}
              </button>
            </li>
          );
        })}
      </ol>
      <div className="noon-read-guide-current" aria-live="polite">
        <span>这一项看什么</span>
        <p>{active.question}</p>
        <small>{active.detail}</small>
      </div>
      <p className="noon-read-guide-footnote">
        目录只定位已保存内容，
        <br />
        不会自动采集行情。
      </p>
    </nav>
  );
}

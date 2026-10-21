import { AlertCircle, ChevronDown, Info } from "lucide-react";
import { dataNotesModel } from "./dataNotePresentation";
import "./DataNotes.css";

export default function DataNotes({ items }: { items: unknown }) {
  const { notes, issues } = dataNotesModel(items);
  if (!notes.length && !issues.length) return null;
  return (
    <div className="data-notes">
      {issues.map((item) => (
        <div className="data-notes-blocking" key={item.key} role="alert">
          <AlertCircle size={16} aria-hidden="true" />
          <p>{item.message}</p>
        </div>
      ))}
      {!!notes.length && (
        <details className="data-notes-fold">
          <summary>
            <Info size={15} aria-hidden="true" />
            <span className="data-notes-title">
              数据说明 · {notes.length}项
            </span>
            <span className="data-notes-hint">
              来源范围、计算口径与已知缺口
            </span>
            <ChevronDown
              className="data-notes-chevron"
              size={15}
              aria-hidden="true"
            />
          </summary>
          <ul>
            {notes.map((item) => (
              <li key={item.key}>{item.message}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

import { boards } from "../strategies/Universe";

export default function TopicBoardFilter({
  value,
  onChange,
}: {
  value: string[];
  onChange: (boards: string[]) => void;
}) {
  return (
    <div
      className="topic-board-filter"
      role="group"
      aria-label="题材成员上市板块"
    >
      <span>上市板块</span>
      <button
        className={"btn small " + (!value.length ? "active" : "")}
        aria-pressed={!value.length}
        onClick={() => onChange([])}
      >
        不限
      </button>
      {Object.entries(boards).map(([board, label]) => (
        <button
          key={board}
          className={"btn small " + (value.includes(board) ? "active" : "")}
          aria-pressed={value.includes(board)}
          onClick={() =>
            onChange(
              Object.keys(boards).filter((key) =>
                key === board ? !value.includes(key) : value.includes(key),
              ),
            )
          }
        >
          {label}
        </button>
      ))}
      <small>可多选 · 当前名录分类，未知身份仅在“不限”时展示</small>
    </div>
  );
}

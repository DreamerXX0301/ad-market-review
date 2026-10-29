import {
  ArrowDown,
  ArrowUp,
  ListOrdered,
  Plus,
  RotateCcw,
  X,
} from "lucide-react";
import type { TableSort } from "./tableSort";
import { moveSortLevel } from "./multiTableSort";
import "./MultiSortControls.css";

export default function MultiSortControls({
  levels,
  choices,
  onChange,
  onReset,
}: {
  levels: TableSort[];
  choices: { key: string; label: string }[];
  onChange: (levels: TableSort[]) => void;
  onReset: () => void;
}) {
  const unused = choices.filter(
    (choice) => !levels.some((level) => level.key === choice.key),
  );
  const change = (index: number, value: TableSort) =>
    onChange(
      levels.map((level, position) => (position === index ? value : level)),
    );
  return (
    <section className="table-multi-sort" aria-label="多条件排序">
      <div className="table-multi-sort-heading">
        <strong>
          <ListOrdered size={15} />
          多条件排序
        </strong>
        <span>先比较第1项，相同时再比较下一项</span>
        <button className="btn small" onClick={onReset}>
          <RotateCcw size={13} />
          重置排序
        </button>
      </div>
      <ol className="table-multi-sort-levels">
        {levels.map((level, index) => {
          const caption =
            choices.find((choice) => choice.key === level.key)?.label ||
            level.key;
          return (
            <li key={index}>
              <span
                className="table-sort-priority"
                aria-label={`优先级${index + 1}`}
              >
                {index + 1}
              </span>
              <select
                aria-label={`第${index + 1}排序字段`}
                value={level.key}
                onChange={(event) =>
                  change(index, { ...level, key: event.target.value })
                }
              >
                {choices.map((choice) => (
                  <option
                    key={choice.key}
                    value={choice.key}
                    disabled={
                      choice.key !== level.key &&
                      levels.some((other) => other.key === choice.key)
                    }
                  >
                    {choice.label}
                  </option>
                ))}
              </select>
              <button
                className="btn small"
                aria-label={`${caption}切换为${level.order === "desc" ? "升序" : "降序"}`}
                onClick={() =>
                  change(index, {
                    ...level,
                    order: level.order === "desc" ? "asc" : "desc",
                  })
                }
              >
                {level.order === "desc" ? (
                  <ArrowDown size={13} />
                ) : (
                  <ArrowUp size={13} />
                )}
                {level.order === "desc" ? "降序" : "升序"}
              </button>
              <button
                className="icon-btn"
                disabled={index === 0}
                title={`提高${caption}优先级`}
                aria-label={`提高${caption}优先级`}
                onClick={() => onChange(moveSortLevel(levels, index, -1))}
              >
                <ArrowUp size={13} />
              </button>
              <button
                className="icon-btn"
                disabled={index === levels.length - 1}
                title={`降低${caption}优先级`}
                aria-label={`降低${caption}优先级`}
                onClick={() => onChange(moveSortLevel(levels, index, 1))}
              >
                <ArrowDown size={13} />
              </button>
              <button
                className="icon-btn"
                title={`删除${caption}排序`}
                aria-label={`删除${caption}排序`}
                onClick={() =>
                  onChange(levels.filter((_, position) => position !== index))
                }
              >
                <X size={13} />
              </button>
            </li>
          );
        })}
        <li className="table-multi-sort-add">
          <button
            className="btn small"
            disabled={!unused.length}
            onClick={() =>
              unused[0] &&
              onChange([...levels, { key: unused[0].key, order: "desc" }])
            }
          >
            <Plus size={13} />
            添加排序
          </button>
        </li>
      </ol>
      <p>
        {levels.length
          ? "当前筛选的全部已加载记录先排序再分页；未知值在对应排序级排末。"
          : "当前保持原顺序，可添加排序条件。"}{" "}
        点击表头可切换为单项排序。
      </p>
    </section>
  );
}

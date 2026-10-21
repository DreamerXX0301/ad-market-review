import type { Row } from "../../lib/api";

export const boards = {
  main: "主板",
  chinext: "创业板",
  star: "科创板",
  beijing: "北交所",
};
export type Universe = {
  mode: "all" | "in" | "not_in";
  boards: string[];
  st: "all" | "exclude" | "only";
};
const empty: Universe = { mode: "all", boards: [], st: "all" };

export function splitUniverse(definition?: Row): {
  universe: Universe;
  definition: Row;
} {
  if (!definition) return { universe: { ...empty }, definition: {} };
  const nodes = definition.all || (definition.field ? [definition] : []);
  const board = nodes.find(
    (n: Row) =>
      n.field === "board" &&
      ["in", "not_in"].includes(n.op) &&
      Array.isArray(n.value),
  );
  const st = nodes.find(
    (n: Row) =>
      n.field === "is_st" && n.op === "==" && typeof n.value === "boolean",
  );
  if (!board && !st) return { universe: { ...empty }, definition };
  const rest = nodes.filter((n: Row) => n !== board && n !== st);
  const remaining =
    rest.length === 1 ? rest[0] : rest.length ? { all: rest } : {};
  return {
    universe: {
      mode: board?.op || "all",
      boards: board?.value || [],
      st: st ? (st.value ? "only" : "exclude") : "all",
    },
    definition: remaining,
  };
}

export function withUniverse(definition: Row, universe: Universe): Row {
  const filters: Row[] = [];
  if (universe.mode !== "all") {
    if (!universe.boards.length)
      throw new Error("请至少选择一个上市板块，或将股票范围设为全部板块");
    filters.push({ field: "board", op: universe.mode, value: universe.boards });
  }
  if (universe.st !== "all")
    filters.push({ field: "is_st", op: "==", value: universe.st === "only" });
  if (!filters.length) return definition;
  const hasRules =
    !!definition.field ||
    !!definition.history_event ||
    !!definition.all?.length ||
    !!definition.any?.length ||
    !!definition.conditions?.length;
  return { all: [...filters, ...(hasRules ? [definition] : [])] };
}

export default function UniverseEditor({
  value,
  onChange,
}: {
  value: Universe;
  onChange: (value: Universe) => void;
}) {
  return (
    <>
      <div className="form-section-title">
        <h3>股票范围</h3>
        <small>范围筛选与下方条件始终同时满足</small>
      </div>
      <div className="form-grid">
        <label>
          上市板块
          <select
            value={value.mode}
            onChange={(e) =>
              onChange({ ...value, mode: e.target.value as Universe["mode"] })
            }
          >
            <option value="all">全部板块</option>
            <option value="in">仅包含所选板块</option>
            <option value="not_in">剔除所选板块</option>
          </select>
        </label>
        <label>
          ST 股票
          <select
            value={value.st}
            onChange={(e) =>
              onChange({ ...value, st: e.target.value as Universe["st"] })
            }
          >
            <option value="all">不限制</option>
            <option value="exclude">剔除 ST / *ST</option>
            <option value="only">仅 ST / *ST</option>
          </select>
        </label>
      </div>
      {value.mode !== "all" && (
        <div className="button-group" role="group" aria-label="上市板块范围">
          {Object.entries(boards).map(([code, label]) => (
            <label className="checkbox-label" key={code}>
              <input
                type="checkbox"
                checked={value.boards.includes(code)}
                onChange={(e) =>
                  onChange({
                    ...value,
                    boards: e.target.checked
                      ? [...value.boards, code]
                      : value.boards.filter((v) => v !== code),
                  })
                }
              />
              {label}
            </label>
          ))}
        </div>
      )}
      <p className="form-help">
        ST 按当前名称识别，名称缺失按待核验处理；不能代表历史各日的 ST
        状态。历史回测将单独披露这一限制。
      </p>
    </>
  );
}

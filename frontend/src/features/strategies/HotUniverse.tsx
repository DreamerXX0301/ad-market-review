import type { Row } from "../../lib/api";
import "./HotUniverse.css";

export type HotUniverseValue = {
  window_sessions: number;
  top_n: number;
  kind: "all" | "concept" | "industry";
};
export const defaultHotUniverse: HotUniverseValue = {
  window_sessions: 7,
  top_n: 10,
  kind: "all",
};
export function hotUniverseSummary(value: HotUniverseValue) {
  const kind =
    value.kind === "industry"
      ? "行业"
      : value.kind === "concept"
        ? "题材"
        : "题材 / 行业";
  return `近 ${value.window_sessions} 个交易日 · 热点${kind}前 ${value.top_n} 名`;
}
export function validateHotUniverse(value: HotUniverseValue | null) {
  if (!value) return "";
  if (
    !Number.isInteger(value.window_sessions) ||
    value.window_sessions < 1 ||
    value.window_sessions > 60
  )
    return "热点窗口请输入 1 至 60 个交易日";
  if (!Number.isInteger(value.top_n) || value.top_n < 1 || value.top_n > 50)
    return "热点排名请输入前 1 至 50 名";
  return ["all", "concept", "industry"].includes(value.kind)
    ? ""
    : "请选择热点分类类型";
}
export default function HotUniverseEditor({
  value,
  onChange,
}: {
  value: HotUniverseValue | null;
  onChange: (value: HotUniverseValue | null) => void;
}) {
  return (
    <section className="hot-universe-editor" aria-label="近期热点题材 / 行业">
      <label className="checkbox-label">
        <input
          type="checkbox"
          checked={value !== null}
          onChange={(event) =>
            onChange(event.target.checked ? { ...defaultHotUniverse } : null)
          }
        />
        <strong>近期热点题材 / 行业</strong>
        <small>
          {value ? "已启用 · 与技术条件同时满足" : "未启用 · 不限制题材行业"}
        </small>
      </label>
      {value && (
        <>
          <div className="hot-universe-fields">
            <label>
              最近交易日
              <input
                aria-label="热点最近交易日"
                type="number"
                min={1}
                max={60}
                value={value.window_sessions}
                onChange={(event) =>
                  onChange({
                    ...value,
                    window_sessions: Number(event.target.value),
                  })
                }
              />
            </label>
            <label>
              排名前 N 名
              <input
                aria-label="热点排名前N名"
                type="number"
                min={1}
                max={50}
                value={value.top_n}
                onChange={(event) =>
                  onChange({ ...value, top_n: Number(event.target.value) })
                }
              />
            </label>
            <label>
              分类范围
              <select
                aria-label="热点分类范围"
                value={value.kind}
                onChange={(event) =>
                  onChange({
                    ...value,
                    kind: event.target.value as HotUniverseValue["kind"],
                  })
                }
              >
                <option value="all">题材与行业</option>
                <option value="concept">题材（含人工题材）</option>
                <option value="industry">行业</option>
              </select>
            </label>
          </div>
          <p className="form-help">
            按已核验收盘涨停股票日数排名，同股同日去重；股票属于其中任一分类即可。使用当前成员代理，覆盖不足显示待核验。每次运行保存排名与成员依据；暂不支持历史回测。
          </p>
        </>
      )}
    </section>
  );
}
export function HotUniverseReceipt({ value }: { value?: Row }) {
  if (!value) return null;
  return (
    <details className="hot-universe-receipt">
      <summary>
        本次热点范围 · {hotUniverseSummary(value.config)} ·{" "}
        {value.complete ? "已核验" : "覆盖待核验"}
      </summary>
      <p>
        {value.window?.start || "—"} → {value.business_date || "—"} ·
        涨停来源完整日 {value.coverage?.source_complete_days ?? "—"}/
        {value.window?.expected_trade_days ?? "—"} · 当前成员代理
      </p>
      {!value.complete && (
        <p role="status">
          覆盖待核验不等于运行失败。排名仅展示已确认事件下界，本次热点条件保持待核验；技术条件不满足的股票仍会排除。
          {typeof value.coverage?.unverified_candidate_stock_days ===
            "number" && (
            <>
              {" "}
              候选股票日待核验：
              {value.coverage.unverified_candidate_stock_days} 个。
            </>
          )}
          {!!value.coverage?.unverified_candidate_stock_day_reasons
            ?.missing_raw_close && (
            <>
              原始收盘缺失：
              {
                value.coverage.unverified_candidate_stock_day_reasons
                  .missing_raw_close
              }
              个。
            </>
          )}
          {!!value.coverage?.unverified_candidate_stock_day_reasons
            ?.source_conflict && (
            <>
              来源证据冲突：
              {
                value.coverage.unverified_candidate_stock_day_reasons
                  .source_conflict
              }
              个。
            </>
          )}
        </p>
      )}
      <div className="hot-universe-tags">
        {(value.items || []).map((topic: Row) => (
          <span key={topic.topic_id}>
            {topic.rank}. {topic.name} · {topic.event_count} 股票日
          </span>
        ))}
      </div>
      {!value.items?.length && <p>暂无已核验热点排名。</p>}
      {value.reason && <p>{value.reason}</p>}
      <small>
        这是本次保存的排名，不随之后的行情或题材修改变化。成交额、资金净流入不参与此排名。
      </small>
    </details>
  );
}

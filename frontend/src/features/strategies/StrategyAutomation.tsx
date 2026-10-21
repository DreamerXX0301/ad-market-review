import { useEffect, useState } from "react";
import { Check, Workflow } from "lucide-react";
import { api, useApi } from "../../lib/api";
import type { Row } from "../../lib/api";
import {
  Badge,
  ErrorBox,
  Loading,
  Panel,
  Spinner,
  useToast,
} from "../../components/UI";
/** These are local post-sync calculations. Creating a new network sync remains manual. */
export default function StrategyAutomation({
  strategyId,
}: {
  strategyId: string;
}) {
  const result = useApi<Row>(
    strategyId ? `/strategies/${strategyId}/automation` : null,
  );
  const [draft, setDraft] = useState({
    refresh_after_sync: false,
    auto_track: false,
  });
  const [saving, setSaving] = useState(false),
    [error, setError] = useState("");
  const toast = useToast();
  useEffect(() => {
    if (result.data)
      setDraft({
        refresh_after_sync: result.data.refresh_after_sync === true,
        auto_track: result.data.auto_track === true,
      });
  }, [result.data]);
  const changed =
    result.data &&
    (draft.refresh_after_sync !== result.data.refresh_after_sync ||
      draft.auto_track !== result.data.auto_track);
  async function save() {
    setSaving(true);
    setError("");
    try {
      await api(`/strategies/${strategyId}/automation`, {
        method: "PUT",
        body: JSON.stringify(draft),
      });
      result.reload();
      toast("同步后执行设置已保存，取数仍由手动启动");
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  }
  return (
    <Panel
      title={
        <>
          <Workflow size={15} />
          同步后执行
        </>
      }
      subtitle="手动盘后增量成功后，执行此策略的本地计算"
      actions={
        <Badge tone={result.data?.refresh_after_sync ? "good" : "neutral"}>
          {result.data?.refresh_after_sync ? "已启用" : "默认关闭"}
        </Badge>
      }
    >
      {result.error ? (
        <ErrorBox message={result.error} retry={result.reload} />
      ) : result.loading && !result.data ? (
        <Loading />
      ) : (
        <div className="automation-settings">
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={draft.refresh_after_sync}
              disabled={!result.data || saving}
              onChange={(e) =>
                setDraft((d) => ({
                  refresh_after_sync: e.target.checked,
                  auto_track: e.target.checked ? d.auto_track : false,
                }))
              }
            />
            <span>
              <strong>同步完成后重新筛选</strong>
              <small>使用新发布行情，保存本策略的当日运行结果。</small>
            </span>
          </label>
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={draft.auto_track}
              disabled={!draft.refresh_after_sync || !result.data || saving}
              onChange={(e) =>
                setDraft((d) => ({ ...d, auto_track: e.target.checked }))
              }
            />
            <span>
              <strong>命中新股自动加入此策略跟踪池</strong>
              <small>
                已跟踪和手动忽略的股票不重复入池，原轮次基准不会重置。
              </small>
            </span>
          </label>
          <div className="automation-footer">
            <span>
              首次初始化与每日增量仍由你点击开始；已有跟踪轮次在同步后核对。
            </span>
            <button
              className="btn small"
              disabled={!changed || saving}
              onClick={save}
            >
              {saving ? <Spinner /> : <Check size={13} />}保存设置
            </button>
          </div>
        </div>
      )}
      {error && <ErrorBox message={error} />}
    </Panel>
  );
}

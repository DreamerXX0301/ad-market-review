import { useState } from "react";
import { Plus, RefreshCw } from "lucide-react";
import { api, list, patch, post, useApi } from "../../lib/api";
import type { Row } from "../../lib/api";
import { dateTime } from "../../lib/format";
import {
  Badge,
  ErrorBox,
  Modal,
  Spinner,
  Table,
  useToast,
  Warning,
} from "../../components/UI";
import TopicDirectory from "./TopicDirectory";
import TopicBrowser from "./TopicBrowser";
import TopicEditor from "./TopicEditor";
import TopicMaintenance from "./TopicMaintenance";
import "./Topics.css";

export default function Topics({
  onStock,
  onNavigate,
}: {
  onStock: (id: string) => void;
  onNavigate?: (route: string, context?: Row) => void;
}) {
  const [query, setQuery] = useState(""),
    [kind, setKind] = useState("all"),
    [includeArchived, setIncludeArchived] = useState(false),
    [selectedIds, setSelectedIds] = useState<string[]>([]),
    [editor, setEditor] = useState<Row | null | undefined>(),
    [busy, setBusy] = useState(""),
    [error, setError] = useState(""),
    [history, setHistory] = useState<Row[] | null>(null),
    [refreshVersion, setRefreshVersion] = useState(0);
  // Keep the complete stable-ID directory: visual filtering must never erase a
  // selection that was made under another search or classification type.
  const result = useApi<Row>("/topics?include_archived=true");
  const bootstrap = useApi<Row>("/bootstrap?scope=navigation");
  const catalog = list(result.data);
  const singleId = selectedIds.length === 1 ? selectedIds[0] : "";
  const detail = useApi<Row>(singleId ? `/topics/${singleId}` : null);
  const toast = useToast();
  function reload() {
    result.reload();
    detail.reload();
    bootstrap.reload();
    setRefreshVersion((value) => value + 1);
  }
  function toggle(id: string) {
    if (selectedIds.includes(id))
      setSelectedIds(selectedIds.filter((value) => value !== id));
    else if (selectedIds.length >= 20)
      toast("联合查询最多选择20个分类，请先移除部分已选项", "error");
    else setSelectedIds([...selectedIds, id]);
  }
  async function init() {
    setBusy("init");
    setError("");
    try {
      await post("/topics/initialize", { request_key: crypto.randomUUID() });
      reload();
      toast("已导入当前真实来源目录");
    } catch (failure: any) {
      setError(failure.message);
    } finally {
      setBusy("");
    }
  }
  async function syncLibrary() {
    setBusy("sync");
    setError("");
    try {
      const task = await post("/sync/topics", {
        request_key: crypto.randomUUID(),
      });
      toast("完整题材同步已提交，数据中心显示实际进度");
      onNavigate?.("data", { task_id: task.task_id || task.job_id });
    } catch (failure: any) {
      setError(failure.message);
    } finally {
      setBusy("");
    }
  }
  async function membership(
    topicId: string,
    instrumentId: string,
    action: string,
  ) {
    setBusy(instrumentId);
    setError("");
    try {
      await post(`/topics/${topicId}/members`, {
        instrument_id: instrumentId,
        action,
        reason: "用户在题材库维护当前分类",
      });
      reload();
      toast(
        action === "include"
          ? "成员已添加，原有其它归属保留"
          : action === "exclude"
            ? "已从当前分类人工排除，其它归属保留"
            : "已撤销当前分类人工修改，重新采用数据源归属",
      );
    } catch (failure: any) {
      setError(failure.message);
    } finally {
      setBusy("");
    }
  }
  async function archive() {
    if (!detail.data || !singleId) return;
    const archived = !!detail.data.archived;
    setBusy("archive");
    setError("");
    try {
      await patch(`/topics/${singleId}`, { archived: !archived });
      reload();
      toast(archived ? "已恢复分类" : "已归档，历史引用保留");
    } catch (failure: any) {
      setError(failure.message);
    } finally {
      setBusy("");
    }
  }
  async function loadHistory() {
    if (!singleId) return;
    try {
      setHistory(list(await api(`/topics/${singleId}/history`)));
    } catch (failure: any) {
      setError(failure.message);
    }
  }
  return (
    <div className="page-stack topics-workspace">
      <div className="section-intro">
        <p>
          题材库 · 独立业务行情日{" "}
          <strong>{bootstrap.data?.data_date || "待初始化"}</strong>{" "}
          <Badge>不随盘面日期切换</Badge>
        </p>
        <div className="button-group">
          <button className="btn" disabled={!!busy} onClick={syncLibrary}>
            {busy === "sync" ? <Spinner /> : <RefreshCw size={14} />}
            同步完整题材库
          </button>
          <button className="btn" disabled={!!busy} onClick={init}>
            {busy === "init" ? <Spinner /> : <RefreshCw size={14} />}
            导入本地题材
          </button>
          <button className="btn primary" onClick={() => setEditor(null)}>
            <Plus size={14} />
            新增题材
          </button>
        </div>
      </div>
      {error && <ErrorBox message={error} />}
      <div className="workspace-split">
        <TopicDirectory
          topics={catalog}
          selectedIds={selectedIds}
          query={query}
          kind={kind}
          includeArchived={includeArchived}
          loading={result.loading}
          error={result.error}
          onQuery={setQuery}
          onKind={setKind}
          onArchived={setIncludeArchived}
          onSelect={(id) => setSelectedIds([id])}
          onToggle={toggle}
          onReload={result.reload}
        />
        <div className="page-stack">
          <TopicBrowser
            topicIds={selectedIds}
            catalog={catalog}
            singleTopic={detail.data}
            refreshVersion={refreshVersion}
            businessDate={bootstrap.data?.data_date}
            busy={busy}
            onRemoveTopic={(id) =>
              setSelectedIds(selectedIds.filter((value) => value !== id))
            }
            onClearTopics={() => setSelectedIds([])}
            onMembership={membership}
            onStock={onStock}
            onChanged={reload}
          />
          {singleId && detail.error && (
            <ErrorBox message={detail.error} retry={detail.reload} />
          )}
          {singleId && detail.data && (
            <TopicMaintenance
              topic={detail.data}
              busy={busy}
              onEdit={() => setEditor(detail.data)}
              onArchive={archive}
              onHistory={loadHistory}
              onMembership={membership}
              onStock={onStock}
            />
          )}
          <Warning>
            维护从当前业务日生效，当前成员不冒充过去已知的归属。多选只联合浏览；单选可编辑该分类或维护成员。导入本地目录不会远端取数；完整同步需在数据中心手动发起并查看实际覆盖。
          </Warning>
        </div>
      </div>
      {editor !== undefined && (
        <TopicEditor
          topic={editor || undefined}
          onClose={() => setEditor(undefined)}
          onSaved={reload}
        />
      )}
      {history && (
        <Modal title="题材变更记录" wide onClose={() => setHistory(null)}>
          <div className="modal-body">
            <Table
              rows={history}
              columns={[
                { key: "revision", title: "版本" },
                { key: "name" },
                { key: "effective_business_date", title: "生效日" },
                {
                  key: "recorded_at_ms",
                  title: "记录时间",
                  render: (value) => dateTime(value),
                },
                { key: "action", title: "变更" },
                { key: "reason" },
              ]}
              emptyTitle="暂无变更记录"
            />
          </div>
        </Modal>
      )}
    </div>
  );
}

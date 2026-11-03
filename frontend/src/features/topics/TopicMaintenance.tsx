import { Archive, Edit3, History } from "lucide-react";
import type { Row } from "../../lib/api";
import InstrumentSearch from "../../components/InstrumentSearch";
import { Badge, Panel, Table } from "../../components/UI";

export default function TopicMaintenance({
  topic,
  busy,
  onEdit,
  onArchive,
  onHistory,
  onMembership,
  onStock,
}: {
  topic: Row;
  busy: string;
  onEdit: () => void;
  onArchive: () => void;
  onHistory: () => void;
  onMembership: (
    topicId: string,
    instrumentId: string,
    action: string,
  ) => Promise<void>;
  onStock: (id: string) => void;
}) {
  return (
    <Panel
      title={
        <>
          {topic.name} <Badge>V{topic.revision}</Badge>
          {!!topic.archived && <Badge tone="warn">已归档 · 成员只读</Badge>}
        </>
      }
      subtitle={
        topic.description || "单分类维护 · 多选时隐藏编辑入口，避免误改归属"
      }
      actions={
        <>
          <button
            className="icon-btn"
            title="查看变更记录"
            aria-label="查看题材变更记录"
            onClick={onHistory}
          >
            <History size={15} />
          </button>
          <button
            className="btn small"
            disabled={!!busy || !!topic.archived}
            onClick={onEdit}
          >
            <Edit3 size={13} />
            编辑
          </button>
          <button className="btn small" disabled={!!busy} onClick={onArchive}>
            <Archive size={13} />
            {topic.archived ? "恢复分类" : "归档"}
          </button>
        </>
      }
    >
      <div className="topic-detail-strip">
        <span>
          分类生效日 <b>{topic.effective_business_date || "当前业务日"}</b>
        </span>
        <div className="tags">
          {(topic.aliases || []).map((name: string) => (
            <Badge key={name}>{name}</Badge>
          ))}
        </div>
      </div>
      {topic.member_count == null && (
        <div className="panel-footnote">
          该成员档案尚未完整同步，已留存{" "}
          {topic.observed_member_count ?? topic.members?.length ?? 0}{" "}
          个可观察成员，不等同确有零成员。
        </div>
      )}
      {!topic.archived && (
        <div className="member-add">
          <span>添加成员</span>
          <InstrumentSearch
            onSelect={(id) => onMembership(topic.topic_id, id, "include")}
            placeholder="名称 / 代码 / 拼音 / 首字母"
          />
        </div>
      )}
      <p className="panel-footnote">
        {topic.origin === "provider"
          ? "人工添加保留补入的股票；排除会隐藏当前分类中的指定股票，后续同步仍保留修改。“撤销人工修改”重新采用数据源归属：来源有则保留，来源没有则移出，不影响其它分类。"
          : "这是人工研究分类；移除仅影响当前分类，原有其它题材和行业归属保持不变。"}
      </p>
      {!!topic.excluded_members?.length && (
        <>
          <div className="form-section-title">
            <h3>当前分类已手动排除</h3>
            <small>{topic.excluded_members.length} 只</small>
          </div>
          <Table
            rows={topic.excluded_members}
            onStock={onStock}
            columns={[
              { key: "name" },
              {
                key: "provider_member",
                title: "来源归属",
                render: (value) => (value ? "来源包含" : "来源未包含"),
              },
              {
                key: "actions",
                title: "操作",
                render: (_, row) => (
                  <button
                    className="btn small"
                    disabled={!!busy || !!topic.archived}
                    onClick={() =>
                      onMembership(
                        topic.topic_id,
                        row.instrument_id,
                        topic.origin === "provider"
                          ? "reset_to_provider"
                          : "include",
                      )
                    }
                  >
                    {topic.origin === "provider" ? "撤销人工排除" : "重新加入"}
                  </button>
                ),
              },
            ]}
          />
        </>
      )}
    </Panel>
  );
}

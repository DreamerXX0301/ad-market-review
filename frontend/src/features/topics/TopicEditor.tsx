import { useState } from "react";
import { Check } from "lucide-react";
import { patch, post } from "../../lib/api";
import type { Row } from "../../lib/api";
import {
  ErrorBox,
  Modal,
  Spinner,
  useToast,
  Warning,
} from "../../components/UI";
export default function TopicEditor({
  topic,
  onSaved,
  onClose,
}: {
  topic?: Row;
  onSaved: () => void;
  onClose: () => void;
}) {
  const [name, setName] = useState(topic?.name || ""),
    [description, setDescription] = useState(topic?.description || ""),
    [aliases, setAliases] = useState((topic?.aliases || []).join("、")),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const toast = useToast();
  async function save() {
    setError("");
    if (!name.trim()) {
      setError("请填写题材名称");
      return;
    }
    setBusy(true);
    try {
      const body = {
        name: name.trim(),
        description,
        aliases: aliases
          .split(/[,，、]/)
          .map((x: string) => x.trim())
          .filter(Boolean),
      };
      if (topic) await patch(`/topics/${topic.topic_id}`, body);
      else await post("/topics", body);
      toast("题材已保存，旧版本引用保持可追溯");
      onSaved();
      onClose();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal title={topic ? "编辑题材" : "新增研究题材"} onClose={onClose}>
      <div className="modal-body">
        <label>
          题材名称
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="例如：具身智能"
          />
        </label>
        <label>
          别名
          <input
            value={aliases}
            onChange={(e) => setAliases(e.target.value)}
            placeholder="多个别名使用顿号分隔"
          />
        </label>
        <label>
          研究说明
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={4}
            placeholder="记录定义范围与研究依据"
          />
        </label>
        <Warning>
          人工编辑从当前业务日生效，不会倒填历史回测题材。来源更新不会覆盖人工研究分类。
        </Warning>
        {error && <ErrorBox message={error} />}
      </div>
      <div className="modal-footer">
        <button className="btn" onClick={onClose}>
          取消
        </button>
        <button className="btn primary" disabled={busy} onClick={save}>
          {busy ? <Spinner /> : <Check size={14} />}保存
        </button>
      </div>
    </Modal>
  );
}

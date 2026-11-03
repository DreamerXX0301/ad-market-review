import { useState } from "react";
import type { Row } from "../../lib/api";
import { patch } from "../../lib/api";
import { ErrorBox, Modal, Spinner, Warning } from "../../components/UI";
import SectorRuleBuilder from "./SectorRuleBuilder";
import {
  sectorPolicyFromTrees,
  sectorPolicyTree,
  validateSectorRule,
} from "./sectorPresentation";

export default function SectorPolicyEditor({
  round,
  fields,
  period,
  onClose,
  onSaved,
}: {
  round: Row;
  fields: Row[];
  period: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const policy = round.policy || {};
  const [alerts, setAlerts] = useState<Row>(
      sectorPolicyTree(policy.alerts, policy.alert_logic),
    ),
    [exits, setExits] = useState<Row>(
      sectorPolicyTree(policy.exits, policy.exit_logic),
    ),
    [duration, setDuration] = useState(
      policy.duration_sessions ?? round.duration_sessions ?? 30,
    ),
    [entryAlerts, setEntryAlerts] = useState(
      policy.allow_entry_day_alerts === true,
    ),
    [entryExits, setEntryExits] = useState(
      policy.allow_entry_day_exits === true,
    ),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const alertFields = fields,
    exitFields = fields.filter((field) => !field.alerts_only);
  const validTree = (tree: Row, specs: Row[]) =>
    (tree.all || tree.any)?.length === 0
      ? null
      : validateSectorRule(tree, specs, period);
  async function save() {
    const problem =
      validTree(alerts, alertFields) || validTree(exits, exitFields);
    if (
      problem ||
      !Number.isInteger(Number(duration)) ||
      Number(duration) < 1 ||
      Number(duration) > 750
    ) {
      setError(problem || "期限请输入1—750交易日");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await patch(`/sector-tracking-rounds/${round.round_id}/policy`, {
        policy: {
          ...policy,
          ...sectorPolicyFromTrees(alerts, exits),
          duration_sessions: Number(duration),
          allow_entry_day_alerts: entryAlerts,
          allow_entry_day_exits: entryExits,
        },
      });
      onSaved();
      onClose();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal title={`${round.name} · 板块跟踪条件`} onClose={onClose} wide>
      <div className="modal-body">
        <div className="form-grid">
          <label>
            跟踪期限 · 交易日
            <input
              type="number"
              min={1}
              max={750}
              step={1}
              value={duration}
              onChange={(e) => setDuration(e.target.value)}
            />
          </label>
          <label className="check-label">
            <input
              type="checkbox"
              checked={entryAlerts}
              onChange={(e) => setEntryAlerts(e.target.checked)}
            />
            允许入池当日提醒
          </label>
          <label className="check-label">
            <input
              type="checkbox"
              checked={entryExits}
              onChange={(e) => setEntryExits(e.target.checked)}
            />
            允许入池当日提前结束
          </label>
        </div>
        <section className="sector-policy-section">
          <h3>
            提醒条件 <small>触发后继续跟踪</small>
          </h3>
          <SectorRuleBuilder
            value={alerts}
            onChange={setAlerts}
            fields={alertFields.filter((field) => !field.selection_only)}
            period={period}
          />
        </section>
        <section className="sector-policy-section">
          <h3>
            提前结束条件 <small>触发后归入结束历史</small>
          </h3>
          <SectorRuleBuilder
            value={exits}
            onChange={setExits}
            fields={exitFields.filter((field) => !field.selection_only)}
            period={period}
          />
        </section>
        <Warning>
          入池当天为第0日；日/周/月指标分别按已核验周期计算。仅修改这个板块轮次；提醒不会自动结束，成分股也不会自动加入个股池。
        </Warning>
        {error && <ErrorBox message={error} />}
      </div>
      <div className="modal-footer">
        <button className="btn" onClick={onClose}>
          取消
        </button>
        <button
          className="btn primary"
          disabled={busy || !fields.length}
          onClick={save}
        >
          {busy && <Spinner />}保存板块条件
        </button>
      </div>
    </Modal>
  );
}

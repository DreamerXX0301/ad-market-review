import { useState } from "react";
import { api, useApi } from "../../lib/api";

const path = "/market/intraday/automation";
type Automation = { enabled: boolean; revision: number };

export default function IntradayAutomation() {
  const settings = useApi<Automation>(path);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const value = settings.data;

  const toggle = async () => {
    if (!value || saving) return;
    setSaving(true);
    setError("");
    try {
      const next = await api<Automation>(path, {
        method: "PUT",
        body: JSON.stringify({ enabled: !value.enabled, expected_revision: value.revision }),
      });
      if (next.enabled === value.enabled || next.revision <= value.revision)
        throw new Error("未能确认自动采集设置。");
      settings.setData(next);
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : "自动采集设置暂不可用。");
      settings.reload();
    } finally {
      setSaving(false);
    }
  };

  if (!value) return null;
  return (
    <span className="intraday-automation">
      <button type="button" className="btn small" aria-pressed={value.enabled}
        disabled={saving || settings.loading} onClick={toggle}>
        {saving ? "正在保存…" : value.enabled ? "自动采集已开" : "开启自动采集"}
      </button>
      {error && <small role="alert">{error}</small>}
    </span>
  );
}

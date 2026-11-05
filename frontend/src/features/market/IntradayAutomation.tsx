import { Clock3, Settings2 } from "lucide-react";
import { useApi } from "../../lib/api";
import type { Row } from "../../lib/api";

export function IntradayAutomationStatus({
  value,
  error,
}: {
  value?: Row | null;
  error?: string;
}) {
  const enabled = typeof value?.enabled === "boolean" ? value.enabled : null;
  const label =
    error || enabled === null
      ? "自动状态待确认"
      : enabled
        ? "自动采集已开"
        : "自动采集关闭";
  return (
    <a
      className="btn small intraday-automation-status"
      href="#data?section=intraday-automatic"
      aria-label={`${label}，去数据中心设置`}
      title="在数据中心统一设置自动采集计划"
      data-enabled={enabled ?? undefined}
    >
      <Clock3 size={13} aria-hidden="true" />
      {label}
      <Settings2 size={12} aria-hidden="true" />
    </a>
  );
}

/** Observation pages read the authorization; only Data Center can change it. */
export default function IntradayAutomation() {
  const settings = useApi<Row>("/market/intraday/automation");
  return (
    <IntradayAutomationStatus value={settings.data} error={settings.error} />
  );
}

import "./ReadonlyPreviewBanner.css";

/** This label identifies an isolated build; server-side method guards enforce it. */
export default function ReadonlyPreviewBanner({
  enabled = false,
}: {
  enabled?: boolean;
}) {
  return enabled ? (
    <div className="readonly-preview-banner" role="status">
      <strong>只读验收预览</strong>
      <span>可查看调研结果、调整图表显示；数据操作由预览服务限制。</span>
    </div>
  ) : null;
}

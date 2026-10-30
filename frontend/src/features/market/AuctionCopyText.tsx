import { Copy } from "lucide-react";
import { useToast } from "../../components/UI";

export default function AuctionCopyText({
  text,
  label,
  visibleLabel,
}: {
  text: string;
  label: string;
  visibleLabel?: string;
}) {
  const notify = useToast();
  return (
    <button
      type="button"
      className={`auction-copy-text${visibleLabel ? " with-label" : ""}`}
      title={label}
      aria-label={label}
      onClick={async (event) => {
        event.preventDefault();
        event.stopPropagation();
        try {
          await navigator.clipboard.writeText(text);
          notify("已复制");
        } catch {
          notify("复制未完成，请选中文字手动复制");
        }
      }}
    >
      <Copy size={12} />
      {visibleLabel && <span>{visibleLabel}</span>}
    </button>
  );
}

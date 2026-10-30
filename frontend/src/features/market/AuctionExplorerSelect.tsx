import type { ComponentPropsWithoutRef } from "react";
import { ChevronDown } from "lucide-react";

export default function AuctionExplorerSelect(
  props: ComponentPropsWithoutRef<"select">,
) {
  return (
    <span className="auction-explorer-select">
      <select {...props} />
      <ChevronDown size={14} aria-hidden="true" />
    </span>
  );
}

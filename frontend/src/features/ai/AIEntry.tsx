import { ArrowUpRight, Sparkles } from "lucide-react";
import type { AILaunchContext } from "./aiNavigation";
import { aiLaunchLocation } from "./aiNavigation";
import "./AIHub.css";

/** Contextual entry only navigates. Opening it never creates or sends a message. */
export default function AIEntry(props: AILaunchContext) {
  return (
    <div className="ai-context-entry">
      <a
        className="ai-entry-shortcut"
        href={aiLaunchLocation(props)}
        onClick={(event) => {
          if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey)
            return;
          event.preventDefault();
          window.location.hash = aiLaunchLocation(props, crypto.randomUUID());
        }}
      >
        <Sparkles size={16} />
        <span>
          {props.kind === "stock" ? "与 AI 研究这只股票" : "与 AI 聊聊这天行情"}
        </span>
        <ArrowUpRight size={15} />
      </a>
    </div>
  );
}

import { ArrowUpRight, Sparkles } from "lucide-react";
import type { AILaunchContext } from "./aiNavigation";
import { aiLaunchLocation } from "./aiNavigation";
import "./AIHub.css";

const newsEntryLabels = {
  day: "AI 梳理当日消息",
  news: "AI 解读这条消息",
  event: "AI 解读这个事件",
};

/** Contextual entry only navigates. Opening it never creates or sends a message. */
export default function AIEntry(props: AILaunchContext) {
  const label = props.newsContext
    ? newsEntryLabels[props.newsContext.kind]
    : props.kind === "stock"
      ? "与 AI 研究这只股票"
      : props.marketStage
        ? "AI 解读当前时点"
        : "与 AI 聊聊这天行情";
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
        <span>{label}</span>
        <ArrowUpRight size={15} />
      </a>
    </div>
  );
}

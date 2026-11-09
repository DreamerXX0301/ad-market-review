import {
  lazy,
  Suspense,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { Loading } from "../../components/UI";
import { usePageActive } from "../../lib/api";
import {
  aiLaunchQuestion,
  readAILaunch,
  upgradeChatLocation,
} from "./aiNavigation";
import "./AIHub.css";

const AIWorkspace = lazy(() => import("./AIWorkspace"));

export default function AIHub({ onConfigure }: { onConfigure?: () => void }) {
  const active = usePageActive();
  const host = useRef<HTMLElement>(null);
  const [launch, setLaunch] = useState(() =>
    readAILaunch(window.location.hash),
  );
  useLayoutEffect(() => {
    if (!active) return;
    let frame = 0;
    const schedule = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        if (!host.current) return;
        const top = Math.max(0, host.current.getBoundingClientRect().top);
        const height = Math.max(420, window.innerHeight - top - 12);
        host.current.style.setProperty("--ai-hub-height", height + "px");
      });
    };
    const observer = new ResizeObserver(schedule);
    for (const element of document.querySelectorAll(
      ".topbar,.readonly-preview-banner",
    ))
      observer.observe(element);
    window.addEventListener("resize", schedule);
    schedule();
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      window.removeEventListener("resize", schedule);
    };
  }, [active]);
  useEffect(() => {
    const followLink = (event: HashChangeEvent) => {
      const hash = new URL(event.newURL).hash;
      const next = readAILaunch(hash);
      // Returning through the sidebar must not discard a conversation or restart
      // its request. Only an explicit contextual/deep link changes the workspace.
      if (next.explicit) {
        const target = upgradeChatLocation(hash);
        if (
          window.location.hash.split("?")[0] === "#ai" &&
          window.location.hash !== target
        )
          window.history.replaceState(window.history.state, "", target);
        setLaunch((current) => (current.key === next.key ? current : next));
      }
    };
    window.addEventListener("hashchange", followLink);
    return () => window.removeEventListener("hashchange", followLink);
  }, []);
  return (
    <section className="ai-hub-page" aria-label="AI 助手" ref={host}>
      <div className="ai-hub-workspace">
        <Suspense fallback={<Loading />}>
          <AIWorkspace
            key={launch.key}
            kind={launch.kind}
            instrumentId={launch.instrumentId}
            reportId={launch.reportId}
            asOf={launch.asOf}
            marketScope={launch.marketScope}
            newsContext={launch.newsContext}
            initialQuestion={aiLaunchQuestion(launch)}
            onConfigure={onConfigure}
            allHistory
            pageRoute="ai"
            pageLaunchId={launch.launchId}
          />
        </Suspense>
      </div>
    </section>
  );
}

import { useCallback, useEffect, useRef, useState } from "react";
import type { RefObject } from "react";

/** Follow real content growth, while leaving a reader's position under their control. */
export function useAIChatFollow(
  viewport: RefObject<HTMLDivElement | null>,
  active: boolean,
  sessionId: string,
) {
  const following = useRef(false);
  const [showLatest, setShowLatest] = useState(false);
  const frame = useRef(0);
  const lastAutomaticTop = useRef<number | null>(null);
  const touchY = useRef<number | null>(null);
  const activeRef = useRef(active);
  activeRef.current = active;

  const cancel = useCallback(() => {
    cancelAnimationFrame(frame.current);
    frame.current = 0;
  }, []);

  const schedule = useCallback(() => {
    if (!activeRef.current) return;
    if (!following.current) {
      const box = viewport.current;
      if (box?.clientHeight)
        setShowLatest(box.scrollHeight - box.clientHeight - box.scrollTop > 4);
      return;
    }
    if (frame.current) return;
    frame.current = requestAnimationFrame(() => {
      frame.current = 0;
      const box = viewport.current;
      if (!activeRef.current || !following.current || !box?.clientHeight)
        return;
      const bottom = Math.max(0, box.scrollHeight - box.clientHeight);
      lastAutomaticTop.current = bottom;
      box.scrollTop = bottom;
      setShowLatest(false);
    });
  }, [viewport]);

  const pause = useCallback(() => {
    following.current = false;
    lastAutomaticTop.current = null;
    cancel();
    const box = viewport.current;
    setShowLatest(!!box && box.scrollHeight > box.clientHeight + 4);
  }, [cancel, viewport]);

  const resume = useCallback(() => {
    following.current = true;
    setShowLatest(false);
    schedule();
  }, [schedule]);

  const reset = useCallback(() => {
    following.current = false;
    lastAutomaticTop.current = null;
    touchY.current = null;
    cancel();
    setShowLatest(false);
  }, [cancel]);

  const capture = useCallback(() => {
    const box = viewport.current;
    if (!activeRef.current || !box?.clientHeight) return;
    const nearBottom = box.scrollHeight - box.clientHeight - box.scrollTop <= 4;
    // A delayed scroll event from our last frame is not a reader scrolling up.
    const automatic =
      following.current &&
      lastAutomaticTop.current != null &&
      Math.abs(box.scrollTop - lastAutomaticTop.current) < 2;
    if (automatic) return;
    following.current = nearBottom;
    if (!nearBottom) cancel();
    setShowLatest(!nearBottom);
  }, [cancel, viewport]);

  useEffect(() => {
    const box = viewport.current;
    if (!active || !box) return cancel;
    // Observe the messages themselves: the scroll viewport can keep the same
    // dimensions while streamed paragraphs, tables or final citations grow.
    const resize = new ResizeObserver(schedule);
    const observe = () => {
      resize.disconnect();
      resize.observe(box);
      for (const child of box.children) resize.observe(child);
      schedule();
    };
    const mutation = new MutationObserver(observe);
    mutation.observe(box, { childList: true });
    observe();
    return () => {
      mutation.disconnect();
      resize.disconnect();
      cancel();
    };
  }, [active, sessionId, viewport, cancel, schedule]);

  return {
    following,
    showLatest,
    reset,
    resume,
    capture,
    schedule,
    onWheel: (event: { deltaY: number }) => {
      if (event.deltaY < 0) pause();
    },
    onTouchStart: (event: {
      touches: { readonly [index: number]: { clientY: number } | undefined };
    }) => {
      touchY.current = event.touches[0]?.clientY ?? null;
    },
    onTouchMove: (event: {
      touches: { readonly [index: number]: { clientY: number } | undefined };
    }) => {
      const next = event.touches[0]?.clientY;
      if (next != null && touchY.current != null && next > touchY.current + 2)
        pause();
      touchY.current = next ?? null;
    },
    onPointerDown: (event: { clientX: number; button: number }) => {
      const box = viewport.current;
      if (
        box &&
        event.button === 0 &&
        event.clientX >= box.getBoundingClientRect().right - 16
      )
        pause();
    },
    onKeyDown: (event: {
      key: string;
      shiftKey?: boolean;
      target: EventTarget | null;
    }) => {
      const target = event.target as HTMLElement | null;
      if (target?.closest("input,textarea,select,button,a,[contenteditable]"))
        return;
      if (
        ["ArrowUp", "PageUp", "Home"].includes(event.key) ||
        (event.key === " " && event.shiftKey)
      )
        pause();
    },
  };
}

import { Check, Copy, LoaderCircle, TriangleAlert } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { copyStockCode, stockCodeCopyValue } from "./stockCodeCopy";
import "./CopyStockCode.css";

export default function CopyStockCode({
  code,
  instrumentId,
  variant = "short",
  className = "",
}: {
  code?: unknown;
  instrumentId?: unknown;
  variant?: "short" | "full";
  className?: string;
}) {
  const { display, value } = stockCodeCopyValue(code, instrumentId, variant);
  const [feedback, setFeedback] = useState(() => ({ value, status: "idle" }));
  const status = feedback.value === value ? feedback.status : "idle";
  const generation = useRef(0),
    inFlight = useRef(false),
    resetTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const currentValue = useRef(value);
  currentValue.current = value;
  useEffect(() => {
    generation.current++;
    inFlight.current = false;
    setFeedback((previous) =>
      previous.value === value && previous.status === "idle"
        ? previous
        : { value, status: "idle" },
    );
    if (resetTimer.current) clearTimeout(resetTimer.current);
    return () => {
      generation.current++;
      if (resetTimer.current) clearTimeout(resetTimer.current);
    };
  }, [value]);
  const failure = status === "failed";
  const title =
    status === "copied"
      ? `已复制 ${value}`
      : failure
        ? "无法自动复制，请选中代码手动复制"
        : `复制${variant === "full" ? "完整" : "六位"}代码 ${value}`;
  async function copy() {
    if (inFlight.current || !value) return;
    inFlight.current = true;
    const token = ++generation.current;
    if (resetTimer.current) clearTimeout(resetTimer.current);
    setFeedback({ value, status: "copying" });
    const result = await copyStockCode(value);
    if (token !== generation.current || value !== currentValue.current) return;
    inFlight.current = false;
    setFeedback({ value, status: result === "copied" ? "copied" : "failed" });
    if (result === "copied")
      resetTimer.current = setTimeout(
        () => setFeedback({ value, status: "idle" }),
        1800,
      );
  }
  return (
    <span
      className={`copy-stock-code ${className}${failure ? " copy-failed" : ""}`}
      onClick={(event) => event.stopPropagation()}
      onMouseDown={(event) => event.stopPropagation()}
    >
      <code className="copy-stock-code-value">{display}</code>
      {value && (
        <button
          type="button"
          className={`copy-stock-code-button ${status}`}
          data-copy-stock-code="true"
          title={title}
          aria-label={title}
          disabled={status === "copying"}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ")
              event.stopPropagation();
          }}
          onClick={(event) => {
            event.preventDefault();
            event.stopPropagation();
            void copy();
          }}
        >
          {status === "copied" ? (
            <Check size={12} />
          ) : status === "copying" ? (
            <LoaderCircle size={12} />
          ) : failure ? (
            <TriangleAlert size={12} />
          ) : (
            <Copy size={12} />
          )}
        </button>
      )}
      <span
        className="copy-stock-code-feedback"
        role="status"
        aria-live="polite"
      >
        {status === "copied"
          ? `已复制 ${value}`
          : failure
            ? "无法自动复制，请选中代码手动复制"
            : ""}
      </span>
    </span>
  );
}

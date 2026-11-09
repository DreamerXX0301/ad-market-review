import { dateTime } from "../../lib/format";
import {
  aiSourceLabel,
  archivedNewsSources,
  itemText,
  metricSummary,
  plainText,
  chatAnswerText,
  nativeWebSources,
  safeHttpUrl,
} from "./aiState";
import type { AIContext, AIItem, AIMessage } from "./aiTypes";
import AIAnswerText from "./AIAnswerText";
import { attachmentSize, safeAttachmentUrl } from "./aiAttachments";

function CompactReferences({
  evidenceIds,
  metricIds,
  context,
}: {
  evidenceIds: string[];
  metricIds: string[];
  context: AIContext;
}) {
  return (
    <ul className="ai-compact-references">
      {evidenceIds
        .map((id) => context.evidence?.find((item) => item.evidence_id === id))
        .filter((item): item is AIItem => !!item)
        .map((item: AIItem) => (
          <li key={item.evidence_id}>
            <strong>{itemText(item)}</strong>
            <span>
              {aiSourceLabel(item.source)}
              {item.dates?.report_period || item.report_period
                ? ` · ${plainText(item.dates?.report_period || item.report_period)}`
                : ""}
            </span>
          </li>
        ))}
      {metricIds
        .map((id) => context.metrics?.find((item) => item.metric_id === id))
        .filter((item): item is AIItem => !!item)
        .map((item: AIItem) => (
          <li key={item.metric_id}>
            <strong>{metricSummary(item)}</strong>
            <span>
              {plainText(
                item.as_of || context.metric_defaults?.as_of || context.as_of,
                "",
              )}
              {item.scope ? ` · ${plainText(item.scope)}` : ""}
            </span>
          </li>
        ))}
    </ul>
  );
}

export function AIMessageView({
  message,
  context,
  onAsk,
  suggestionsDisabled = false,
}: {
  message: AIMessage;
  context: AIContext;
  onAsk?: (question: string) => void;
  suggestionsDisabled?: boolean;
}) {
  const result = message.interpretation;
  const assistant = message.role === "assistant";
  const agent = assistant && message.response_type === "agent_answer";
  const webAnswer = assistant && message.response_type === "web_answer";
  const streaming = message.state === "streaming";
  const incomplete =
    assistant &&
    !streaming &&
    (message.partial === true ||
      ["cancelled", "failed", "interrupted_manual_retry"].includes(
        message.state,
      ));
  const modern = Array.isArray(result?.answer);
  const blocks: AIItem[] = modern ? result!.answer : [];
  const claims = (result?.selected_claim_ids || [])
    .map((id: string) => context.claims?.find((item) => item.claim_id === id))
    .filter((item: AIItem | undefined): item is AIItem => !!item) as AIItem[];
  const evidenceIds = [
    ...new Set<string>(
      modern
        ? blocks.flatMap((block) => block.evidence_ids || [])
        : [
            ...claims.flatMap((claim) => claim.evidence_ids || []),
            ...(result?.hypotheses || []).flatMap((item: AIItem) => [
              ...(item.supporting_evidence_ids || []),
              ...(item.counter_evidence_ids || []),
            ]),
            ...(result?.questions || []).flatMap(
              (item: AIItem) => item.evidence_ids || [],
            ),
          ],
    ),
  ];
  const metricIds = [
    ...new Set<string>(
      modern
        ? blocks.flatMap((block) => block.metric_refs || [])
        : (result?.hypotheses || []).flatMap(
            (item: AIItem) => item.metric_refs || [],
          ),
    ),
  ];
  const sources = nativeWebSources(message);
  const newsSources = assistant ? archivedNewsSources(message) : [];
  const hasSources =
    sources.length > 0 ||
    newsSources.length > 0 ||
    evidenceIds.length > 0 ||
    metricIds.length > 0;
  return (
    <article
      className={`ai-message ${assistant ? "is-assistant" : "is-user"}`}
      data-message-id={message.message_id}
    >
      <div className="ai-message-heading">
        <strong>{assistant ? "AI" : "你"}</strong>
        <time>{dateTime(message.created_at_ms)}</time>
      </div>
      {!assistant && !!message.attachments?.length && (
        <div className="ai-message-attachments" aria-label="消息附件">
          {message.attachments.map((item) => {
            const url = safeAttachmentUrl(item.content_url);
            const body = (
              <>
                <span className="ai-attachment-caption">
                  <strong>{item.name}</strong>
                  <small>
                    {attachmentSize(item.size)}
                    {item.text_only ? " · 已读取正文" : ""}
                    {item.page_count ? ` · ${item.page_count} 页` : ""}
                  </small>
                </span>
              </>
            );
            return url ? (
              <a
                className={`ai-attachment-card ${item.kind === "image" ? "is-image" : ""}`}
                key={item.attachment_id}
                href={url}
                target="_blank"
                rel="noreferrer"
                title={`查看 ${item.name}`}
              >
                {item.kind === "image" && (
                  <img src={url} alt={item.name} loading="lazy" />
                )}
                {body}
              </a>
            ) : (
              <div className="ai-attachment-card" key={item.attachment_id}>
                {body}
              </div>
            );
          })}
        </div>
      )}
      {incomplete && (
        <p className="ai-incomplete-note">
          {message.state === "cancelled"
            ? "回答已停止，以下内容尚未完成。"
            : "这次回答未完成，以下内容仅供核对。"}
        </p>
      )}
      {streaming ? (
        <div className="ai-stream-text" aria-busy="true">
          <AIAnswerText text={message.text} />
          <span className="ai-stream-cursor" aria-label="正在回答" />
        </div>
      ) : agent || webAnswer ? (
        <AIAnswerText message={message} />
      ) : modern ? (
        <div className="ai-answer-body">
          {blocks.map((block, index) => (
            <section key={index}>
              {block.heading && <h4>{plainText(block.heading)}</h4>}
              <AIAnswerText text={chatAnswerText(block, context)} />
            </section>
          ))}
        </div>
      ) : result ? (
        <>
          <AIAnswerText text={plainText(result.summary, "本次未返回回答")} />
          {(result.hypotheses?.length > 0 || result.questions?.length > 0) && (
            <details className="ai-message-more">
              <summary>展开完整回答</summary>
              {(result.hypotheses || []).map((item: AIItem, index: number) => (
                <section key={index}>
                  <AIAnswerText text={plainText(item.text)} />
                  <p>{plainText(item.open_question)}</p>
                </section>
              ))}
              {!!result.questions?.length && (
                <ol>
                  {result.questions.map((item: AIItem, index: number) => (
                    <li key={index}>{plainText(item.text)}</li>
                  ))}
                </ol>
              )}
            </details>
          )}
        </>
      ) : assistant ? (
        <AIAnswerText
          text={plainText(
            message.text,
            incomplete ? "这次没有完成回答。" : "本次暂无回答",
          )}
        />
      ) : (
        <p className="ai-message-text">
          {plainText(
            message.text,
            incomplete ? "这次没有完成回答。" : "本次暂无回答",
          )}
        </p>
      )}
      {assistant && !streaming && hasSources && (
        <details className="ai-answer-sources">
          <summary>来源</summary>
          <CompactReferences
            evidenceIds={evidenceIds}
            metricIds={metricIds}
            context={context}
          />
          {newsSources.length > 0 && (
            <ol className="ai-native-sources" aria-label="本机归档原文">
              {newsSources.map(({ url, title, source }) => (
                <li key={JSON.stringify([url, title])}>
                  <a href={url} target="_blank" rel="noopener noreferrer">
                    {title}
                  </a>
                  {source && <span>{source} · 本机归档</span>}
                </li>
              ))}
            </ol>
          )}
          {sources.length > 0 && (
            <ol className="ai-native-sources">
              {sources.map((source) => (
                <li key={`${source.source_id}:${source.url}`}>
                  <a
                    href={safeHttpUrl(source.url)!}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    {plainText(source.title, "网页来源")}
                  </a>
                  <span>
                    {source.accessed_at_ms != null
                      ? `查询于 ${dateTime(source.accessed_at_ms)}`
                      : ""}
                    {source.published_at != null
                      ? ` · 发布于 ${dateTime(source.published_at)}`
                      : ""}
                  </span>
                </li>
              ))}
            </ol>
          )}
        </details>
      )}
      {modern && result!.follow_up_questions?.length > 0 && onAsk && (
        <div className="ai-suggestions" aria-label="建议继续问">
          {result!.follow_up_questions.map(
            (question: string, index: number) => (
              <button
                type="button"
                key={index}
                disabled={suggestionsDisabled}
                onClick={() => onAsk(question)}
              >
                {question}
              </button>
            ),
          )}
        </div>
      )}
    </article>
  );
}

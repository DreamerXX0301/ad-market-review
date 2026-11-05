import type { AIAttachmentInput, AIProvider } from "./aiTypes";

export const ATTACHMENT_ACCEPT =
  ".png,.jpg,.jpeg,.webp,.pdf,.txt,.md,.markdown,.csv,.tsv,.json,.docx";
const mediaTypes: Record<string, string> = {
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  webp: "image/webp",
  pdf: "application/pdf",
  txt: "text/plain",
  md: "text/markdown",
  markdown: "text/markdown",
  csv: "text/csv",
  tsv: "text/tab-separated-values",
  json: "application/json",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
};
export interface AIDraftAttachment {
  id: string;
  size: number;
  input: AIAttachmentInput;
}
export function attachmentSize(size: number) {
  return size >= 1048576
    ? `${(size / 1048576).toFixed(1)} MB`
    : `${Math.max(1, Math.ceil(size / 1024))} KB`;
}
export function attachmentType(name: string) {
  return mediaTypes[name.split(".").pop()?.toLowerCase() || ""];
}
export function attachmentIssue(
  items: AIDraftAttachment[],
  provider?: AIProvider,
): string {
  if (
    items.some((item) => item.input.media_type.startsWith("image/")) &&
    !provider?.capabilities?.images
  )
    return "当前模型不支持图片，请在下方切换支持看图的模型，或移除图片后发送。";
  return "";
}
// Draft bytes stay in component memory; persistence starts only with an explicit send.
export async function readAttachmentFiles(
  files: File[],
  existing: AIDraftAttachment[],
  provider?: AIProvider,
): Promise<AIDraftAttachment[]> {
  const caps = provider?.capabilities;
  if (existing.length + files.length > (caps?.max_files || 3))
    throw new Error("每次最多添加 3 个附件。");
  if (
    existing.reduce((sum, item) => sum + item.size, 0) +
      files.reduce((sum, file) => sum + file.size, 0) >
    (caps?.max_total_bytes || 10485760)
  )
    throw new Error("这次附件合计不能超过 10 MB。");
  for (const file of files) {
    if (!attachmentType(file.name))
      throw new Error(
        `暂不支持 ${file.name}。请选择图片、PDF、文字文件或 DOCX。`,
      );
    if (!file.size) throw new Error(`${file.name} 是空文件。`);
    if (file.size > (caps?.max_file_bytes || 5242880))
      throw new Error(`${file.name} 超过了单个附件 5 MB 的限制。`);
  }
  return Promise.all(
    files.map(async (file) => {
      const data = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () =>
          resolve(String(reader.result || "").split(",", 2)[1] || "");
        reader.onerror = () =>
          reject(new Error(`${file.name} 读取失败，请重新添加。`));
        reader.onabort = () => reject(new Error("附件读取已取消。"));
        reader.readAsDataURL(file);
      });
      if (!data) throw new Error(`${file.name} 读取失败。`);
      return {
        id: crypto.randomUUID(),
        size: file.size,
        input: {
          name: file.name,
          media_type: attachmentType(file.name),
          data_base64: data,
        },
      };
    }),
  );
}
export function safeAttachmentUrl(url?: string) {
  return url &&
    /^\/api\/v1\/ai\/sessions\/[a-zA-Z0-9_-]+\/messages\/[a-zA-Z0-9_-]+\/attachments\/[a-zA-Z0-9_-]+$/.test(
      url,
    )
    ? url
    : "";
}

import type { ReactNode } from "react";
import type { AIItem, AIMessage } from "./aiTypes";
import { plainText, webAnswerParts } from "./aiState";

const integer = String.raw`\d+(?:[,，]\d{3})*`;
const number = String.raw`${integer}(?:\.\d+)?`;
const count = String.raw`[ \t]*(?:至少|约|≥|≤)?[ \t]*${integer}[ \t]*(?:家|只)`;
const amount = String.raw`[ \t]*(?:约[ \t]*)?${number}[ \t]*(?:万亿|亿|万)?元`;
const marketValues = new RegExp(
  [
    String.raw`(?<![\w.+＋\-−－])[+＋\-−－]${number}[ \t]*(?:[%％]|(?:个)?百分点)`,
    String.raw`(?<warning>已?(?:新增炸板|炸板|封板打开|新开板|开板)${count}|已(?:炸板|开板)|(?<=[(（])炸板(?=[)）]))`,
    String.raw`(?<up>已?(?:新封板|新增封板|回封|上涨|高开|封板|首板|连板|涨停|晋级)${count}|放量${amount}|成交(?:同比|环比)?放量|已(?:封板|回封)|(?<=[(（])(?:首板|回封)(?=[)）])|(?<![\w.])\d+(?:连)?板)`,
    String.raw`(?<down>(?:下跌|低开|跌停|回落)${count}|缩量${amount}|成交(?:同比|环比)?缩量)`,
  ].join("|"),
  "gu",
);
const breadthLabel = /^(?:上涨\/下跌\/平盘|涨\/跌\/平|高开\/低开\/平开)$/;
const breadthValues = new RegExp(
  String.raw`^(${integer})([ \t]*[/／][ \t]*)(${integer})(?=[ \t]*[/／][ \t]*${integer}(?![\d.,，]))`,
);

function marketText(
  text: string,
  start: number,
  breadth: boolean,
): ReactNode[] {
  const value = (part: string, tone: string, offset: number) =>
    Number(part.match(/\d[\d,，]*(?:\.\d+)?/)?.[0].replace(/[,，]/g, "")) ===
    0 ? (
      part
    ) : (
      <span
        className={`ai-market-value ${tone}`}
        key={`value-${start + offset}`}
      >
        {part}
      </span>
    );
  const nodes: ReactNode[] = [];
  let cursor = 0;
  const totals = breadth ? breadthValues.exec(text) : null;
  if (totals) {
    const [, up, divider, down] = totals;
    nodes.push(
      value(up, "up", 0),
      divider,
      value(down, "down", up.length + divider.length),
    );
    cursor = totals[0].length;
  }
  for (const match of text.matchAll(marketValues)) {
    const offset = match.index;
    const groups = match.groups!;
    const tone = groups.warning
      ? "is-board-warning"
      : groups.up || /^[+＋]/.test(match[0])
        ? "up"
        : "down";
    nodes.push(text.slice(cursor, offset), value(match[0], tone, offset));
    cursor = offset + match[0].length;
  }
  nodes.push(text.slice(cursor));
  return nodes;
}

// Markdown changes presentation only. Hyperlinks are exclusively native,
// server-validated citations; model-authored links never become anchors.
export default function AIAnswerText({
  message,
  text,
}: {
  message?: AIMessage;
  text?: string;
}) {
  const input = webAnswerParts(message || { text: text || "" });
  let value = "";
  const refs: { position: number; citation: AIItem; key: number }[] = [];
  for (const part of input) {
    if (part.text != null) value += part.text;
    if (part.citation)
      refs.push({
        position: value.length,
        citation: part.citation,
        key: refs.length,
      });
  }
  const used = new Set<number>();
  const citationNode = (ref: (typeof refs)[number]) => {
    used.add(ref.key);
    return (
      <sup className="ai-native-citation" key={`cite-${ref.key}`}>
        <a
          href={ref.citation.url}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={`来源 ${ref.citation.number}：${plainText(ref.citation.title, "网页来源")}`}
        >
          [{ref.citation.number}]
        </a>
      </sup>
    );
  };
  const remaining = (start: number, end: number) =>
    refs
      .filter(
        (ref) =>
          !used.has(ref.key) && ref.position >= start && ref.position <= end,
      )
      .map(citationNode);
  const plain = (
    start: number,
    end: number,
    highlight: boolean,
    breadth = false,
  ): ReactNode[] => {
    const nodes: ReactNode[] = [];
    let cursor = start;
    const append = (next: number) => {
      const fragment = value.slice(cursor, next);
      if (highlight)
        nodes.push(
          ...marketText(fragment, cursor, breadth && cursor === start),
        );
      else nodes.push(fragment);
    };
    for (const ref of refs.filter(
      (item) =>
        !used.has(item.key) && item.position >= start && item.position <= end,
    )) {
      append(ref.position);
      nodes.push(citationNode(ref));
      cursor = ref.position;
    }
    append(end);
    return nodes;
  };
  const inline = (
    start: number,
    end: number,
    breadth = false,
    depth = 0,
  ): ReactNode[] => {
    if (depth > 2) return plain(start, end, true, breadth);
    const fragment = value.slice(start, end);
    const pattern = /(\*\*|__)([^\n]+?)\1|`([^`\n]+)`/g;
    const nodes: ReactNode[] = [];
    let cursor = start,
      count = 0;
    for (const match of fragment.matchAll(pattern)) {
      if (++count > 200) break;
      const from = start + match.index!,
        to = from + match[0].length;
      nodes.push(...plain(cursor, from, true, breadth && cursor === start));
      const width = match[1] ? 2 : 1;
      const children = match[1]
        ? inline(from + width, to - width, breadth && from === start, depth + 1)
        : plain(from + width, to - width, false);
      nodes.push(
        match[1] ? (
          <strong key={`bold-${from}`}>{children}</strong>
        ) : (
          <code key={`code-${from}`}>{children}</code>
        ),
      );
      nodes.push(...remaining(from, to));
      cursor = to;
    }
    nodes.push(...plain(cursor, end, true, breadth && cursor === start));
    return nodes;
  };
  const lines: { text: string; start: number; end: number; after: number }[] =
    [];
  let offset = 0;
  for (const row of value.split("\n").slice(0, 5000)) {
    const body = row.replace(/\r$/, "");
    lines.push({
      text: body,
      start: offset,
      end: offset + body.length,
      after: offset + row.length + 1,
    });
    offset += row.length + 1;
  }
  const divider = (row: string) =>
    /^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(row);
  const heading = (row: string) =>
    /^\s{0,3}(#{1,6})\s+(.+?)(?:\s+#+)?\s*$/.exec(row);
  const list = (row: string) => /^(\s*(?:[-+*]|\d+[.)])\s+)(.*)$/.exec(row);
  const tableAt = (index: number) =>
    lines[index]?.text.includes("|") &&
    index + 1 < lines.length &&
    divider(lines[index + 1].text);
  const special = (index: number) =>
    !!heading(lines[index].text) ||
    !!list(lines[index].text) ||
    /^\s*```/.test(lines[index].text) ||
    tableAt(index);
  const cells = (line: (typeof lines)[number]) => {
    const parts: { start: number; end: number }[] = [];
    let from = 0;
    for (let i = 0; i < line.text.length; i++)
      if (line.text[i] === "|" && line.text[i - 1] !== "\\") {
        parts.push({ start: from, end: i });
        from = i + 1;
      }
    parts.push({ start: from, end: line.text.length });
    if (/^\s*\|/.test(line.text)) parts.shift();
    if (/\|\s*$/.test(line.text)) parts.pop();
    return parts.slice(0, 12).map((part) => {
      while (part.start < part.end && /\s/.test(line.text[part.start]))
        part.start++;
      while (part.end > part.start && /\s/.test(line.text[part.end - 1]))
        part.end--;
      return { start: line.start + part.start, end: line.start + part.end };
    });
  };
  const blocks: ReactNode[] = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (!line.text.trim()) {
      blocks.push(...remaining(line.start, line.after));
      index++;
      continue;
    }
    if (tableAt(index)) {
      const headers = cells(line),
        rows: (typeof lines)[number][] = [];
      const separator = cells(lines[index + 1]);
      index += 2;
      while (
        index < lines.length &&
        lines[index].text.trim() &&
        lines[index].text.includes("|") &&
        rows.length < 50
      )
        rows.push(lines[index++]);
      const end = rows.at(-1)?.after || lines[index - 1].after;
      blocks.push(
        <div className="ai-markdown-table-wrap" key={`table-${line.start}`}>
          <table className="ai-markdown-table">
            <thead>
              <tr>
                {headers.map((cell, n) => (
                  <th key={n}>{inline(cell.start, cell.end)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const rowCells = cells(row);
                const label = rowCells[0];
                const breadth =
                  label &&
                  breadthLabel.test(
                    value
                      .slice(label.start, label.end)
                      .replace(/[ \t*_]/g, "")
                      .replaceAll("／", "/"),
                  );
                return (
                  <tr key={row.start}>
                    {rowCells.map((cell, n) => {
                      const spec = separator[n]
                        ? value.slice(separator[n].start, separator[n].end)
                        : "";
                      const align =
                        spec.startsWith(":") && spec.endsWith(":")
                          ? "center"
                          : spec.endsWith(":")
                            ? "right"
                            : "left";
                      return (
                        <td key={n} style={{ textAlign: align }}>
                          {inline(cell.start, cell.end, breadth && n === 1)}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
          {remaining(line.start, end)}
        </div>,
      );
      continue;
    }
    const title = heading(line.text);
    if (title) {
      const from = line.start + line.text.indexOf(title[2], title[1].length);
      blocks.push(
        <h4
          className="ai-markdown-heading"
          data-level={title[1].length}
          key={line.start}
        >
          {inline(from, from + title[2].length)}
          {remaining(line.start, line.after)}
        </h4>,
      );
      index++;
      continue;
    }
    if (/^\s*```/.test(line.text)) {
      const start = line.after;
      index++;
      let end = start;
      while (index < lines.length && !/^\s*```/.test(lines[index].text))
        end = lines[index++].end;
      if (index < lines.length) index++;
      blocks.push(
        <pre className="ai-markdown-code" key={line.start}>
          <code>{plain(start, end, false)}</code>
          {remaining(line.start, lines[index - 1]?.after || end)}
        </pre>,
      );
      continue;
    }
    const item = list(line.text);
    if (item) {
      const ordered = /^\s*\d/.test(line.text);
      const items: ReactNode[] = [];
      while (index < lines.length) {
        const current = lines[index],
          match = list(current.text);
        if (!match || /^\s*\d/.test(current.text) !== ordered) break;
        const number = ordered ? Number.parseInt(match[1], 10) : undefined;
        items.push(
          <li
            key={current.start}
            value={Number.isSafeInteger(number) ? number : undefined}
          >
            {inline(current.start + match[1].length, current.end)}
            {remaining(current.start, current.after)}
          </li>,
        );
        index++;
      }
      blocks.push(
        ordered ? (
          <ol key={line.start}>{items}</ol>
        ) : (
          <ul key={line.start}>{items}</ul>
        ),
      );
      continue;
    }
    let end = line.end,
      after = line.after;
    index++;
    while (
      index < lines.length &&
      lines[index].text.trim() &&
      !special(index)
    ) {
      end = lines[index].end;
      after = lines[index].after;
      index++;
    }
    blocks.push(
      <p className="ai-message-text" key={line.start}>
        {inline(line.start, end)}
        {remaining(line.start, after)}
      </p>,
    );
  }
  blocks.push(...remaining(0, value.length));
  return <div className="ai-safe-markdown">{blocks}</div>;
}

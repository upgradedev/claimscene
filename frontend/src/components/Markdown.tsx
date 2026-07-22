import { Fragment, type ReactNode } from "react";

/** A deliberately tiny, SAFE markdown renderer for the deterministic incident
 *  report. It builds React elements (so every text node is escaped by React —
 *  never dangerouslySetInnerHTML) and handles only the constructs the report
 *  emits: #/##/### headings, > blockquote, - lists, --- rules, **bold** inline,
 *  and paragraphs. Unknown syntax renders as plain text. */
function inline(text: string, keyBase: string): ReactNode[] {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).filter(Boolean);
  return parts.map((p, i) => {
    if (p.startsWith("**") && p.endsWith("**")) {
      return (
        <strong key={`${keyBase}-${i}`} className="font-semibold text-blueprint-text">
          {p.slice(2, -2)}
        </strong>
      );
    }
    if (p.startsWith("`") && p.endsWith("`")) {
      return (
        <code key={`${keyBase}-${i}`} className="rounded bg-steel-800 px-1 py-0.5 font-mono text-[0.85em] text-cyan-200">
          {p.slice(1, -1)}
        </code>
      );
    }
    return <Fragment key={`${keyBase}-${i}`}>{p}</Fragment>;
  });
}

export function Markdown({ source }: { source: string }) {
  const lines = source.replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let list: string[] | null = null;
  let key = 0;

  const flushList = () => {
    if (list) {
      const items = list;
      blocks.push(
        <ul key={`ul-${key++}`} className="my-2 space-y-1 pl-4">
          {items.map((li, i) => (
            <li key={i} className="list-disc text-sm text-blueprint-text/90">
              {inline(li, `li-${i}`)}
            </li>
          ))}
        </ul>,
      );
      list = null;
    }
  };

  for (const raw of lines) {
    const line = raw.trimEnd();
    if (line.startsWith("- ")) {
      (list ??= []).push(line.slice(2));
      continue;
    }
    flushList();
    if (!line.trim()) continue;
    if (line.startsWith("### ")) {
      blocks.push(<h4 key={key++} className="mt-3 font-mono text-sm font-semibold text-cyan-200">{inline(line.slice(4), "h4")}</h4>);
    } else if (line.startsWith("## ")) {
      blocks.push(<h3 key={key++} className="mt-4 font-mono text-base font-semibold text-blueprint-text">{inline(line.slice(3), "h3")}</h3>);
    } else if (line.startsWith("# ")) {
      blocks.push(<h2 key={key++} className="font-mono text-lg font-semibold text-blueprint-text">{inline(line.slice(2), "h2")}</h2>);
    } else if (line.startsWith("> ")) {
      blocks.push(
        <blockquote key={key++} className="my-2 border-l-2 border-amber-400/50 bg-amber-400/[0.05] py-1 pl-3 text-xs text-amber-200">
          {inline(line.slice(2), "bq")}
        </blockquote>,
      );
    } else if (line.startsWith("---")) {
      blocks.push(<hr key={key++} className="my-3 border-steel-700" />);
    } else {
      blocks.push(<p key={key++} className="text-sm text-blueprint-text/90">{inline(line, "p")}</p>);
    }
  }
  flushList();

  return <div className="space-y-1">{blocks}</div>;
}

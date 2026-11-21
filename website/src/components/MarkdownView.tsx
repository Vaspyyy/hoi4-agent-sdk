import { useEffect, useMemo, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { marked, Renderer } from "marked";
import { highlightCode } from "../lib/highlight";

function slugify(text: string): string {
  return (
    text
      .replace(/<[^>]+>/g, "")
      .replace(/&[a-z#0-9]+;/gi, "")
      .replace(/[^a-zA-Z0-9\s-]/g, "")
      .trim()
      .toLowerCase()
      .replace(/[\s_-]+/g, "-") || "section"
  );
}

interface MarkdownViewProps {
  markdown: string;
}

export function MarkdownView({ markdown }: MarkdownViewProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();

  const html = useMemo(() => {
    const seen = new Map<string, number>();
    const renderer = new Renderer();

    renderer.heading = function ({ tokens, depth }) {
      const inner = this.parser.parseInline(tokens);
      const base = slugify(inner);
      const count = seen.get(base) || 0;
      seen.set(base, count + 1);
      const id = count === 0 ? base : `${base}-${count}`;
      const anchor =
        depth <= 3
          ? `<a class="md-heading-anchor" href="#${id}" aria-label="Link to section">#</a>`
          : "";
      return `<h${depth} id="${id}">${inner}${anchor}</h${depth}>\n`;
    };

    renderer.code = function ({ text, lang }) {
      const language = (lang || "text").split(/\s+/)[0];
      const highlighted = highlightCode(text, language);
      const encoded = encodeURIComponent(text);
      const lineCount = text.replace(/\n$/, "").split("\n").length;
      return `
        <div class="code-block">
          <div class="code-block-header">
            <div class="code-block-meta">
              <span class="code-lang">${language}</span>
              <span style="opacity:0.5">${lineCount}L</span>
            </div>
            <button type="button" class="code-copy-btn" data-copy="${encoded}">Copy</button>
          </div>
          <pre><code>${highlighted}</code></pre>
        </div>
      `;
    };

    renderer.table = function (token) {
      const headerCells = token.header
        .map((cell) => `<th>${this.parser.parseInline(cell.tokens)}</th>`)
        .join("");
      const bodyRows = token.rows
        .map(
          (row) =>
            `<tr>${row
              .map((cell) => `<td>${this.parser.parseInline(cell.tokens)}</td>`)
              .join("")}</tr>`
        )
        .join("");
      return `<div class="md-table-wrap"><table><thead><tr>${headerCells}</tr></thead><tbody>${bodyRows}</tbody></table></div>`;
    };

    return marked.parse(markdown, {
      renderer,
      gfm: true,
      breaks: false,
    }) as string;
  }, [markdown]);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const handleClick = (event: MouseEvent) => {
      const target = event.target as HTMLElement | null;
      if (!target) return;

      const copyBtn = target.closest<HTMLButtonElement>("button[data-copy]");
      if (copyBtn) {
        const raw = decodeURIComponent(copyBtn.dataset.copy || "");
        navigator.clipboard?.writeText(raw).then(() => {
          copyBtn.textContent = "Copied";
          setTimeout(() => {
            copyBtn.textContent = "Copy";
          }, 1600);
        });
        return;
      }

      const link = target.closest<HTMLAnchorElement>("a[href]");
      if (link) {
        const href = link.getAttribute("href") || "";
        if (href.startsWith("/")) {
          event.preventDefault();
          navigate(href);
        }
      }
    };

    el.addEventListener("click", handleClick);
    return () => el.removeEventListener("click", handleClick);
  }, [navigate]);

  return (
    <div
      ref={containerRef}
      className="md-prose"
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}

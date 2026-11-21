import { useState } from "react";
import { Check, Copy } from "lucide-react";
import { highlightCode } from "../lib/highlight";

interface CodeBlockProps {
  code: string;
  language?: string;
  title?: string;
  showHeader?: boolean;
}

export function CodeBlock({
  code,
  language = "python",
  title,
  showHeader = true,
}: CodeBlockProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(code.trim());
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      // Fallback ignored in restricted clipboard contexts
    }
  };

  const html = highlightCode(code, language);
  const lineCount = code.replace(/\n$/, "").split("\n").length;

  return (
    <div className="code-block">
      {showHeader && (
        <div className="code-block-header">
          <div className="code-block-meta">
            <span className="code-lang">{title || language || "code"}</span>
            {title && language && (
              <span style={{ opacity: 0.65 }}>{language}</span>
            )}
            <span style={{ opacity: 0.5 }}>{lineCount}L</span>
          </div>
          <button
            type="button"
            className="code-copy-btn"
            onClick={handleCopy}
            aria-label="Copy code"
          >
            {copied ? <Check size={12} /> : <Copy size={12} />}
            {copied ? "Copied" : "Copy"}
          </button>
        </div>
      )}
      <pre>
        <code dangerouslySetInnerHTML={{ __html: html }} />
      </pre>
    </div>
  );
}

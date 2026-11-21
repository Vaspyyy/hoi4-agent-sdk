function escapeHtml(input: string): string {
  return input
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

const PY_KEYWORDS = new Set([
  "from", "import", "as", "def", "class", "return", "with", "if", "elif", "else",
  "for", "in", "while", "try", "except", "finally", "raise", "pass", "break",
  "continue", "and", "or", "not", "is", "lambda", "yield", "async", "await",
  "assert", "del", "global", "nonlocal", "True", "False", "None",
]);

const BASH_KEYWORDS = new Set([
  "cd", "mkdir", "export", "python", "python3", "pip", "uv", "pytest", "ruff",
  "mypy", "git", "gh", "npm", "npx", "echo", "if", "then", "fi", "for", "do", "done",
]);

function highlightPythonLine(line: string): string {
  let out = "";
  let i = 0;
  const n = line.length;

  while (i < n) {
    const ch = line[i];

    // Comment
    if (ch === "#") {
      out += `<span class="tok-comment">${escapeHtml(line.slice(i))}</span>`;
      break;
    }

    // String literals (single/double/triple + optional prefix)
    if (
      (ch === '"' || ch === "'") ||
      ((ch === "f" || ch === "r" || ch === "b" || ch === "u") &&
        (line[i + 1] === '"' || line[i + 1] === "'"))
    ) {
      let start = i;
      if (ch !== '"' && ch !== "'") i++;
      const quote = line[i];
      const triple = line.slice(i, i + 3) === quote.repeat(3);
      i += triple ? 3 : 1;
      while (i < n) {
        if (line[i] === "\\") {
          i += 2;
          continue;
        }
        if (triple && line.slice(i, i + 3) === quote.repeat(3)) {
          i += 3;
          break;
        }
        if (!triple && line[i] === quote) {
          i += 1;
          break;
        }
        i++;
      }
      out += `<span class="tok-string">${escapeHtml(line.slice(start, i))}</span>`;
      continue;
    }

    // Numbers
    if (/[0-9]/.test(ch) && (i === 0 || !/[A-Za-z0-9_]/.test(line[i - 1]))) {
      const m = line.slice(i).match(/^\d+(?:\.\d+)?/);
      if (m) {
        out += `<span class="tok-number">${escapeHtml(m[0])}</span>`;
        i += m[0].length;
        continue;
      }
    }

    // Identifiers / keywords / types / function calls
    if (/[A-Za-z_]/.test(ch)) {
      const m = line.slice(i).match(/^[A-Za-z_][A-Za-z0-9_]*/);
      if (m) {
        const word = m[0];
        const nextChar = line[i + word.length];
        if (PY_KEYWORDS.has(word)) {
          out += `<span class="tok-keyword">${escapeHtml(word)}</span>`;
        } else if (/^[A-Z][A-Za-z0-9_]*$/.test(word)) {
          out += `<span class="tok-type">${escapeHtml(word)}</span>`;
        } else if (nextChar === "(") {
          out += `<span class="tok-func">${escapeHtml(word)}</span>`;
        } else {
          out += escapeHtml(word);
        }
        i += word.length;
        continue;
      }
    }

    out += escapeHtml(ch);
    i++;
  }

  return out;
}

function highlightParadoxLine(line: string): string {
  let out = "";
  let i = 0;
  const n = line.length;

  while (i < n) {
    const ch = line[i];

    if (ch === "#") {
      out += `<span class="tok-comment">${escapeHtml(line.slice(i))}</span>`;
      break;
    }

    if (ch === '"') {
      const start = i;
      i++;
      while (i < n) {
        if (line[i] === "\\") {
          i += 2;
          continue;
        }
        if (line[i] === '"') {
          i++;
          break;
        }
        i++;
      }
      out += `<span class="tok-string">${escapeHtml(line.slice(start, i))}</span>`;
      continue;
    }

    if (ch === "@") {
      const m = line.slice(i).match(/^@(?:[A-Za-z0-9_.\-]+|\[[^\]]*\])/);
      if (m) {
        out += `<span class="tok-var">${escapeHtml(m[0])}</span>`;
        i += m[0].length;
        continue;
      }
    }

    if (/[0-9]/.test(ch) && (i === 0 || !/[A-Za-z0-9_]/.test(line[i - 1]))) {
      const m = line.slice(i).match(/^-?\d+(?:\.\d+)?/);
      if (m) {
        out += `<span class="tok-number">${escapeHtml(m[0])}</span>`;
        i += m[0].length;
        continue;
      }
    }

    if (/[A-Za-z_]/.test(ch)) {
      const m = line.slice(i).match(/^[A-Za-z_][A-Za-z0-9_.:-]*/);
      if (m) {
        const word = m[0];
        const rest = line.slice(i + word.length);
        if (word === "yes" || word === "no" || word === "rgb" || word === "hsv" || word === "HSV") {
          out += `<span class="tok-keyword">${escapeHtml(word)}</span>`;
        } else if (/^\s*(?:==|!=|>=|<=|=|>|<)/.test(rest)) {
          out += `<span class="tok-func">${escapeHtml(word)}</span>`;
        } else if (/^[A-Z0-9_]{3,}$/.test(word)) {
          out += `<span class="tok-type">${escapeHtml(word)}</span>`;
        } else {
          out += escapeHtml(word);
        }
        i += word.length;
        continue;
      }
    }

    if (ch === "{" || ch === "}" || ch === "=" || ch === "<" || ch === ">") {
      out += `<span class="tok-op">${escapeHtml(ch)}</span>`;
      i++;
      continue;
    }

    out += escapeHtml(ch);
    i++;
  }

  return out;
}

function highlightJsonLine(line: string): string {
  return escapeHtml(line)
    .replace(/(&quot;[^&]*?&quot;)(\s*:)/g, '<span class="tok-func">$1</span>$2')
    .replace(/:\s*(&quot;[^&]*?&quot;)/g, ': <span class="tok-string">$1</span>')
    .replace(/:\s*(\d+(?:\.\d+)?)/g, ': <span class="tok-number">$1</span>')
    .replace(/:\s*(true|false|null)\b/g, ': <span class="tok-keyword">$1</span>');
}

function highlightBashLine(line: string): string {
  if (line.trimStart().startsWith("#")) {
    return `<span class="tok-comment">${escapeHtml(line)}</span>`;
  }
  return line
    .split(/(\s+)/)
    .map((part) => {
      if (BASH_KEYWORDS.has(part)) {
        return `<span class="tok-keyword">${escapeHtml(part)}</span>`;
      }
      if (part.startsWith("--") || part.startsWith("-")) {
        return `<span class="tok-func">${escapeHtml(part)}</span>`;
      }
      if (part.startsWith('"') || part.startsWith("'")) {
        return `<span class="tok-string">${escapeHtml(part)}</span>`;
      }
      if (/^[A-Z_][A-Z0-9_]*=/.test(part)) {
        const [k, ...rest] = part.split("=");
        return `<span class="tok-var">${escapeHtml(k)}</span>=${escapeHtml(rest.join("="))}`;
      }
      return escapeHtml(part);
    })
    .join("");
}

function highlightDiff(code: string): string {
  return code
    .split("\n")
    .map((line) => {
      if (line.startsWith("+") && !line.startsWith("+++")) {
        return `<span class="tok-diff-add">${escapeHtml(line)}</span>`;
      }
      if (line.startsWith("-") && !line.startsWith("---")) {
        return `<span class="tok-diff-del">${escapeHtml(line)}</span>`;
      }
      if (line.startsWith("@@") || line.startsWith("---") || line.startsWith("+++")) {
        return `<span class="tok-diff-hunk">${escapeHtml(line)}</span>`;
      }
      return escapeHtml(line);
    })
    .join("\n");
}

export function highlightCode(code: string, lang?: string): string {
  const normalized = (lang || "").toLowerCase().trim();
  const trimmed = code.replace(/\n$/, "");

  if (normalized === "diff") {
    return highlightDiff(trimmed);
  }
  if (normalized === "python" || normalized === "py") {
    return trimmed.split("\n").map(highlightPythonLine).join("\n");
  }
  if (normalized === "json") {
    return trimmed.split("\n").map(highlightJsonLine).join("\n");
  }
  if (normalized === "bash" || normalized === "sh" || normalized === "shell") {
    return trimmed.split("\n").map(highlightBashLine).join("\n");
  }
  if (
    normalized === "pdx" ||
    normalized === "paradox" ||
    normalized === "hoi4" ||
    normalized === "text" ||
    normalized === "txt"
  ) {
    return trimmed.split("\n").map(highlightParadoxLine).join("\n");
  }

  return escapeHtml(trimmed);
}

import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Search, ArrowRight, X } from "lucide-react";
import searchData from "../generated/search-index.json";
import type { SearchItem } from "../types/sdk";

const ITEMS = searchData as SearchItem[];
const KINDS = ["All", "Doc", "Section", "Method", "Layer", "Definition", "Validation", "Release"] as const;

interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
}

export function CommandPalette({ open, onClose }: CommandPaletteProps) {
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState<(typeof KINDS)[number]>("All");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  useEffect(() => {
    if (open) {
      setQuery("");
      setKind("All");
      setSelectedIndex(0);
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  const results = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = ITEMS.filter((item) => {
      if (kind !== "All" && item.kind !== kind) return false;
      if (!q) return true;
      return (
        item.title.toLowerCase().includes(q) ||
        item.subtitle.toLowerCase().includes(q) ||
        item.keywords.toLowerCase().includes(q)
      );
    });

    if (!q) {
      // Curated starter set when empty
      return filtered.slice(0, 18);
    }

    // Score exact prefix matches higher
    return filtered
      .sort((a, b) => {
        const aTitle = a.title.toLowerCase();
        const bTitle = b.title.toLowerCase();
        const aExact = aTitle.startsWith(q) ? 0 : aTitle.includes(q) ? 1 : 2;
        const bExact = bTitle.startsWith(q) ? 0 : bTitle.includes(q) ? 1 : 2;
        return aExact - bExact;
      })
      .slice(0, 40);
  }, [query, kind]);

  useEffect(() => {
    setSelectedIndex(0);
  }, [query, kind]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
      } else if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedIndex((i) => Math.min(results.length - 1, i + 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelectedIndex((i) => Math.max(0, i - 1));
      } else if (e.key === "Enter" && results[selectedIndex]) {
        e.preventDefault();
        navigate(results[selectedIndex].href);
        onClose();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, results, selectedIndex, navigate, onClose]);

  if (!open) return null;

  return (
    <div className="cmd-backdrop" onClick={onClose} role="dialog" aria-modal="true" aria-label="Search SDK">
      <div className="cmd-modal" onClick={(e) => e.stopPropagation()}>
        <div className="cmd-input-row">
          <Search size={16} color="var(--ink-muted)" />
          <input
            ref={inputRef}
            className="cmd-input"
            type="text"
            placeholder="Search docs, 257 Mod methods, 30 layers, 163 validation codes, releases…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close search">
            <X size={15} />
          </button>
        </div>

        <div className="cmd-filters">
          {KINDS.map((k) => (
            <button
              key={k}
              type="button"
              className={`cmd-filter-btn ${kind === k ? "active" : ""}`}
              onClick={() => setKind(k)}
            >
              {k}
            </button>
          ))}
        </div>

        <div className="cmd-results">
          {results.length === 0 ? (
            <div style={{ padding: "28px 16px", textAlign: "center", color: "var(--ink-muted)", fontSize: 13.5 }}>
              No matching documentation, methods, or codes for “{query}”.
            </div>
          ) : (
            results.map((item, idx) => (
              <button
                key={item.id}
                type="button"
                className={`cmd-item ${idx === selectedIndex ? "selected" : ""}`}
                onMouseEnter={() => setSelectedIndex(idx)}
                onClick={() => {
                  navigate(item.href);
                  onClose();
                }}
              >
                <div style={{ minWidth: 0 }}>
                  <div className="cmd-item-title">
                    <span className="mono">{item.title}</span>
                  </div>
                  <div className="cmd-item-sub">{item.subtitle}</div>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
                  <span className="tag">{item.kind}</span>
                  <ArrowRight size={13} color="var(--ink-muted)" />
                </div>
              </button>
            ))
          )}
        </div>

        <div className="cmd-footer">
          <span>{results.length} shown · {ITEMS.length} indexed symbols &amp; sections</span>
          <span>↑↓ navigate · ↵ open · esc close</span>
        </div>
      </div>
    </div>
  );
}

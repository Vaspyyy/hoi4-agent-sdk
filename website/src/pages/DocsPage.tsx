import { useEffect, useMemo, useState } from "react";
import { Link, Navigate, useLocation, useParams } from "react-router-dom";
import { ArrowLeft, ArrowRight, FileCode2 } from "lucide-react";
import docsJson from "../generated/docs.json";
import type { DocPage } from "../types/sdk";
import { MarkdownView } from "../components/MarkdownView";

const DOCS = docsJson as DocPage[];
const GROUPS = ["Overview", "Guides", "Reference", "Internals", "Project"];

export function DocsPage() {
  const { slug } = useParams<{ slug?: string }>();
  const location = useLocation();
  const [tocFilter, setTocFilter] = useState("");
  const [activeHeading, setActiveHeading] = useState("");

  const currentSlug = slug || "getting-started";
  const docIndex = DOCS.findIndex((d) => d.slug === currentSlug);
  const doc = docIndex >= 0 ? DOCS[docIndex] : null;

  useEffect(() => {
    setTocFilter("");
  }, [currentSlug]);

  useEffect(() => {
    if (location.hash) {
      const targetId = location.hash.slice(1);
      requestAnimationFrame(() => {
        const el = document.getElementById(targetId);
        if (el) el.scrollIntoView({ behavior: "smooth" });
      });
    } else {
      window.scrollTo({ top: 0 });
    }
  }, [currentSlug, location.hash]);

  useEffect(() => {
    if (!doc || doc.headings.length === 0) return;
    const ids = doc.headings.map((h) => h.id);
    const elements = ids
      .map((id) => document.getElementById(id))
      .filter((el): el is HTMLElement => Boolean(el));

    if (elements.length === 0) return;

    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (visible[0]?.target.id) {
          setActiveHeading(visible[0].target.id);
        }
      },
      { rootMargin: "-80px 0px -70% 0px", threshold: 0 }
    );

    elements.forEach((el) => observer.observe(el));
    return () => observer.disconnect();
  }, [doc]);

  const filteredHeadings = useMemo(() => {
    if (!doc) return [];
    const q = tocFilter.trim().toLowerCase();
    if (!q) return doc.headings;
    return doc.headings.filter((h) => h.title.toLowerCase().includes(q));
  }, [doc, tocFilter]);

  if (!doc) {
    return <Navigate to="/docs/getting-started" replace />;
  }

  const prevDoc = docIndex > 0 ? DOCS[docIndex - 1] : null;
  const nextDoc = docIndex < DOCS.length - 1 ? DOCS[docIndex + 1] : null;

  return (
    <div className="container">
      <div className="docs-shell">
        {/* Left Sidebar */}
        <aside className="docs-sidebar" aria-label="Documentation navigation">
          {GROUPS.map((group) => {
            const items = DOCS.filter((d) => d.group === group);
            if (items.length === 0) return null;
            return (
              <div key={group} className="docs-group">
                <div className="docs-group-title">{group}</div>
                {items.map((item) => (
                  <Link
                    key={item.slug}
                    to={`/docs/${item.slug}`}
                    className={`docs-nav-item ${item.slug === doc.slug ? "active" : ""}`}
                  >
                    <span>{item.title}</span>
                    <span className="mono" style={{ fontSize: 10.5, opacity: 0.7 }}>
                      {item.readingMinutes}m
                    </span>
                  </Link>
                ))}
              </div>
            );
          })}
        </aside>

        {/* Main Article */}
        <article className="docs-article">
          <header className="docs-article-header">
            <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8, marginBottom: 10 }}>
              <span className="eyebrow">{doc.group}</span>
              <span className="tag">{doc.badge}</span>
              <span className="tag" style={{ gap: 5 }}>
                <FileCode2 size={11} />
                {doc.sourcePath}
              </span>
            </div>

            <h1
              className="serif"
              style={{
                fontSize: "clamp(1.95rem, 3.3vw, 2.6rem)",
                fontWeight: 500,
                lineHeight: 1.12,
                letterSpacing: "-0.02em",
                margin: "6px 0 12px",
              }}
            >
              {doc.title}
            </h1>

            <p style={{ margin: "0 0 14px", color: "var(--ink-secondary)", fontSize: 15 }}>
              {doc.summary}
            </p>

            <div className="mono" style={{ fontSize: 11.5, color: "var(--ink-muted)", display: "flex", flexWrap: "wrap", gap: 14 }}>
              <span>{doc.lines.toLocaleString()} source lines</span>
              <span>{doc.words.toLocaleString()} words</span>
              <span>~{doc.readingMinutes} min read</span>
              <span>{doc.headings.length} sections</span>
            </div>
          </header>

          <MarkdownView markdown={doc.markdown} />

          <nav className="docs-pager" aria-label="Previous and next documentation pages">
            {prevDoc ? (
              <Link to={`/docs/${prevDoc.slug}`} className="docs-pager-card">
                <span className="mono" style={{ fontSize: 11, color: "var(--ink-muted)", display: "inline-flex", alignItems: "center", gap: 5 }}>
                  <ArrowLeft size={12} /> Previous
                </span>
                <span style={{ fontWeight: 600, fontSize: 14 }}>{prevDoc.title}</span>
              </Link>
            ) : (
              <div />
            )}
            {nextDoc ? (
              <Link to={`/docs/${nextDoc.slug}`} className="docs-pager-card" style={{ textAlign: "right", alignItems: "flex-end" }}>
                <span className="mono" style={{ fontSize: 11, color: "var(--ink-muted)", display: "inline-flex", alignItems: "center", gap: 5 }}>
                  Next <ArrowRight size={12} />
                </span>
                <span style={{ fontWeight: 600, fontSize: 14 }}>{nextDoc.title}</span>
              </Link>
            ) : (
              <div />
            )}
          </nav>
        </article>

        {/* Right Sticky TOC */}
        <aside className="docs-toc" aria-label="On this page">
          <div className="docs-toc-title">On this page ({doc.headings.length})</div>
          {doc.headings.length > 12 && (
            <input
              type="text"
              value={tocFilter}
              onChange={(e) => setTocFilter(e.target.value)}
              placeholder="Filter sections…"
              style={{
                width: "100%",
                height: 30,
                padding: "0 8px",
                marginBottom: 10,
                fontSize: 12,
                border: "1px solid var(--border-hairline)",
                background: "var(--bg-elevated)",
                borderRadius: "var(--radius-xs)",
              }}
            />
          )}
          <div>
            {filteredHeadings.map((h) => (
              <a
                key={h.id}
                href={`#${h.id}`}
                className={`docs-toc-link lvl-${h.level} ${activeHeading === h.id ? "active" : ""}`}
              >
                {h.title}
              </a>
            ))}
          </div>
        </aside>
      </div>
    </div>
  );
}

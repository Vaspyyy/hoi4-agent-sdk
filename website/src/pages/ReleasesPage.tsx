import { useEffect, useMemo, useState } from "react";
import { useLocation } from "react-router-dom";
import releasesJson from "../generated/releases.json";
import type { ReleaseEntry } from "../types/sdk";

const RELEASES = releasesJson as ReleaseEntry[];

const SECTION_FILTERS = [
  "All",
  "Added",
  "Changed",
  "Fixed",
  "Migration",
  "Removed",
  "Known audit status",
] as const;

function renderInlineMarkdown(text: string): string {
  const escaped = text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
  return escaped
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
}

function sectionBadgeClass(title: string): string {
  const t = title.toLowerCase();
  if (t.includes("added")) return "tag-green";
  if (t.includes("changed")) return "tag-blue";
  if (t.includes("fixed")) return "tag-amber";
  if (t.includes("migration")) return "tag-accent";
  return "";
}

export function ReleasesPage() {
  const location = useLocation();
  const [query, setQuery] = useState("");
  const [sectionFilter, setSectionFilter] = useState<(typeof SECTION_FILTERS)[number]>("All");

  useEffect(() => {
    if (location.hash) {
      const id = location.hash.slice(1);
      requestAnimationFrame(() => {
        document.getElementById(id)?.scrollIntoView({ behavior: "smooth" });
      });
    } else {
      window.scrollTo({ top: 0 });
    }
  }, [location.hash]);

  const filteredReleases = useMemo(() => {
    const q = query.trim().toLowerCase();
    return RELEASES.map((rel) => {
      const sections = rel.sections
        .filter((sec) => {
          if (sectionFilter !== "All" && sec.title.toLowerCase() !== sectionFilter.toLowerCase()) {
            return false;
          }
          return true;
        })
        .map((sec) => ({
          ...sec,
          items: !q
            ? sec.items
            : sec.items.filter(
                (it) =>
                  it.toLowerCase().includes(q) ||
                  rel.version.toLowerCase().includes(q) ||
                  sec.title.toLowerCase().includes(q)
              ),
        }))
        .filter((sec) => sec.items.length > 0);

      return { ...rel, sections };
    }).filter((rel) => rel.sections.length > 0);
  }, [query, sectionFilter]);

  const totalEntries = useMemo(
    () => RELEASES.reduce((acc, r) => acc + r.totalItems, 0),
    []
  );

  return (
    <div className="container" style={{ paddingTop: 40 }}>
      <header style={{ paddingBottom: 28, borderBottom: "1px solid var(--border-hairline)" }}>
        <span className="eyebrow">Changelog &amp; Release Notes</span>
        <h1 className="section-heading">Every version, migration note, and audit finding</h1>
        <p className="section-lead" style={{ marginBottom: 20 }}>
          Parsed directly from <code>CHANGELOG.md</code> ({RELEASES.length} releases ·{" "}
          {totalEntries} documented entries). Follows Keep a Changelog and Semantic Versioning.
        </p>

        <div className="ref-toolbar" style={{ marginBottom: 0 }}>
          <input
            type="text"
            className="ref-search"
            style={{ maxWidth: 420 }}
            placeholder="Search release notes (e.g. syntax, OOB, Man the Guns, BOM, Commons…)"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {SECTION_FILTERS.map((f) => (
              <button
                key={f}
                type="button"
                className={`cmd-filter-btn ${sectionFilter === f ? "active" : ""}`}
                onClick={() => setSectionFilter(f)}
              >
                {f}
              </button>
            ))}
          </div>
        </div>
      </header>

      <div className="releases-layout">
        {/* Sticky timeline rail */}
        <aside className="releases-rail" aria-label="Version timeline">
          <div className="docs-group-title" style={{ paddingLeft: 8 }}>Versions</div>
          {RELEASES.map((rel) => (
            <a key={rel.id} href={`#${rel.id}`} className="release-rail-item">
              <span style={{ fontWeight: rel.isUnreleased ? 600 : 500, color: rel.isUnreleased ? "var(--accent)" : undefined }}>
                {rel.isUnreleased ? "Unreleased" : `v${rel.version}`}
              </span>
              <span style={{ fontSize: 10.5, color: "var(--ink-muted)" }}>
                {rel.totalItems}
              </span>
            </a>
          ))}
        </aside>

        {/* Release cards */}
        <div>
          {filteredReleases.length === 0 ? (
            <div className="panel-box" style={{ textAlign: "center", color: "var(--ink-muted)" }}>
              No changelog entries match your filter.
            </div>
          ) : (
            filteredReleases.map((rel) => (
              <section
                key={rel.id}
                id={rel.id}
                className={`release-card ${rel.isUnreleased ? "unreleased" : ""}`}
              >
                <div className="release-card-head">
                  <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                    <h2 className="serif" style={{ margin: 0, fontSize: "1.55rem", fontWeight: 500 }}>
                      {rel.isUnreleased ? "Unreleased (main)" : `v${rel.version}`}
                    </h2>
                    {rel.isUnreleased ? (
                      <span className="tag tag-accent">Upcoming Milestone</span>
                    ) : (
                      <span className="tag">{rel.date}</span>
                    )}
                  </div>
                  <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                    {rel.counts.added > 0 && <span className="tag tag-green">+{rel.counts.added} added</span>}
                    {rel.counts.changed > 0 && <span className="tag tag-blue">~{rel.counts.changed} changed</span>}
                    {rel.counts.fixed > 0 && <span className="tag tag-amber">!{rel.counts.fixed} fixed</span>}
                    {rel.counts.migration > 0 && <span className="tag tag-accent">{rel.counts.migration} migration</span>}
                    {rel.counts.removed > 0 && <span className="tag">-{rel.counts.removed} removed</span>}
                  </div>
                </div>

                <div className="release-card-body">
                  {rel.sections.map((sec) => (
                    <div key={sec.title} style={{ marginBottom: 18 }}>
                      <div className="release-section-title">
                        <span className={`tag ${sectionBadgeClass(sec.title)}`}>{sec.title}</span>
                        <span style={{ color: "var(--ink-muted)", fontSize: 11 }}>
                          {sec.items.length} {sec.items.length === 1 ? "entry" : "entries"}
                        </span>
                      </div>
                      <ul className="release-items">
                        {sec.items.map((item, idx) => (
                          <li
                            key={idx}
                            dangerouslySetInnerHTML={{ __html: renderInlineMarkdown(item) }}
                          />
                        ))}
                      </ul>
                    </div>
                  ))}
                </div>
              </section>
            ))
          )}
        </div>
      </div>
    </div>
  );
}

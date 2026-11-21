import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ArrowRight } from "lucide-react";
import layersJson from "../generated/layers.json";
import type { LayerInfo, LayersPayload } from "../types/sdk";

const { layers: LAYERS } = layersJson as LayersPayload;

const TIER_META: Record<LayerInfo["tier"], { label: string; desc: string }> = {
  foundation: {
    label: "Tier 01 // Foundation, Sources & Core Services",
    desc: "Shared state, file grouping, effective catalogs, localisation tables, and effect builders.",
  },
  domains: {
    label: "Tier 02 // Content Domain Authoring",
    desc: "One isolated module per HOI4 content domain: countries, characters, OOBs, map, focuses, events, decisions, and assets.",
  },
  validation: {
    label: "Tier 03 // Staged Validation & Auditing",
    desc: "Script vocabulary, OOB DLC splits, country package completeness, and release-stage liveness.",
  },
  persistence: {
    label: "Tier 04 // Preview, Transactions & Atomic Persistence",
    desc: "Plain-language change summaries, unified diffs, rollback transactions, and atomic disk writes.",
  },
};

const PIPELINE = [
  {
    step: "01",
    mod: "hoi4.textio",
    title: "Byte-Exact I/O",
    desc: "Line-by-line decoding preserves UTF-8 BOM, Windows-1252, mixed CRLF/LF, and stray bytes.",
  },
  {
    step: "02",
    mod: "hoi4.syntax",
    title: "Lossless CST",
    desc: "Every token, comment, typed block, and @variable retains exact character offsets.",
  },
  {
    step: "03",
    mod: "hoi4.mod.*",
    title: "30 Typed Layers",
    desc: "Domain models mutate in memory; mypy enforces acyclic calls across every layer.",
  },
  {
    step: "04",
    mod: "hoi4.patching",
    title: "sync_body Spans",
    desc: "Replaces only modified statements and scalars; untouched bytes stay bit-identical.",
  },
  {
    step: "05",
    mod: "mod.save()",
    title: "Atomic Commit",
    desc: "Concurrency check, syntax-safety guard, preview diff, and atomic multi-file write.",
  },
];

export function ArchitecturePage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const initialLayer = searchParams.get("layer") || "_validation";
  const [selectedId, setSelectedId] = useState(
    LAYERS.some((l) => l.id === initialLayer) ? initialLayer : "_core"
  );
  const [query, setQuery] = useState("");

  const selectedLayer = useMemo(
    () => LAYERS.find((l) => l.id === selectedId) || LAYERS[0],
    [selectedId]
  );

  const selectLayer = (id: string) => {
    setSelectedId(id);
    setSearchParams({ layer: id }, { replace: true });
  };

  useEffect(() => {
    const param = searchParams.get("layer");
    if (param && LAYERS.some((l) => l.id === param)) {
      setSelectedId(param);
    }
  }, [searchParams]);

  const depSet = useMemo(
    () => new Set(selectedLayer.transitiveBases),
    [selectedLayer]
  );
  const dependentSet = useMemo(
    () => new Set(selectedLayer.dependents),
    [selectedLayer]
  );

  const totalLayerLines = useMemo(
    () => LAYERS.reduce((acc, l) => acc + l.lines, 0),
    []
  );

  const q = query.trim().toLowerCase();

  return (
    <div className="container">
      <header className="arch-header">
        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "flex-end", justifyContent: "space-between", gap: 18 }}>
          <div>
            <span className="eyebrow">Architecture // Acyclic Layer Graph</span>
            <h1 className="section-heading" style={{ marginBottom: 10 }}>
              30 mypy-enforced layers, zero circular calls
            </h1>
            <p className="section-lead">
              <code>hoi4.Mod</code> presents a single ergonomic class to callers, while its{" "}
              {totalLayerLines.toLocaleString()} lines live across {LAYERS.length} focused modules
              in <code>src/hoi4/mod/</code>. Every layer subclasses only the layers it calls—so{" "}
              <code>mypy</code> rejects undeclared cross-domain dependencies at type-check time.
            </p>
          </div>

          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <Link to="/docs/internals-layers" className="btn btn-secondary btn-sm">
              Layering Rules
            </Link>
            <Link to="/docs/internals-parser" className="btn btn-secondary btn-sm">
              Parser Internals
            </Link>
          </div>
        </div>

        {/* 5-Stage Script Pipeline */}
        <div className="pipeline-strip" aria-label="Five-stage source-preserving script pipeline">
          {PIPELINE.map((item) => (
            <div key={item.step} className="pipeline-step">
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
                <span className="mono" style={{ fontSize: 10.5, color: "var(--accent)", fontWeight: 600 }}>
                  STAGE {item.step}
                </span>
                <span className="mono" style={{ fontSize: 10.5, color: "var(--ink-muted)" }}>
                  {item.mod}
                </span>
              </div>
              <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 4 }}>{item.title}</div>
              <div style={{ fontSize: 12.5, color: "var(--ink-secondary)", lineHeight: 1.5 }}>
                {item.desc}
              </div>
            </div>
          ))}
        </div>

        {/* Interactive Legend & Filter */}
        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
          <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8 }}>
            <span className="tag tag-accent">Selected Layer</span>
            <span className="tag tag-blue">Dependencies (Bases)</span>
            <span className="tag tag-green">Direct Dependents (Callers)</span>
          </div>
          <input
            type="text"
            className="ref-search"
            style={{ maxWidth: 300, height: 34 }}
            placeholder="Filter layers or methods…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
      </header>

      <div className="arch-workspace" style={{ marginTop: 26 }}>
        {/* Left: Tiered Layer Grid */}
        <div>
          {(["foundation", "domains", "validation", "persistence"] as const).map((tier) => {
            const groupLayers = LAYERS.filter((l) => {
              if (l.tier !== tier) return false;
              if (!q) return true;
              return (
                l.id.toLowerCase().includes(q) ||
                l.className.toLowerCase().includes(q) ||
                l.description.toLowerCase().includes(q) ||
                l.methods.some((m) => m.name.toLowerCase().includes(q))
              );
            });
            if (groupLayers.length === 0) return null;

            return (
              <section key={tier} className="layer-tier-group">
                <div className="layer-tier-heading">
                  <span>{TIER_META[tier].label}</span>
                  <span>{groupLayers.length} modules</span>
                </div>
                <div className="layer-cards">
                  {groupLayers.map((layer) => {
                    const isSelected = layer.id === selectedLayer.id;
                    const isDep = depSet.has(layer.id);
                    const isDependent = dependentSet.has(layer.id);
                    const isDimmed = !isSelected && !isDep && !isDependent;

                    return (
                      <button
                        key={layer.id}
                        type="button"
                        onClick={() => selectLayer(layer.id)}
                        className={`layer-node ${
                          isSelected
                            ? "selected"
                            : isDep
                            ? "is-dep"
                            : isDependent
                            ? "is-dependent"
                            : isDimmed
                            ? "dimmed"
                            : ""
                        }`}
                      >
                        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 6 }}>
                          <span className="mono" style={{ fontSize: 12.5, fontWeight: 600 }}>
                            {layer.id}
                          </span>
                          <span className="mono" style={{ fontSize: 10.5, color: "var(--ink-muted)" }}>
                            {layer.lines}L
                          </span>
                        </div>
                        <div style={{ fontSize: 11.5, color: "var(--ink-secondary)", marginTop: 4 }}>
                          {layer.methodCount} {layer.methodCount === 1 ? "method" : "methods"}
                        </div>
                      </button>
                    );
                  })}
                </div>
              </section>
            );
          })}
        </div>

        {/* Right: Layer Inspector */}
        <aside className="layer-inspector" aria-label="Selected layer inspector">
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, marginBottom: 6 }}>
            <span className="tag tag-accent">{selectedLayer.tier}</span>
            <span className="mono" style={{ fontSize: 11.5, color: "var(--ink-muted)" }}>
              {selectedLayer.filePath} · {selectedLayer.lines} lines
            </span>
          </div>

          <h2 className="serif" style={{ fontSize: "1.6rem", fontWeight: 500, margin: "6px 0 8px" }}>
            {selectedLayer.className}
          </h2>
          <p style={{ margin: "0 0 18px", fontSize: 13.8, color: "var(--ink-secondary)" }}>
            {selectedLayer.description || "Layer module in hoi4.mod."}
          </p>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 18 }}>
            <div style={{ padding: 12, border: "1px solid var(--border-hairline)", background: "var(--bg-surface)", borderRadius: "var(--radius-xs)" }}>
              <div className="mono" style={{ fontSize: 10.5, textTransform: "uppercase", color: "var(--ink-muted)", marginBottom: 6 }}>
                Direct Bases ({selectedLayer.directBases.length})
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                {selectedLayer.directBases.length === 0 ? (
                  <span className="mono" style={{ fontSize: 11.5, color: "var(--ink-muted)" }}>Root (object)</span>
                ) : (
                  selectedLayer.directBases.map((b) => (
                    <button
                      key={b}
                      type="button"
                      className="tag tag-blue"
                      style={{ cursor: "pointer" }}
                      onClick={() => selectLayer(b)}
                    >
                      {b}
                    </button>
                  ))
                )}
              </div>
            </div>

            <div style={{ padding: 12, border: "1px solid var(--border-hairline)", background: "var(--bg-surface)", borderRadius: "var(--radius-xs)" }}>
              <div className="mono" style={{ fontSize: 10.5, textTransform: "uppercase", color: "var(--ink-muted)", marginBottom: 6 }}>
                Direct Callers ({selectedLayer.dependents.length})
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                {selectedLayer.dependents.length === 0 ? (
                  <span className="mono" style={{ fontSize: 11.5, color: "var(--ink-muted)" }}>Top-level Mod base</span>
                ) : (
                  selectedLayer.dependents.map((d) => (
                    <button
                      key={d}
                      type="button"
                      className="tag tag-green"
                      style={{ cursor: "pointer" }}
                      onClick={() => selectLayer(d)}
                    >
                      {d}
                    </button>
                  ))
                )}
              </div>
            </div>
          </div>

          <div style={{ borderTop: "1px solid var(--border-hairline)", paddingTop: 14 }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
              <span className="mono" style={{ fontSize: 11, textTransform: "uppercase", color: "var(--ink-muted)" }}>
                Public Methods ({selectedLayer.methods.length})
              </span>
              <Link
                to={`/reference?tab=methods&layer=${selectedLayer.id}`}
                className="mono"
                style={{ fontSize: 11.5, color: "var(--accent)", display: "inline-flex", alignItems: "center", gap: 4 }}
              >
                Open in Reference <ArrowRight size={12} />
              </Link>
            </div>

            {selectedLayer.methods.length === 0 ? (
              <p style={{ fontSize: 13, color: "var(--ink-muted)", margin: 0 }}>
                Internal support layer (provides protected helpers and shared state to higher layers).
              </p>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {selectedLayer.methods.map((m) => (
                  <div
                    key={m.name}
                    style={{
                      padding: "9px 11px",
                      border: "1px solid var(--border-hairline)",
                      background: "var(--bg-canvas)",
                      borderRadius: "var(--radius-xs)",
                    }}
                  >
                    <div className="mono" style={{ fontSize: 12, fontWeight: 600, color: "var(--ink-primary)", wordBreak: "break-word" }}>
                      {m.name}()
                    </div>
                    {m.summary && (
                      <div style={{ fontSize: 12.2, color: "var(--ink-secondary)", marginTop: 3 }}>
                        {m.summary}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </aside>
      </div>
    </div>
  );
}

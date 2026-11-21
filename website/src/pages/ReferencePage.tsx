import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Check, Copy, ChevronDown, ChevronRight } from "lucide-react";
import catalogsJson from "../generated/catalogs.json";
import layersJson from "../generated/layers.json";
import type { CatalogsPayload, LayersPayload } from "../types/sdk";
import { CodeBlock } from "../components/CodeBlock";

const CATALOGS = catalogsJson as CatalogsPayload;
const { layers: LAYERS, methods: METHODS } = layersJson as LayersPayload;

type RefTab = "methods" | "definitions" | "validation" | "catalogs" | "tech";

export function ReferencePage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const tabParam = (searchParams.get("tab") as RefTab) || "methods";
  const [tab, setTab] = useState<RefTab>(
    ["methods", "definitions", "validation", "catalogs", "tech"].includes(tabParam)
      ? tabParam
      : "methods"
  );
  const [query, setQuery] = useState(searchParams.get("q") || "");
  const [layerFilter, setLayerFilter] = useState(searchParams.get("layer") || "all");
  const [expandedMethod, setExpandedMethod] = useState<string | null>(null);
  const [selectedDef, setSelectedDef] = useState(CATALOGS.definitions[0]?.name || "scripted_effect");
  const [valSeverity, setValSeverity] = useState<"all" | "error" | "warning">("all");
  const [valCategory, setValCategory] = useState("all");
  const [catalogKind, setCatalogKind] = useState<"effects" | "modifiers">("effects");
  const [selectedTech, setSelectedTech] = useState("infantry_weapons");
  const [copiedKey, setCopiedKey] = useState<string | null>(null);

  const switchTab = (next: RefTab) => {
    setTab(next);
    setSearchParams({ tab: next }, { replace: true });
  };

  useEffect(() => {
    const nextTab = searchParams.get("tab") as RefTab | null;
    if (nextTab && ["methods", "definitions", "validation", "catalogs", "tech"].includes(nextTab)) {
      setTab(nextTab);
    }
    const nextQ = searchParams.get("q");
    if (nextQ !== null) {
      setQuery(nextQ);
    }
    const nextLayer = searchParams.get("layer");
    if (nextLayer !== null) {
      setLayerFilter(nextLayer);
    }
  }, [searchParams]);

  const copyText = async (id: string, value: string) => {
    try {
      await navigator.clipboard.writeText(value);
      setCopiedKey(id);
      setTimeout(() => setCopiedKey(null), 1500);
    } catch {
      // ignore
    }
  };

  const q = query.trim().toLowerCase();

  const filteredMethods = useMemo(() => {
    return METHODS.filter((m) => {
      if (layerFilter !== "all" && m.layer !== layerFilter) return false;
      if (!q) return true;
      return (
        m.name.toLowerCase().includes(q) ||
        m.signature.toLowerCase().includes(q) ||
        m.summary.toLowerCase().includes(q) ||
        m.layer.toLowerCase().includes(q)
      );
    });
  }, [layerFilter, q]);

  const filteredDefinitions = useMemo(() => {
    return CATALOGS.definitions.filter((d) => {
      if (!q) return true;
      return (
        d.name.toLowerCase().includes(q) ||
        d.directory.toLowerCase().includes(q) ||
        d.description.toLowerCase().includes(q) ||
        d.layout.toLowerCase().includes(q)
      );
    });
  }, [q]);

  const valCategories = useMemo(
    () => Array.from(new Set(CATALOGS.validationCodes.map((v) => v.category))).sort(),
    []
  );

  const filteredValidation = useMemo(() => {
    return CATALOGS.validationCodes.filter((v) => {
      if (valSeverity !== "all" && v.severity !== valSeverity) return false;
      if (valCategory !== "all" && v.category !== valCategory) return false;
      if (!q) return true;
      return (
        v.code.toLowerCase().includes(q) ||
        v.description.toLowerCase().includes(q) ||
        v.category.toLowerCase().includes(q)
      );
    });
  }, [valSeverity, valCategory, q]);

  const filteredTechs = useMemo(() => {
    return CATALOGS.technologyCategories.filter((t) =>
      !q ? true : t.toLowerCase().includes(q)
    );
  }, [q]);

  const activeDefinition =
    CATALOGS.definitions.find((d) => d.name === selectedDef) || CATALOGS.definitions[0];

  return (
    <div className="container" style={{ paddingTop: 40, paddingBottom: 72 }}>
      <span className="eyebrow">Interactive Reference Workbench</span>
      <h1 className="section-heading">Live-extracted API, definitions &amp; HOI4 1.19 catalogs</h1>
      <p className="section-lead">
        Every method signature, keyed <code>common/</code> domain, validation rule, modifier, and
        technology category below is generated directly from <code>src/hoi4/</code> at build time.
      </p>

      {/* Tabs */}
      <div className="ref-tabs-bar" role="tablist">
        <button type="button" className={`ref-tab-btn ${tab === "methods" ? "active" : ""}`} onClick={() => switchTab("methods")}>
          Mod Methods <span className="mono" style={{ opacity: 0.72 }}>{METHODS.length}</span>
        </button>
        <button type="button" className={`ref-tab-btn ${tab === "definitions" ? "active" : ""}`} onClick={() => switchTab("definitions")}>
          Common/ Domains <span className="mono" style={{ opacity: 0.72 }}>{CATALOGS.definitions.length}</span>
        </button>
        <button type="button" className={`ref-tab-btn ${tab === "validation" ? "active" : ""}`} onClick={() => switchTab("validation")}>
          Validation Codes <span className="mono" style={{ opacity: 0.72 }}>{CATALOGS.validationCodes.length}</span>
        </button>
        <button type="button" className={`ref-tab-btn ${tab === "catalogs" ? "active" : ""}`} onClick={() => switchTab("catalogs")}>
          Effects &amp; Modifiers <span className="mono" style={{ opacity: 0.72 }}>115</span>
        </button>
        <button type="button" className={`ref-tab-btn ${tab === "tech" ? "active" : ""}`} onClick={() => switchTab("tech")}>
          Tech Categories <span className="mono" style={{ opacity: 0.72 }}>{CATALOGS.technologyCategories.length}</span>
        </button>
      </div>

      {/* TAB 1: MOD METHODS */}
      {tab === "methods" && (
        <div>
          <div className="ref-toolbar">
            <input
              type="text"
              className="ref-search"
              placeholder="Search 257 Mod methods by name, argument, or docstring…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <select
              className="ref-select"
              value={layerFilter}
              onChange={(e) => setLayerFilter(e.target.value)}
              aria-label="Filter by layer"
            >
              <option value="all">All layers ({LAYERS.length})</option>
              {LAYERS.filter((l) => l.methodCount > 0).map((l) => (
                <option key={l.id} value={l.id}>
                  {l.id} ({l.methodCount})
                </option>
              ))}
            </select>
            <span className="mono" style={{ fontSize: 12, color: "var(--ink-muted)" }}>
              {filteredMethods.length} results
            </span>
          </div>

          <div className="ref-list">
            {filteredMethods.map((m) => {
              const isOpen = expandedMethod === m.name;
              return (
                <div key={`${m.layer}.${m.name}`} className="ref-row">
                  <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
                    <button
                      type="button"
                      onClick={() => setExpandedMethod(isOpen ? null : m.name)}
                      style={{
                        flex: 1,
                        textAlign: "left",
                        border: 0,
                        background: "transparent",
                        padding: 0,
                        cursor: "pointer",
                      }}
                    >
                      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                        {isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                        <span className="mono" style={{ fontSize: 13.2, fontWeight: 600, color: "var(--ink-primary)", wordBreak: "break-word" }}>
                          {m.kind === "staticmethod" ? "Mod." : "mod."}
                          {m.signature}
                        </span>
                      </div>
                      {m.summary && (
                        <div style={{ fontSize: 13.2, color: "var(--ink-secondary)", marginTop: 4, paddingLeft: 22 }}>
                          {m.summary}
                        </div>
                      )}
                    </button>

                    <div style={{ display: "flex", alignItems: "center", gap: 6, flexShrink: 0 }}>
                      <span className="tag">{m.layer}</span>
                      {m.kind !== "method" && <span className="tag tag-blue">{m.kind}</span>}
                    </div>
                  </div>

                  {isOpen && m.doc && (
                    <div
                      style={{
                        marginTop: 12,
                        marginLeft: 22,
                        padding: "12px 14px",
                        border: "1px solid var(--border-hairline)",
                        background: "var(--bg-surface)",
                        borderRadius: "var(--radius-xs)",
                        fontSize: 13,
                        whiteSpace: "pre-wrap",
                        color: "var(--ink-secondary)",
                        fontFamily: "var(--font-mono)",
                        lineHeight: 1.58,
                      }}
                    >
                      {m.doc}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* TAB 2: DEFINITION KINDS */}
      {tab === "definitions" && (
        <div className="kit-grid">
          <div>
            <div className="ref-toolbar">
              <input
                type="text"
                className="ref-search"
                placeholder="Search 36 common/ definition kinds (scripted_effect, mio, technology…)"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </div>

            <div className="ref-list">
              {filteredDefinitions.map((d) => (
                <button
                  key={d.name}
                  type="button"
                  onClick={() => setSelectedDef(d.name)}
                  className="ref-row"
                  style={{
                    width: "100%",
                    textAlign: "left",
                    borderLeft: d.name === activeDefinition.name ? "3px solid var(--accent)" : "3px solid transparent",
                    background: d.name === activeDefinition.name ? "var(--bg-surface)" : "transparent",
                    borderTop: 0,
                    borderRight: 0,
                    cursor: "pointer",
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
                    <span className="mono" style={{ fontWeight: 600, fontSize: 13.5 }}>{d.name}</span>
                    <span className="mono" style={{ fontSize: 11.5, color: "var(--ink-muted)" }}>{d.directory}</span>
                  </div>
                  <div style={{ fontSize: 13, color: "var(--ink-secondary)", marginTop: 3 }}>
                    {d.description}
                  </div>
                </button>
              ))}
            </div>
          </div>

          <div className="panel-box" style={{ position: "sticky", top: "calc(var(--nav-height) + 20px)" }}>
            <span className="eyebrow">DefinitionKind Spec</span>
            <h2 className="serif" style={{ fontSize: "1.65rem", fontWeight: 500, margin: "8px 0 6px" }}>
              {activeDefinition.name}
            </h2>
            <p style={{ margin: "0 0 16px", color: "var(--ink-secondary)", fontSize: 14 }}>
              {activeDefinition.description}
            </p>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 16 }}>
              <div style={{ padding: 10, border: "1px solid var(--border-hairline)", background: "var(--bg-surface)" }}>
                <div className="mono" style={{ fontSize: 10, color: "var(--ink-muted)", textTransform: "uppercase" }}>Directory</div>
                <div className="mono" style={{ fontSize: 12.5, marginTop: 3 }}>{activeDefinition.directory}/</div>
              </div>
              <div style={{ padding: 10, border: "1px solid var(--border-hairline)", background: "var(--bg-surface)" }}>
                <div className="mono" style={{ fontSize: 10, color: "var(--ink-muted)", textTransform: "uppercase" }}>Default File</div>
                <div className="mono" style={{ fontSize: 12.5, marginTop: 3 }}>{activeDefinition.defaultFile || "(explicit path required)"}</div>
              </div>
            </div>

            <div style={{ padding: 10, border: "1px solid var(--border-hairline)", background: "var(--bg-surface)", marginBottom: 16 }}>
              <div className="mono" style={{ fontSize: 10, color: "var(--ink-muted)", textTransform: "uppercase" }}>Paradox Storage Layout</div>
              <div className="mono" style={{ fontSize: 12.5, marginTop: 3 }}>{activeDefinition.layout}</div>
            </div>

            <CodeBlock
              language="python"
              title={`definition_${activeDefinition.name}.py`}
              code={`from hoi4 import Mod

mod = Mod.from_config()

# List effective definitions (mod + vanilla/base mods)
items = mod.list_definitions("${activeDefinition.name}")

# Create or patch byte-preservingly
mod.create_definition(
    "${activeDefinition.name}",
    "my_${activeDefinition.name}_id",
    body="# Paradox script body for this entry",
)
mod.save(require_changes=True)`}
            />
          </div>
        </div>
      )}

      {/* TAB 3: VALIDATION CODES */}
      {tab === "validation" && (
        <div>
          <div className="ref-toolbar">
            <input
              type="text"
              className="ref-search"
              placeholder="Search 163 validation codes (e.g. hull, recruit, localization, scope…)"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <select
              className="ref-select"
              value={valSeverity}
              onChange={(e) => setValSeverity(e.target.value as "all" | "error" | "warning")}
              aria-label="Filter by severity"
            >
              <option value="all">All severities ({CATALOGS.validationCodes.length})</option>
              <option value="error">Errors only ({CATALOGS.validationCodes.filter((v) => v.severity === "error").length})</option>
              <option value="warning">Suppressible warnings ({CATALOGS.validationCodes.filter((v) => v.severity === "warning").length})</option>
            </select>
            <select
              className="ref-select"
              value={valCategory}
              onChange={(e) => setValCategory(e.target.value)}
              aria-label="Filter by category"
            >
              <option value="all">All categories</option>
              {valCategories.map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
            <span className="mono" style={{ fontSize: 12, color: "var(--ink-muted)" }}>
              {filteredValidation.length} shown
            </span>
          </div>

          <div className="ref-list">
            {filteredValidation.map((v) => (
              <div key={v.code} className="ref-row" style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 16, flexWrap: "wrap" }}>
                <div style={{ flex: 1, minWidth: 260 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                    <span className="mono" style={{ fontWeight: 600, fontSize: 13.5 }}>{v.code}</span>
                    <span className={`tag ${v.severity === "warning" ? "tag-amber" : "tag-accent"}`}>
                      {v.severity}
                    </span>
                    <span className="tag">{v.category}</span>
                  </div>
                  <div style={{ fontSize: 13.5, color: "var(--ink-secondary)", marginTop: 4 }}>
                    {v.description}
                  </div>
                </div>
                {v.suppressible && (
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm"
                    onClick={() => copyText(v.code, `suppress_warnings=["${v.code}"]`)}
                  >
                    {copiedKey === v.code ? <Check size={12} /> : <Copy size={12} />}
                    {copiedKey === v.code ? "Copied" : "Copy suppress"}
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* TAB 4: EFFECTS & MODIFIERS */}
      {tab === "catalogs" && (
        <div>
          <div className="ref-toolbar">
            <div className="install-tabs" style={{ border: "1px solid var(--border-strong)", padding: 3, borderRadius: "var(--radius-xs)", background: "var(--bg-surface)" }}>
              <button
                type="button"
                className={`install-tab ${catalogKind === "effects" ? "active" : ""}`}
                onClick={() => setCatalogKind("effects")}
              >
                Curated Effects (53)
              </button>
              <button
                type="button"
                className={`install-tab ${catalogKind === "modifiers" ? "active" : ""}`}
                onClick={() => setCatalogKind("modifiers")}
              >
                Curated Modifiers (62)
              </button>
            </div>
            <input
              type="text"
              className="ref-search"
              placeholder={`Search ${catalogKind}…`}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>

          {catalogKind === "effects" ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 22 }}>
              {CATALOGS.effects.map((cat) => {
                const items = cat.items.filter(
                  (it) =>
                    !q ||
                    it.label.toLowerCase().includes(q) ||
                    it.script.toLowerCase().includes(q) ||
                    cat.category.toLowerCase().includes(q)
                );
                if (items.length === 0) return null;
                return (
                  <div key={cat.category}>
                    <div className="layer-tier-heading">
                      <span>{cat.category}</span>
                      <span>{items.length} effects</span>
                    </div>
                    <div className="ref-list">
                      {items.map((it) => (
                        <div key={`${cat.category}-${it.label}`} className="ref-row" style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 14, flexWrap: "wrap" }}>
                          <div>
                            <div style={{ fontWeight: 600, fontSize: 13.5 }}>{it.label}</div>
                            <code className="mono" style={{ fontSize: 12.2, color: "var(--accent)" }}>
                              {it.script}
                            </code>
                          </div>
                          <button
                            type="button"
                            className="btn btn-ghost btn-sm"
                            onClick={() => copyText(it.script, it.script)}
                          >
                            {copiedKey === it.script ? <Check size={12} /> : <Copy size={12} />}
                            {copiedKey === it.script ? "Copied" : "Copy"}
                          </button>
                        </div>
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 22 }}>
              {CATALOGS.modifiers.map((cat) => {
                const items = cat.items.filter(
                  (it) =>
                    !q ||
                    it.label.toLowerCase().includes(q) ||
                    it.key.toLowerCase().includes(q) ||
                    cat.category.toLowerCase().includes(q)
                );
                if (items.length === 0) return null;
                return (
                  <div key={cat.category}>
                    <div className="layer-tier-heading">
                      <span>{cat.category}</span>
                      <span>{items.length} modifiers</span>
                    </div>
                    <div className="ref-list">
                      {items.map((it) => {
                        const snippet = `${it.key} = ${it.example}`;
                        return (
                          <div key={`${cat.category}-${it.key}`} className="ref-row" style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 14, flexWrap: "wrap" }}>
                            <div>
                              <div style={{ fontWeight: 600, fontSize: 13.5 }}>{it.label}</div>
                              <code className="mono" style={{ fontSize: 12.2, color: "var(--green)" }}>
                                {snippet}
                              </code>
                            </div>
                            <button
                              type="button"
                              className="btn btn-ghost btn-sm"
                              onClick={() => copyText(it.key, snippet)}
                            >
                              {copiedKey === it.key ? <Check size={12} /> : <Copy size={12} />}
                              {copiedKey === it.key ? "Copied" : "Copy"}
                            </button>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* TAB 5: TECHNOLOGY CATEGORIES */}
      {tab === "tech" && (
        <div className="kit-grid">
          <div>
            <div className="ref-toolbar">
              <input
                type="text"
                className="ref-search"
                placeholder="Search 133 HOI4 1.19 technology bonus categories…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
              <span className="mono" style={{ fontSize: 12, color: "var(--ink-muted)" }}>
                {filteredTechs.length} categories
              </span>
            </div>

            <div className="tech-grid">
              {filteredTechs.map((cat) => (
                <button
                  key={cat}
                  type="button"
                  className={`tech-chip ${selectedTech === cat ? "active" : ""}`}
                  onClick={() => setSelectedTech(cat)}
                >
                  <span>{cat}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="panel-box" style={{ position: "sticky", top: "calc(var(--nav-height) + 20px)" }}>
            <span className="eyebrow">Selected Category</span>
            <h2 className="serif" style={{ fontSize: "1.6rem", fontWeight: 500, margin: "8px 0 8px" }}>
              {selectedTech}
            </h2>
            <p style={{ margin: "0 0 14px", fontSize: 13.8, color: "var(--ink-secondary)" }}>
              Verified against HOI4 1.19&apos;s generated technology documentation. Pass directly to{" "}
              <code>Mod.effect_add_tech_bonus()</code>.
            </p>
            <CodeBlock
              language="python"
              title="tech_bonus.py"
              code={`from hoi4 import Mod

reward = Mod.effect_add_tech_bonus(
    "${selectedTech}_bonus",
    category="${selectedTech}",
    uses=1,
    bonus=1.0,
)`}
            />
          </div>
        </div>
      )}
    </div>
  );
}

import { useState } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, Check, Copy } from "lucide-react";
import metaJson from "../generated/meta.json";
import releasesJson from "../generated/releases.json";
import type { ReleaseEntry, SdkMeta } from "../types/sdk";
import { CodeBlock } from "../components/CodeBlock";
import { SpanPatcherDemo } from "../components/SpanPatcherDemo";

const META = metaJson as SdkMeta;
const RELEASES = releasesJson as ReleaseEntry[];

const CONTRACT_ITEMS = [
  {
    num: "01",
    module: "hoi4.textio",
    title: "Byte-exact encoding, BOM, and line endings",
    body: "Reads files line by line without dropping stray bytes. Windows-1252, UTF-8 with BOM, and mixed CRLF/LF files round-trip untouched on every line you did not edit.",
  },
  {
    num: "02",
    module: "hoi4.syntax",
    title: "One lossless syntax tree for all Paradox script",
    body: "Every node carries its exact character offsets. Understands typed blocks (rgb {}, hsv360 {}), quoted keys, @variables, inline maths, and every comparison operator.",
  },
  {
    num: "03",
    module: "hoi4.patching",
    title: "Statement-level diffing inside nested blocks",
    body: "sync_body compares old and new block bodies statement by statement. Adding one trait, event, or victory point preserves sibling comments and unmodeled engine keys.",
  },
  {
    num: "04",
    module: "hoi4.syntax.repair",
    title: "Forgiving on vanilla, strict on your mod",
    body: "Ships with the game's own brace tolerance for inherited vanilla files (like FRA_1936 or SOV ideas), repairing them on override while refusing to overwrite malformed mod files.",
  },
  {
    num: "05",
    module: "mod.transaction()",
    title: "Dry-run previews and atomic multi-file commits",
    body: "All mutations stay in memory until save(). Inspect semantic preview_summary() and full unified diffs before writing across script, localisation, and binary DDS assets.",
  },
  {
    num: "06",
    module: "ExternalModificationError",
    title: "Optimistic concurrency and injection guards",
    body: "Detects external disk changes before preview or save, and refuses stray braces or unquoted multi-word scalars that could escape their target block.",
  },
];

const VALIDATION_STAGES = [
  {
    id: "build",
    title: 'stage="build"',
    badge: "Mid-pipeline",
    summary:
      "Fast structural, syntax, reference, and script-scope checks while a multi-script build is still assembling a country or tree.",
    checks: [
      "Paradox script syntax, stray braces, and BOM/encoding checks",
      "Cross-references for ideas, events, focuses, technologies, and equipment",
      "Installed-game HOI4 1.19 effect/trigger/modifier vocabulary & scope enforcement",
      "Duplicate single-definition IDs across mod files",
      "Defers complete-country package errors until assembly finishes",
    ],
    code: `issues = mod.validate(stage="build")
errors = [i for i in issues if i.severity == "error"]
if errors:
    for err in errors:
        print(f"[{err.code}] {err.message}")
    raise RuntimeError("Build validation failed")`,
  },
  {
    id: "package",
    title: 'stage="package" (default)',
    badge: "Country complete",
    summary:
      "Adds full CountryPackageReport checks for every created tag, distinguishing scenario-start countries from runtime-released revolts.",
    checks: [
      "Three-size TGA flags, leader portraits, and civilian small advisor sprites",
      "Roster recruitment in country history (rejects runtime recruit_character)",
      "Separate land, naval, and air OOBs with Man the Guns / By Blood Alone DLC gates",
      "Chassis technology grant order before modular create_equipment_variant",
      "Runtime activation path detection for tags released later via focus/event",
    ],
    code: `report = mod.validate_country_package("SIC")
assert report.complete, report.missing_requirements

issues = mod.validate(stage="package")
print(mod.preview_summary())`,
  },
  {
    id: "release",
    title: 'stage="release"',
    badge: "Pre-ship gate",
    summary:
      "Adds ContentLivenessReport static graph auditing, icon sprite resolution, strict localization, and optional live HOI4 error.log correlation.",
    checks: [
      "Unreachable focuses, unfired triggered events, and ungranted national spirits",
      "Asymmetric country/global/state flag reads and writes (vanilla-aware)",
      "Unused localization keys and missing translations across shipped languages",
      "Sprite resolution for focus icons, GFX_idea_ stems, and decision pictures",
      "Pairs with Mod.validate_game_log() after launching the real game",
    ],
    code: `issues = mod.validate(
    stage="release",
    validate_icons=True,
    strict_localization=True,
)
liveness = mod.analyze_content_liveness()
log_report = mod.validate_game_log(error_log_path)`,
  },
];

interface DomainTab {
  id: string;
  code: string;
  label: string;
  headline: string;
  summary: string;
  bullets: string[];
  docsHref: string;
  refHref: string;
  codeTitle: string;
  snippet: string;
}

const DOMAINS: DomainTab[] = [
  {
    id: "countries",
    code: "01 // ROSTERS",
    label: "Countries & Characters",
    headline: "Author complete playable countries instead of empty tags",
    summary:
      "Model tags, colors (rgb/hsv/hsv360), politics, multi-role characters with DLC instances, recruitment, name pools, and verify everything via CountryPackageReport.",
    bullets: [
      "Data-driven custom ideologies and sub-ideologies",
      "Direct role blocks (country_leader, advisor, corps_commander, navy_leader)",
      "Occurrence selectors for repeated DLC character instances",
      "set_country_name_pool() for air-capable dynamic character names",
    ],
    docsHref: "/docs/api#countries",
    refHref: "/reference?tab=methods&q=country",
    codeTitle: "country_authoring.py",
    snippet: `from hoi4 import AdvisorRole, Character, CharacterPortrait, Mod

mod = Mod.from_config()
mod.create_country("SIC", "Kingdom of Sicily", capital=115, color=(204, 153, 51))
mod.patch_state_history(115, owner="SIC", add_cores=["SIC"])

mod.create_character(
    "SIC",
    Character(
        id="SIC_luigi_sturzo",
        name="Luigi Sturzo",
        portraits=CharacterPortrait(civilian_small="GFX_idea_SIC_sturzo"),
        advisors=[AdvisorRole(slot="political_advisor", idea_token="SIC_sturzo", traits=["silent_workhorse"])],
    ),
    recruit=True,
)
assert mod.validate_country_package("SIC").complete`,
  },
  {
    id: "oobs",
    code: "02 // FORCES",
    label: "Land, Naval & Air OOBs",
    headline: "DLC-safe division templates, MtG ship hulls, and BBA airframes",
    summary:
      "Keep land, naval, and air OOBs in separate engine-assigned files with full EquipmentVariant ordering checks so HOI4 never silently drops ships or wings.",
    bullets: [
      "Separate set_oob, set_naval_oob, and set_air_oob assignments",
      "Man the Guns hull OOBs with required_dlc and legacy excluded_dlc fallback",
      "By Blood Alone airframes with version_name validation",
      "Verifies enabling chassis tech grants appear before create_equipment_variant",
    ],
    docsHref: "/docs/api#land-naval-and-air-oob-methods",
    refHref: "/reference?tab=methods&q=oob",
    codeTitle: "naval_mtg_split.py",
    snippet: `from hoi4 import EquipmentVariant, Fleet, OrderOfBattle, Ship, ShipEquipment, TaskForce

mod.update_country("SIC", technologies={"early_ship_hull_light": 1})
mod.create_equipment_variant(
    "SIC",
    EquipmentVariant(
        name="Palermo Class",
        type="ship_hull_light_1",
        modules={"fixed_ship_battery_slot": "ship_light_battery_1"},
    ),
    required_dlc=("Man the Guns",),
)
mod.create_oob(
    "SIC_1936_naval_mtg",
    OrderOfBattle(fleets=[Fleet(name="Regia Squadra", naval_base=10074, task_forces=[
        TaskForce(name="1a Flottiglia", location=10074, ships=[
            Ship(name="RN Etna", definition="destroyer",
                 equipment=ShipEquipment(type="ship_hull_light_1", version_name="Palermo Class"))
        ])
    ])]),
    kind="naval", country="SIC", assign=True, required_dlc=("Man the Guns",),
)`,
  },
  {
    id: "content",
    code: "03 // SCRIPTS",
    label: "Focuses, Events & Decisions",
    headline: "Shared focuses, fractional costs, missions, and conditional events",
    summary:
      "Edit vanilla or custom trees in place with automatic branch layout, overlap detection, decision missions, targeted decisions, and compositional on-actions.",
    bullets: [
      "Shared and joint focus containers (FocusTree.kind == 'file') & cost=7.2",
      "auto_layout_branch() and assert_no_visual_overlap() before saving",
      "Decision missions (days_mission_timeout) & targeted decisions",
      "36 keyed common/ definition domains via one unified CRUD API",
    ],
    docsHref: "/docs/recipes",
    refHref: "/reference?tab=definitions",
    codeTitle: "focus_and_mission.py",
    snippet: `from hoi4 import Focus, Mod

mod.insert_focus_after(
    "SIC_focus",
    after="SIC_industrial_charter",
    focus=Focus(
        id="SIC_palermo_shipyards",
        cost=7.2,
        completion_reward=Mod.effect_add_civilian_factory(115, count=2),
    ),
)
mod.create_mission(
    "SIC_industrial_category",
    "SIC_rail_deadline",
    days_mission_timeout=90,
    activation="has_country_flag = sic_rail_plan",
    timeout_effect=Mod.effect_add_political_power(-50),
)
mod.assert_no_visual_overlap("SIC_focus")`,
  },
  {
    id: "map",
    code: "04 // CARTOGRAPHY",
    label: "States, Map & Topology",
    headline: "Transactional province transfers, railways, and map analysis",
    summary:
      "Split states with automatic victory-point and building migration, edit strategic regions and supply networks, detect disconnected enclaves, or generate new maps.",
    bullets: [
      "create_state(), move_provinces(), and split_state() patch IDs in place",
      "Strategic regions, supply nodes, railways, and buildings.txt positions",
      "find_disconnected_states() and find_enclosed_foreign_states() topology checks",
      "Political map PNG rendering and procedural province/territory mapgen",
    ],
    docsHref: "/docs/api#map-editing",
    refHref: "/reference?tab=methods&q=state",
    codeTitle: "map_and_topology.py",
    snippet: `report = mod.split_state(
    source_state_id=115,
    new_state_id=950,
    provinces=[4159, 4014],
    name="East Sicily",
    owner="SIC",
    move_buildings=True,
)
disconnected = mod.find_disconnected_states("SIC", allowlist={114})
enclosed = mod.find_enclosed_foreign_states("SIC")
print(report.summary(), len(disconnected), len(enclosed))`,
  },
  {
    id: "validation",
    code: "05 // VERIFICATION",
    label: "Vocabulary & Log Auditing",
    headline: "Static HOI4 1.19 grammar checks plus post-launch error.log parsing",
    summary:
      "Cross-check every effect, trigger, and modifier against your installed game's generated docs, audit static liveness, and attribute real engine errors to your mod.",
    bullets: [
      "163 structured validation codes with 54 explicitly suppressible warnings",
      "Usage-frequency typo suggestions from your actual HOI4 installation",
      "Enforces documented country, state, character, and iterator scopes",
      "Incremental byte-offset error.log parser attributing mod-owned failures",
    ],
    docsHref: "/docs/agent-traps",
    refHref: "/reference?tab=validation",
    codeTitle: "audit_and_log.py",
    snippet: `# Static release validation with installed HOI4 1.19 vocabulary
issues = mod.validate(
    stage="release",
    validate_icons=True,
    strict_localization=True,
    suppress_warnings=["country_scope_core_effect"],
)

# Post-launch game error.log attribution (supports --incremental offsets)
log_report = mod.validate_game_log(
    "~/.local/share/Paradox Interactive/Hearts of Iron IV/logs/error.log"
)
print(f"Mod errors: {len(log_report.entries)}")`,
  },
  {
    id: "assets",
    code: "06 // GRAPHICS",
    label: "Assets, Commons & Gemini",
    headline: "Transactional DDS/TGA/GFX pipelines with web-first image sourcing",
    summary:
      "Search and download historical flags and portraits from Wikimedia Commons with checksum and license provenance, import all HOI4 sizes atomically, or generate opt-in Gemini candidates.",
    bullets: [
      "Key-free CommonsImageClient with creator, license, and SHA-256 JSON sidecars",
      "Transactional import_flag_to_mod() (3 TGA sizes) and portrait/icon DDS + .gfx",
      "Focus shine sprite generation and staged pre-save icon validation",
      "Opt-in GeminiImageGenerator for durable assets/candidates/ PNG review",
    ],
    docsHref: "/docs/recipes#source-a-flag-without-paid-generation",
    refHref: "/reference?tab=methods&q=import_",
    codeTitle: "commons_to_mod.py",
    snippet: `from pathlib import Path
from hoi4 import CommonsImageClient, Mod

client = CommonsImageClient()
selected = client.get_image("File:Flag_of_the_Kingdom_of_Sicily.svg")
source = client.download(selected, Path.cwd() / "assets" / "sources")

mod = Mod.from_config()
mod.import_flag_to_mod("SIC", source.path)          # large, medium, small TGA
portrait = mod.import_portrait_to_mod("SIC", "sturzo", "assets/sources/sturzo.jpg")
mod.write_portrait_gfx("SIC", "sturzo", portrait_path=portrait)
mod.save(require_changes=True)`,
  },
];

const EXTRA_KEYS = ["assets", "map", "gemini", "dev"] as const;

export function HomePage() {
  const [installer, setInstaller] = useState<"pip" | "uv">("pip");
  const [selectedExtras, setSelectedExtras] = useState<string[]>([]);
  const [copiedInstall, setCopiedInstall] = useState(false);
  const [activeStage, setActiveStage] = useState(VALIDATION_STAGES[1].id);
  const [activeDomain, setActiveDomain] = useState(DOMAINS[0].id);

  const toggleExtra = (extra: string) => {
    setSelectedExtras((prev) =>
      prev.includes(extra) ? prev.filter((e) => e !== extra) : [...prev, extra]
    );
  };

  const pkgSpec =
    selectedExtras.length > 0
      ? `'hoi4-agent-sdk[${selectedExtras.join(",")}]'`
      : "hoi4-agent-sdk";

  const installCmd =
    installer === "pip"
      ? `python -m pip install ${pkgSpec}`
      : `uv pip install ${pkgSpec}`;

  const handleCopyInstall = async () => {
    try {
      await navigator.clipboard.writeText(installCmd);
      setCopiedInstall(true);
      setTimeout(() => setCopiedInstall(false), 1600);
    } catch {
      // ignore
    }
  };

  const currentStage =
    VALIDATION_STAGES.find((s) => s.id === activeStage) || VALIDATION_STAGES[0];
  const currentDomain =
    DOMAINS.find((d) => d.id === activeDomain) || DOMAINS[0];

  const unreleased = RELEASES.find((r) => r.isUnreleased);
  const recentTagged = RELEASES.filter((r) => !r.isUnreleased).slice(0, 5);

  return (
    <div>
      {/* HERO BAND */}
      <section className="hero-band drafting-grid">
        <div className="hero-contour-bg" aria-hidden="true">
          <svg
            viewBox="0 0 1440 520"
            fill="none"
            preserveAspectRatio="xMidYMid slice"
            style={{ width: "100%", height: "100%" }}
          >
            <path
              d="M-40 430 C 210 350, 340 470, 620 390 C 900 310, 1120 420, 1480 310"
              stroke="var(--border-strong)"
              strokeOpacity="0.45"
              strokeWidth="1"
            />
            <path
              d="M-40 468 C 230 392, 370 502, 650 425 C 930 348, 1140 452, 1480 345"
              stroke="var(--border-strong)"
              strokeOpacity="0.3"
              strokeWidth="1"
            />
            <path
              d="M780 -20 C 860 95, 980 120, 1140 80 C 1270 48, 1380 110, 1490 70"
              stroke="var(--accent)"
              strokeOpacity="0.22"
              strokeWidth="1"
              strokeDasharray="4 6"
            />
          </svg>
        </div>

        <div className="container hero-grid">
          <div>
            <div className="hero-kicker-row">
              <span className="eyebrow">Python {META.requiresPython} · MIT</span>
              <span className="tag tag-green">Zero Core Dependencies</span>
              <span className="tag">HOI4 1.19 Verified</span>
            </div>

            <h1 className="hero-title">
              Paradox script, treated as <em>source code</em>.
            </h1>

            <p className="hero-sub">
              <strong>hoi4-agent-sdk</strong> is a source-preserving Python toolkit for
              creating, inspecting, validating, and editing Hearts of Iron IV mods. Built for
              automation, desktop tools, and coding agents that must patch hand-authored mods
              without destroying a single comment, byte-order mark, or unknown engine key.
            </p>

            <div className="hero-cta-row">
              <Link to="/docs/getting-started" className="btn btn-primary">
                Read the Documentation
                <ArrowRight size={15} />
              </Link>
              <Link to="/architecture" className="btn btn-secondary">
                Explore 30-Layer Architecture
              </Link>
              <Link to="/releases" className="btn btn-ghost">
                Release Notes
              </Link>
            </div>

            <div className="install-box">
              <div className="install-box-top">
                <div className="install-tabs" role="tablist" aria-label="Package manager">
                  <button
                    type="button"
                    className={`install-tab ${installer === "pip" ? "active" : ""}`}
                    onClick={() => setInstaller("pip")}
                  >
                    pip
                  </button>
                  <button
                    type="button"
                    className={`install-tab ${installer === "uv" ? "active" : ""}`}
                    onClick={() => setInstaller("uv")}
                  >
                    uv
                  </button>
                </div>

                <div className="install-extras" aria-label="Optional extras">
                  <span className="mono" style={{ fontSize: 10.5, color: "var(--ink-muted)", marginRight: 3 }}>
                    extras:
                  </span>
                  {EXTRA_KEYS.map((extra) => (
                    <button
                      key={extra}
                      type="button"
                      className={`extra-pill ${selectedExtras.includes(extra) ? "active" : ""}`}
                      onClick={() => toggleExtra(extra)}
                    >
                      +{extra}
                    </button>
                  ))}
                </div>
              </div>

              <div className="install-cmd-row">
                <span className="install-cmd-text">
                  <span style={{ color: "#7d8690", marginRight: 8 }}>$</span>
                  {installCmd}
                </span>
                <button
                  type="button"
                  className="code-copy-btn"
                  onClick={handleCopyInstall}
                  aria-label="Copy install command"
                >
                  {copiedInstall ? <Check size={12} /> : <Copy size={12} />}
                  {copiedInstall ? "Copied" : "Copy"}
                </button>
              </div>
            </div>
          </div>

          <SpanPatcherDemo />
        </div>
      </section>

      {/* TELEMETRY LEDGER STRIP */}
      <section className="ledger-band" aria-label="SDK architecture metrics">
        <div className="container" style={{ padding: 0 }}>
          <div className="ledger-grid">
            <div className="ledger-cell">
              <div className="ledger-value">{META.stats.modMethods}</div>
              <div className="ledger-label">Mod Facade Methods</div>
              <div className="ledger-sub">Single typed entry point</div>
            </div>
            <div className="ledger-cell">
              <div className="ledger-value">{META.stats.layerModules}</div>
              <div className="ledger-label">Acyclic Layer Modules</div>
              <div className="ledger-sub">Enforced by mypy + tests</div>
            </div>
            <div className="ledger-cell">
              <div className="ledger-value">{META.stats.definitionKinds}</div>
              <div className="ledger-label">Common/ Domains</div>
              <div className="ledger-sub">Unified CRUD &amp; patching</div>
            </div>
            <div className="ledger-cell">
              <div className="ledger-value">{META.stats.validationCodes}</div>
              <div className="ledger-label">Validation Codes</div>
              <div className="ledger-sub">{META.stats.errorCodes} errors · {META.stats.warningCodes} warnings</div>
            </div>
            <div className="ledger-cell">
              <div className="ledger-value">{META.stats.techCategories}</div>
              <div className="ledger-label">HOI4 1.19 Tech Cats</div>
              <div className="ledger-sub">+ {META.stats.curatedEffects} effects · {META.stats.curatedModifiers} modifiers</div>
            </div>
            <div className="ledger-cell">
              <div className="ledger-value">{META.stats.testSuites}</div>
              <div className="ledger-label">Test Suites</div>
              <div className="ledger-sub">Property &amp; corpus verified</div>
            </div>
          </div>
        </div>
      </section>

      {/* 01: SOURCE PRESERVATION CONTRACT */}
      <section className="home-section">
        <div className="container">
          <div className="section-header-row">
            <div>
              <span className="eyebrow">01 // Source-Preservation Contract</span>
              <h2 className="section-heading">Surgical edits, never destructive re-serialization</h2>
              <p className="section-lead">
                Most modding tools parse a file into JSON, lose every comment and formatting
                decision, and dump it back out. hoi4-agent-sdk replaces only the exact source spans
                that changed.
              </p>
            </div>
            <Link to="/docs/internals-parser" className="btn btn-secondary">
              Read Parser Internals
              <ArrowRight size={14} />
            </Link>
          </div>

          <div className="contract-grid">
            {CONTRACT_ITEMS.map((item) => (
              <article key={item.num} className="contract-cell">
                <div className="contract-index">
                  <span>SPEC {item.num}</span>
                  <span className="tag">{item.module}</span>
                </div>
                <h3>{item.title}</h3>
                <p>{item.body}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      {/* 02: SAFETY LOOP & STAGED VALIDATION */}
      <section className="home-section" style={{ background: "var(--bg-surface)" }}>
        <div className="container">
          <div className="section-header-row">
            <div>
              <span className="eyebrow">02 // Agent-Safe Workflow</span>
              <h2 className="section-heading">Dry-run transactions and three-stage validation</h2>
              <p className="section-lead">
                Validate incrementally during multi-script builds, verify full country visual and
                OOB packages before shipping, and audit static liveness plus real game error logs.
              </p>
            </div>
            <Link to="/field-kit" className="btn btn-secondary">
              Open Interactive Field Kit
              <ArrowRight size={14} />
            </Link>
          </div>

          <div className="stages-layout">
            <div className="stage-list" role="tablist" aria-label="Validation stages">
              {VALIDATION_STAGES.map((stage) => (
                <button
                  key={stage.id}
                  type="button"
                  role="tab"
                  aria-selected={stage.id === activeStage}
                  className={`stage-btn ${stage.id === activeStage ? "active" : ""}`}
                  onClick={() => setActiveStage(stage.id)}
                >
                  <div className="stage-btn-top">
                    <span className="stage-btn-title">{stage.title}</span>
                    <span className="tag">{stage.badge}</span>
                  </div>
                  <div className="stage-btn-desc">{stage.summary}</div>
                </button>
              ))}
            </div>

            <div className="panel-box">
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, marginBottom: 14 }}>
                <span className="mono" style={{ fontSize: 13, fontWeight: 600 }}>
                  What {currentStage.title} verifies
                </span>
                <span className="tag tag-accent">{currentStage.badge}</span>
              </div>

              <ul className="atlas-bullets" style={{ gridTemplateColumns: "1fr", margin: "0 0 18px" }}>
                {currentStage.checks.map((check) => (
                  <li key={check}>{check}</li>
                ))}
              </ul>

              <CodeBlock
                code={currentStage.code}
                language="python"
                title={`validate_${currentStage.id}.py`}
              />
            </div>
          </div>
        </div>
      </section>

      {/* 03: DOMAIN COVERAGE ATLAS */}
      <section className="home-section">
        <div className="container">
          <div className="section-header-row">
            <div>
              <span className="eyebrow">03 // Domain Coverage Atlas</span>
              <h2 className="section-heading">Every layer of a Hearts of Iron IV mod, typed</h2>
              <p className="section-lead">
                From character DLC instances and Man the Guns ship hulls to strategic regions,
                multi-language localisation, and Wikimedia Commons asset provenance.
              </p>
            </div>
            <Link to="/reference" className="btn btn-secondary">
              Browse Full API &amp; Catalogs
              <ArrowRight size={14} />
            </Link>
          </div>

          <div className="atlas-shell">
            <div className="atlas-tabs" role="tablist" aria-label="SDK domains">
              {DOMAINS.map((domain) => (
                <button
                  key={domain.id}
                  type="button"
                  role="tab"
                  aria-selected={domain.id === activeDomain}
                  className={`atlas-tab ${domain.id === activeDomain ? "active" : ""}`}
                  onClick={() => setActiveDomain(domain.id)}
                >
                  <span className="atlas-tab-code">{domain.code}</span>
                  <span className="atlas-tab-label">{domain.label}</span>
                </button>
              ))}
            </div>

            <div className="atlas-panel">
              <span className="eyebrow">{currentDomain.code}</span>
              <h3
                className="serif"
                style={{ fontSize: "1.65rem", fontWeight: 500, margin: "8px 0 10px", lineHeight: 1.2 }}
              >
                {currentDomain.headline}
              </h3>
              <p style={{ margin: 0, color: "var(--ink-secondary)", maxWidth: "66ch", fontSize: 14.5 }}>
                {currentDomain.summary}
              </p>

              <ul className="atlas-bullets">
                {currentDomain.bullets.map((b) => (
                  <li key={b}>{b}</li>
                ))}
              </ul>

              <CodeBlock
                code={currentDomain.snippet}
                language="python"
                title={currentDomain.codeTitle}
              />

              <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginTop: 18 }}>
                <Link to={currentDomain.docsHref} className="btn btn-primary btn-sm">
                  Read Guide
                  <ArrowRight size={13} />
                </Link>
                <Link to={currentDomain.refHref} className="btn btn-secondary btn-sm">
                  Inspect Methods &amp; Schemas
                </Link>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* 04: RELEASES & UNRELEASED MILESTONE */}
      <section className="home-section" style={{ background: "var(--bg-surface)" }}>
        <div className="container">
          <div className="section-header-row">
            <div>
              <span className="eyebrow">04 // Engineering Chronicle</span>
              <h2 className="section-heading">Built in the open, audited against real mods</h2>
              <p className="section-lead">
                Every release ships with semantic versioning, migration notes, and regression tests
                against real Hearts of Iron IV installs.
              </p>
            </div>
            <Link to="/releases" className="btn btn-primary">
              Full Release Notes ({META.stats.releases})
              <ArrowRight size={14} />
            </Link>
          </div>

          <div className="release-preview-grid">
            {unreleased && (
              <div className="panel-box panel-box-accent">
                <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", justifyContent: "space-between", gap: 8, marginBottom: 12 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span className="tag tag-accent">NEXT ON MAIN</span>
                    <span className="mono" style={{ fontWeight: 600, fontSize: 15 }}>
                      [Unreleased]
                    </span>
                  </div>
                  <span className="mono" style={{ fontSize: 12, color: "var(--ink-muted)" }}>
                    +{unreleased.counts.added} added · ~{unreleased.counts.changed} changed · !{unreleased.counts.fixed} fixed
                  </span>
                </div>

                <h3 className="serif" style={{ fontSize: "1.4rem", fontWeight: 500, margin: "0 0 10px" }}>
                  Lossless Syntax Tree, 30-Layer Mod Architecture &amp; 36 Common/ Domains
                </h3>
                <p style={{ fontSize: 13.8, color: "var(--ink-secondary)", margin: "0 0 16px" }}>
                  The largest architectural leap since 0.1.0: one lossless syntax tree (<code>hoi4.syntax</code>),
                  byte-exact line-by-line I/O (<code>hoi4.textio</code>), statement-level <code>sync_body</code> patching,
                  multi-language localisation, map editing, decision missions, and Wikimedia Commons sourcing.
                </p>

                <ul className="release-items" style={{ marginBottom: 18 }}>
                  <li><code>hoi4.syntax</code> &amp; <code>hoi4.textio</code> replace legacy regex readers across every content domain.</li>
                  <li>12,992-line <code>mod.py</code> decomposed into 30 acyclic mypy-checked layer modules.</li>
                  <li>Generic keyed <code>common/</code> definitions cover 36 game databases through one API.</li>
                  <li>Shared/joint focuses, fractional costs (<code>7.2</code>), and conditional event text preserved in place.</li>
                </ul>

                <Link to="/releases#unreleased" className="btn btn-secondary btn-sm">
                  Inspect all {unreleased.totalItems} unreleased entries
                  <ArrowRight size={13} />
                </Link>
              </div>
            )}

            <div className="panel-box">
              <div className="mono" style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.09em", color: "var(--ink-muted)", marginBottom: 14 }}>
                Recent Tagged Releases
              </div>
              <div style={{ display: "flex", flexDirection: "column" }}>
                {recentTagged.map((rel) => (
                  <Link
                    key={rel.id}
                    to={`/releases#${rel.id}`}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      gap: 12,
                      padding: "11px 0",
                      borderBottom: "1px solid var(--border-hairline)",
                    }}
                  >
                    <div>
                      <span className="mono" style={{ fontWeight: 600, fontSize: 13.5 }}>
                        v{rel.version}
                      </span>
                      <span className="mono" style={{ fontSize: 11.5, color: "var(--ink-muted)", marginLeft: 10 }}>
                        {rel.date}
                      </span>
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                      {rel.counts.added > 0 && <span className="tag tag-green">+{rel.counts.added}</span>}
                      {rel.counts.changed > 0 && <span className="tag tag-blue">~{rel.counts.changed}</span>}
                      {rel.counts.fixed > 0 && <span className="tag tag-amber">!{rel.counts.fixed}</span>}
                    </div>
                  </Link>
                ))}
              </div>
              <div style={{ marginTop: 18, display: "flex", gap: 10, flexWrap: "wrap" }}>
                <Link to="/docs/agent-traps" className="btn btn-ghost btn-sm">
                  19 Agent Traps Guide
                </Link>
                <Link to="/docs/contributing" className="btn btn-ghost btn-sm">
                  Corpus Test Policy
                </Link>
              </div>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}

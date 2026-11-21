import { useMemo, useState } from "react";
import { CodeBlock } from "../components/CodeBlock";

export function FieldKitPage() {
  const [tool, setTool] = useState<"scaffold" | "package">("scaffold");

  // Scaffold Builder State
  const [modPath, setModPath] = useState("/path/to/my_mod");
  const [hoi4Install, setHoi4Install] = useState("/path/to/Hearts of Iron IV");
  const [useBaseMod, setUseBaseMod] = useState(false);
  const [baseModPath, setBaseModPath] = useState("/path/to/base_mod");
  const [strictLoading, setStrictLoading] = useState(true);
  const [stage, setStage] = useState<"build" | "package" | "release">("release");
  const [dryRun, setDryRun] = useState(true);
  const [validateIcons, setValidateIcons] = useState(true);
  const [strictLoc, setStrictLoc] = useState(true);

  // Country Package Readiness State
  const [tag, setTag] = useState("SIC");
  const [countryName, setCountryName] = useState("Kingdom of Sicily");
  const [capitalState, setCapitalState] = useState("115");
  const [lifecycle, setLifecycle] = useState<"starting" | "runtime">("starting");
  const [hasNavyMtg, setHasNavyMtg] = useState(true);
  const [hasAirBba, setHasAirBba] = useState(true);
  const [useCommons, setUseCommons] = useState(true);

  const hoi4JsonConfig = useMemo(() => {
    const obj: Record<string, unknown> = {
      mod_path: modPath || "/path/to/my_mod",
      hoi4_install: hoi4Install || "/path/to/Hearts of Iron IV",
    };
    if (useBaseMod && baseModPath.trim()) {
      obj.base_mod_paths = [baseModPath.trim()];
    }
    return JSON.stringify(obj, null, 2);
  }, [modPath, hoi4Install, useBaseMod, baseModPath]);

  const workflowScript = useMemo(() => {
    const valArgs = [`stage="${stage}"`];
    if (stage === "release") {
      if (validateIcons) valArgs.push("validate_icons=True");
      if (strictLoc) valArgs.push("strict_localization=True");
    }
    const txLine = dryRun
      ? "with mod.transaction():"
      : "with mod.transaction(save=True):";

    return `from hoi4 import Focus, Mod

# Loads .hoi4.json from current working directory (or parents)
mod = Mod.from_config(strict_loading=${strictLoading ? "True" : "False"})

${txLine}
    # Apply source-preserving edits here
    mod.upsert_focus(
        "my_focus_tree",
        Focus(
            id="MY_industrial_charter",
            x=0,
            y=0,
            cost=7.2,
            completion_reward=Mod.effect_add_political_power(100),
        ),
    )

    print(mod.preview_summary())
    print(mod.preview())

    issues = mod.validate(${valArgs.join(", ")})
    for issue in issues:
        print(f"[{issue.severity.upper()}:{issue.code}] {issue.message}")
    if any(issue.severity == "error" for issue in issues):
        raise RuntimeError("Validation failed; aborting transaction.")
${
  dryRun
    ? "\n# Dry run complete: edits were rolled back on exit.\n# Switch to mod.transaction(save=True) or mod.save(require_changes=True) to commit."
    : "\n# Committed atomically on clean exit from transaction(save=True)."
}`;
  }, [strictLoading, stage, dryRun, validateIcons, strictLoc]);

  const checklist = useMemo(() => {
    const items = [
      {
        title: `Three-size flag TGA set (${tag}.tga large, medium, small)`,
        detail: useCommons
          ? "Source via CommonsImageClient, verify license + 10x7 preview, then mod.import_flag_to_mod()."
          : "Import vetted local source via mod.import_flag_to_mod().",
        required: true,
      },
      {
        title: "Head-of-state portrait + GFX sprite declaration",
        detail: "156x210 DDS portrait imported via mod.import_portrait_to_mod() + mod.write_portrait_gfx().",
        required: true,
      },
      {
        title: "Coherent character roster (>=2 advisors + >=2 commanders)",
        detail: "Recruited in country history only (never runtime recruit_character); every advisor has civilian_small sprite.",
        required: true,
      },
      {
        title:
          lifecycle === "starting"
            ? `Scenario-start territory (state ${capitalState} owned + cored) & land OOB`
            : `Runtime activation path (focus/event/decision releasing ${tag} & setting capital ${capitalState})`,
        detail:
          lifecycle === "starting"
            ? "Explicit mod.patch_state_history() + separate land OOB assigned via set_oob."
            : "Do NOT assign 1936 ownership or starting OOB; CountryPackageReport validates the activation path.",
        required: true,
      },
      {
        title: "Man the Guns naval split + chassis tech ordering",
        detail: "Grant chassis tech before create_equipment_variant(); gate hull OOB with required_dlc=('Man the Guns',) and provide legacy fallback with excluded_dlc.",
        required: lifecycle === "starting" && hasNavyMtg,
      },
      {
        title: "By Blood Alone airframe split + country name pool",
        detail: "Gate airframe OOB with required_dlc=('By Blood Alone',), provide legacy air OOB, and register set_country_name_pool().",
        required: lifecycle === "starting" && hasAirBba,
      },
      {
        title: "Topology & structural package gate",
        detail: "Run find_disconnected_states(), find_enclosed_foreign_states(), and assert mod.validate_country_package(tag).complete.",
        required: true,
      },
    ];
    return items.filter((it) => it.required);
  }, [tag, capitalState, lifecycle, hasNavyMtg, hasAirBba, useCommons]);

  const packageScript = useMemo(() => {
    const cleanTag = (tag.toUpperCase().replace(/[^A-Z0-9]/g, "") || "TAG").slice(0, 3);
    const cap = parseInt(capitalState, 10) || 115;

    return `from hoi4 import Mod

mod = Mod.from_config(strict_loading=True)

mod.create_country("${cleanTag}", "${countryName || "New Country"}", capital=${cap}, color=(184, 72, 48))
${
  lifecycle === "starting"
    ? `mod.patch_state_history(${cap}, owner="${cleanTag}", add_cores=["${cleanTag}"])`
    : `# Runtime-released tag: grant cores/territory via focus or event effect instead of 1936 history\n# reward = Mod.effect_transfer_state_with_core(${cap}, "${cleanTag}")`
}
${hasAirBba && lifecycle === "starting" ? `mod.set_country_name_pool("${cleanTag}", male_names=["Marco", "Giovanni"], surnames=["Rossi", "Bianchi"])` : ""}
${useCommons ? `# Source historical flag via Wikimedia Commons before importing:\n# client = CommonsImageClient(); img = client.get_image("File:...")` : ""}
# Final structural completeness check
report = mod.validate_country_package("${cleanTag}")
print("Complete:", report.complete, "| Lifecycle:", report.lifecycle)
if not report.complete:
    for missing in report.missing_requirements:
        print(" -", missing)

# Check map topology (requires [map] extra)
print("Disconnected states:", mod.find_disconnected_states("${cleanTag}"))
print("Enclosed foreign states:", mod.find_enclosed_foreign_states("${cleanTag}"))`;
  }, [tag, countryName, capitalState, lifecycle, hasAirBba, useCommons]);

  return (
    <div className="container" style={{ paddingTop: 40, paddingBottom: 72 }}>
      <span className="eyebrow">Field Kit // Interactive Generators</span>
      <h1 className="section-heading">Config builders &amp; country-package readiness</h1>
      <p className="section-lead">
        Generate agent-safe transaction boilerplate, <code>.hoi4.json</code> workspace configs,
        and <code>CountryPackageReport</code> checklists aligned with the SDK&apos;s hard rules.
      </p>

      <div className="ref-tabs-bar">
        <button
          type="button"
          className={`ref-tab-btn ${tool === "scaffold" ? "active" : ""}`}
          onClick={() => setTool("scaffold")}
        >
          01 // .hoi4.json &amp; Transaction Scaffold
        </button>
        <button
          type="button"
          className={`ref-tab-btn ${tool === "package" ? "active" : ""}`}
          onClick={() => setTool("package")}
        >
          02 // Country Package Readiness Matrix
        </button>
      </div>

      {tool === "scaffold" ? (
        <div className="kit-grid">
          <div className="panel-box">
            <div className="kit-field">
              <label className="kit-label" htmlFor="mod-path">mod_path</label>
              <input
                id="mod-path"
                className="kit-input"
                value={modPath}
                onChange={(e) => setModPath(e.target.value)}
              />
            </div>

            <div className="kit-field">
              <label className="kit-label" htmlFor="hoi4-install">hoi4_install (optional, enables vanilla vocabulary &amp; fallback)</label>
              <input
                id="hoi4-install"
                className="kit-input"
                value={hoi4Install}
                onChange={(e) => setHoi4Install(e.target.value)}
              />
            </div>

            <label className="kit-check-row">
              <input type="checkbox" checked={useBaseMod} onChange={(e) => setUseBaseMod(e.target.checked)} />
              <div>
                <div style={{ fontWeight: 600, fontSize: 13.5 }}>Include ordered base_mod_paths</div>
                <div style={{ fontSize: 12.2, color: "var(--ink-secondary)" }}>
                  Inherits effective content from directory-backed parent mods.
                </div>
              </div>
            </label>

            {useBaseMod && (
              <div className="kit-field">
                <label className="kit-label" htmlFor="base-mod">base_mod_paths[0]</label>
                <input
                  id="base-mod"
                  className="kit-input"
                  value={baseModPath}
                  onChange={(e) => setBaseModPath(e.target.value)}
                />
              </div>
            )}

            <div className="kit-field" style={{ marginTop: 14 }}>
              <label className="kit-label" htmlFor="val-stage">Validation stage</label>
              <select
                id="val-stage"
                className="kit-input"
                value={stage}
                onChange={(e) => setStage(e.target.value as "build" | "package" | "release")}
              >
                <option value="build">build — mid-pipeline checks</option>
                <option value="package">package — complete-country enforcement</option>
                <option value="release">release — full liveness, icons &amp; localization</option>
              </select>
            </div>

            <label className="kit-check-row">
              <input type="checkbox" checked={strictLoading} onChange={(e) => setStrictLoading(e.target.checked)} />
              <div>
                <div style={{ fontWeight: 600, fontSize: 13.5 }}>strict_loading=True</div>
                <div style={{ fontSize: 12.2, color: "var(--ink-secondary)" }}>
                  Stop immediately if any mod file has a syntax error or fails to load.
                </div>
              </div>
            </label>

            <label className="kit-check-row">
              <input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} />
              <div>
                <div style={{ fontWeight: 600, fontSize: 13.5 }}>Dry-run transaction (rollback on exit)</div>
                <div style={{ fontSize: 12.2, color: "var(--ink-secondary)" }}>
                  Recommended for first pass so you can inspect preview() before committing.
                </div>
              </div>
            </label>

            {stage === "release" && (
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                <label className="kit-check-row" style={{ marginBottom: 0 }}>
                  <input type="checkbox" checked={validateIcons} onChange={(e) => setValidateIcons(e.target.checked)} />
                  <span style={{ fontSize: 12.5 }}>validate_icons=True</span>
                </label>
                <label className="kit-check-row" style={{ marginBottom: 0 }}>
                  <input type="checkbox" checked={strictLoc} onChange={(e) => setStrictLoc(e.target.checked)} />
                  <span style={{ fontSize: 12.5 }}>strict_localization=True</span>
                </label>
              </div>
            )}
          </div>

          <div>
            <CodeBlock code={hoi4JsonConfig} language="json" title=".hoi4.json" />
            <CodeBlock code={workflowScript} language="python" title="/tmp/hoi4-agent-scripts/run_edit.py" />
          </div>
        </div>
      ) : (
        <div className="kit-grid">
          <div className="panel-box">
            <div style={{ display: "grid", gridTemplateColumns: "100px 1fr 110px", gap: 10 }}>
              <div className="kit-field">
                <label className="kit-label" htmlFor="c-tag">Tag</label>
                <input id="c-tag" className="kit-input" maxLength={3} value={tag} onChange={(e) => setTag(e.target.value)} />
              </div>
              <div className="kit-field">
                <label className="kit-label" htmlFor="c-name">Country Name</label>
                <input id="c-name" className="kit-input" value={countryName} onChange={(e) => setCountryName(e.target.value)} />
              </div>
              <div className="kit-field">
                <label className="kit-label" htmlFor="c-cap">Capital ID</label>
                <input id="c-cap" className="kit-input" value={capitalState} onChange={(e) => setCapitalState(e.target.value)} />
              </div>
            </div>

            <div className="kit-field">
              <label className="kit-label" htmlFor="c-life">Country Lifecycle</label>
              <select
                id="c-life"
                className="kit-input"
                value={lifecycle}
                onChange={(e) => setLifecycle(e.target.value as "starting" | "runtime")}
              >
                <option value="starting">Exists at scenario start (1936)</option>
                <option value="runtime">Released at runtime (focus / event / revolt)</option>
              </select>
            </div>

            {lifecycle === "starting" && (
              <>
                <label className="kit-check-row">
                  <input type="checkbox" checked={hasNavyMtg} onChange={(e) => setHasNavyMtg(e.target.checked)} />
                  <div>
                    <div style={{ fontWeight: 600, fontSize: 13.5 }}>Naval OOB with Man the Guns DLC split</div>
                    <div style={{ fontSize: 12.2, color: "var(--ink-secondary)" }}>
                      Separate hull OOB + EquipmentVariant + legacy fallback.
                    </div>
                  </div>
                </label>
                <label className="kit-check-row">
                  <input type="checkbox" checked={hasAirBba} onChange={(e) => setHasAirBba(e.target.checked)} />
                  <div>
                    <div style={{ fontWeight: 600, fontSize: 13.5 }}>Air OOB with By Blood Alone split + name pool</div>
                    <div style={{ fontSize: 12.2, color: "var(--ink-secondary)" }}>
                      Separate airframe OOB + set_country_name_pool() for ace generation.
                    </div>
                  </div>
                </label>
              </>
            )}

            <label className="kit-check-row">
              <input type="checkbox" checked={useCommons} onChange={(e) => setUseCommons(e.target.checked)} />
              <div>
                <div style={{ fontWeight: 600, fontSize: 13.5 }}>Web-first Wikimedia Commons image sourcing</div>
                <div style={{ fontSize: 12.2, color: "var(--ink-secondary)" }}>
                  Key-free search + durable assets/sources/ provenance JSON sidecars.
                </div>
              </div>
            </label>

            <div style={{ marginTop: 18, paddingTop: 16, borderTop: "1px solid var(--border-hairline)" }}>
              <div className="mono" style={{ fontSize: 11, textTransform: "uppercase", color: "var(--ink-muted)", marginBottom: 10 }}>
                Required Package Deliverables ({checklist.length})
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {checklist.map((item, idx) => (
                  <div
                    key={idx}
                    style={{
                      padding: "10px 12px",
                      border: "1px solid var(--border-hairline)",
                      background: "var(--bg-surface)",
                      borderRadius: "var(--radius-xs)",
                    }}
                  >
                    <div style={{ fontWeight: 600, fontSize: 13.2 }}>
                      <span className="mono" style={{ color: "var(--accent)", marginRight: 8 }}>
                        0{idx + 1}
                      </span>
                      {item.title}
                    </div>
                    <div style={{ fontSize: 12.3, color: "var(--ink-secondary)", marginTop: 3 }}>
                      {item.detail}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div>
            <CodeBlock code={packageScript} language="python" title={`verify_${(tag || "TAG").toLowerCase()}_package.py`} />
          </div>
        </div>
      )}
    </div>
  );
}

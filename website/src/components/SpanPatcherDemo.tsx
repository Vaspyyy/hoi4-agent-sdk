import { useState } from "react";
import { highlightCode } from "../lib/highlight";

interface PatchLine {
  num: number;
  kind: "same" | "old" | "new";
  before?: string;
  span?: string;
  after?: string;
  raw: string;
}

interface PatchScenario {
  id: string;
  label: string;
  filePath: string;
  encoding: string;
  lineEndings: string;
  spanBytes: string;
  unchangedPct: string;
  commentsPreserved: string;
  pythonCall: string;
  note: string;
  lines: PatchLine[];
}

const SCENARIOS: PatchScenario[] = [
  {
    id: "focus-cost",
    label: "Focus Cost (7.2)",
    filePath: "common/national_focus/germany.txt",
    encoding: "UTF-8 (no BOM)",
    lineEndings: "CRLF preserved",
    spanBytes: "bytes [214..216] -> [214..217]",
    unchangedPct: "98.6%",
    commentsPreserved: "3 / 3 intact",
    pythonCall: `mod.update_focus("german_focus", "GER_rhineland", cost=7.2)`,
    note: "Conditional icon blocks, inline comments, and unknown keys stay untouched byte-for-byte.",
    lines: [
      { num: 12, kind: "same", raw: "    shared_focus = {" },
      { num: 13, kind: "same", raw: "        id = GER_rhineland # entry anchor" },
      { num: 14, kind: "same", raw: "        icon = { trigger = { has_dlc = \"Gtd\" } value = GFX_goal_ger }" },
      { num: 15, kind: "old", before: "        cost = ", span: "10", after: " # 70 days baseline", raw: "        cost = 10 # 70 days baseline" },
      { num: 15, kind: "new", before: "        cost = ", span: "7.2", after: " # 70 days baseline", raw: "        cost = 7.2 # 70 days baseline" },
      { num: 16, kind: "same", raw: "        x = 0   y = 0   # hand-aligned grid" },
      { num: 17, kind: "same", raw: "        completion_reward = { add_political_power = 120 }" },
      { num: 18, kind: "same", raw: "    }" },
    ],
  },
  {
    id: "state-patch",
    label: "State Owner & Core",
    filePath: "history/states/115-Sicily.txt",
    encoding: "UTF-8 + BOM",
    lineEndings: "CRLF preserved",
    spanBytes: "sync_body(history): 2 spans",
    unchangedPct: "94.1%",
    commentsPreserved: "2 / 2 intact",
    pythonCall: `mod.patch_state_history(115, owner="SIC", add_cores=["SIC"])`,
    note: "Multi-pair victory_points and buildings comments survive untouched; only history statements change.",
    lines: [
      { num: 1, kind: "same", raw: "state = {" },
      { num: 2, kind: "same", raw: "    id = 115" },
      { num: 3, kind: "same", raw: "    name = \"STATE_115\" # Sicily" },
      { num: 4, kind: "same", raw: "    history = {" },
      { num: 5, kind: "old", before: "        owner = ", span: "ITA", after: "", raw: "        owner = ITA" },
      { num: 5, kind: "new", before: "        owner = ", span: "SIC", after: "", raw: "        owner = SIC" },
      { num: 6, kind: "same", raw: "        victory_points = { 10074 10 4159 5 } # Palermo & Messina" },
      { num: 7, kind: "same", raw: "        add_core_of = ITA" },
      { num: 8, kind: "new", before: "        ", span: "add_core_of = SIC", after: "", raw: "        add_core_of = SIC" },
      { num: 9, kind: "same", raw: "    }" },
    ],
  },
  {
    id: "decision-mission",
    label: "Decision Mission",
    filePath: "common/decisions/SIC_industrial_drive.txt",
    encoding: "UTF-8 (no BOM)",
    lineEndings: "LF preserved",
    spanBytes: "bytes [148..151] -> [148..151]",
    unchangedPct: "97.9%",
    commentsPreserved: "2 / 2 intact",
    pythonCall: `mod.update_decision("SIC_rail_deadline", days_mission_timeout=90)`,
    note: "Unmodeled script constants like cost = @rail_cost and category presentation stay in place.",
    lines: [
      { num: 4, kind: "same", raw: "SIC_industrial_category = {" },
      { num: 5, kind: "same", raw: "    SIC_rail_deadline = {" },
      { num: 6, kind: "same", raw: "        icon = generic_construction" },
      { num: 7, kind: "same", raw: "        activation = { has_country_flag = sic_rail_plan } # event trigger" },
      { num: 8, kind: "old", before: "        days_mission_timeout = ", span: "120", after: "", raw: "        days_mission_timeout = 120" },
      { num: 8, kind: "new", before: "        days_mission_timeout = ", span: "90", after: "", raw: "        days_mission_timeout = 90" },
      { num: 9, kind: "same", raw: "        is_good = no # failure harms stability" },
      { num: 10, kind: "same", raw: "    }" },
    ],
  },
  {
    id: "vanilla-repair",
    label: "Vanilla Brace Repair",
    filePath: "history/units/FRA_1936.txt (override)",
    encoding: "Windows-1252 preserved",
    lineEndings: "CRLF preserved",
    spanBytes: "syntax.repair(): +1 closing brace",
    unchangedPct: "99.4%",
    commentsPreserved: "All intact",
    pythonCall: `mod.update_oob("FRA_1936", divisions=updated_divs)  # inherited vanilla file`,
    note: "Game-shipped files with unbalanced braces load forgivingly and emit balanced overrides in preview/save.",
    lines: [
      { num: 412, kind: "same", raw: "    division = {" },
      { num: 413, kind: "same", raw: "        name = \"1ère Division d'Infanterie\" # Windows-1252 byte kept" },
      { num: 414, kind: "same", raw: "        location = 11506" },
      { num: 415, kind: "same", raw: "        division_template = \"Division d'Infanterie\"" },
      { num: 416, kind: "same", raw: "        start_experience_factor = 0.3" },
      { num: 417, kind: "same", raw: "    }" },
      { num: 418, kind: "new", before: "", span: "}", after: " # repaired by hoi4.syntax.repair() on override", raw: "} # repaired by hoi4.syntax.repair() on override" },
    ],
  },
  {
    id: "event-cond",
    label: "Conditional Event Title",
    filePath: "events/SIC_events.txt",
    encoding: "UTF-8 (no BOM)",
    lineEndings: "LF preserved",
    spanBytes: "bytes [94..107] -> [94..113]",
    unchangedPct: "95.8%",
    commentsPreserved: "2 / 2 intact",
    pythonCall: `mod.update_event("sicily.1", title="sicily.1.t_republic")`,
    note: "Updating the plain title leaves conditional title = { trigger = { ... } } alternatives intact.",
    lines: [
      { num: 1, kind: "same", raw: "country_event = {" },
      { num: 2, kind: "same", raw: "    id = sicily.1" },
      { num: 3, kind: "old", before: "    title = ", span: "sicily.1.t", after: " # default fallback", raw: "    title = sicily.1.t # default fallback" },
      { num: 3, kind: "new", before: "    title = ", span: "sicily.1.t_republic", after: " # default fallback", raw: "    title = sicily.1.t_republic # default fallback" },
      { num: 4, kind: "same", raw: "    title = { text = sicily.1.t_monarchy trigger = { has_government = neutrality } }" },
      { num: 5, kind: "same", raw: "    desc = sicily.1.d" },
      { num: 6, kind: "same", raw: "    is_triggered_only = yes # fired via focus" },
      { num: 7, kind: "same", raw: "}" },
    ],
  },
];

export function SpanPatcherDemo() {
  const [activeId, setActiveId] = useState(SCENARIOS[0].id);
  const [viewMode, setViewMode] = useState<"diff" | "after">("diff");

  const scenario = SCENARIOS.find((s) => s.id === activeId) || SCENARIOS[0];
  const visibleLines =
    viewMode === "diff"
      ? scenario.lines
      : scenario.lines.filter((line) => line.kind !== "old");

  return (
    <div className="patcher-card" aria-label="Interactive lossless span patching demonstration">
      <div className="patcher-topbar">
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span className="tag tag-accent">LIVE SPAN INSPECTOR</span>
          <span className="mono" style={{ fontSize: 11.5, color: "var(--ink-muted)" }}>
            hoi4.syntax + hoi4.patching
          </span>
        </div>
        <div className="install-tabs" role="tablist" aria-label="Patch view mode">
          <button
            type="button"
            role="tab"
            aria-selected={viewMode === "diff"}
            className={`install-tab ${viewMode === "diff" ? "active" : ""}`}
            onClick={() => setViewMode("diff")}
          >
            Span Diff
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={viewMode === "after"}
            className={`install-tab ${viewMode === "after" ? "active" : ""}`}
            onClick={() => setViewMode("after")}
          >
            Result File
          </button>
        </div>
      </div>

      <div className="patcher-scenarios" role="tablist" aria-label="Editing scenarios">
        {SCENARIOS.map((item) => (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={item.id === activeId}
            className={`scenario-btn ${item.id === activeId ? "active" : ""}`}
            onClick={() => setActiveId(item.id)}
          >
            {item.label}
          </button>
        ))}
      </div>

      <div className="patcher-call">
        <span style={{ color: "#7d8690", marginRight: 8 }}>&gt;&gt;&gt;</span>
        <code dangerouslySetInnerHTML={{ __html: highlightCode(scenario.pythonCall, "python") }} />
      </div>

      <div className="patcher-filebar">
        <span>{scenario.filePath}</span>
        <span>{scenario.encoding} · {scenario.lineEndings}</span>
      </div>

      <pre className="patcher-code-body">
        {visibleLines.map((line, i) => {
          const prefix =
            viewMode === "diff"
              ? line.kind === "old"
                ? "-"
                : line.kind === "new"
                  ? "+"
                  : " "
              : " ";

          return (
            <div
              key={`${line.num}-${line.kind}-${i}`}
              className={`patcher-line ${
                viewMode === "diff"
                  ? line.kind === "old"
                    ? "changed-old"
                    : line.kind === "new"
                      ? "changed-new"
                      : ""
                  : ""
              }`}
            >
              <span className="patcher-line-num">{line.num}</span>
              <span
                style={{
                  width: 14,
                  flexShrink: 0,
                  color:
                    line.kind === "old"
                      ? "#df664c"
                      : line.kind === "new"
                        ? "#61b28e"
                        : "transparent",
                }}
              >
                {prefix}
              </span>
              {line.kind === "same" ? (
                <span dangerouslySetInnerHTML={{ __html: highlightCode(line.raw, "pdx") }} />
              ) : (
                <span>
                  <span dangerouslySetInnerHTML={{ __html: highlightCode(line.before || "", "pdx") }} />
                  <span className={line.kind === "old" ? "span-mark-del" : "span-mark-add"}>
                    {line.span}
                  </span>
                  <span dangerouslySetInnerHTML={{ __html: highlightCode(line.after || "", "pdx") }} />
                </span>
              )}
            </div>
          );
        })}
      </pre>

      <div
        style={{
          padding: "9px 14px",
          borderTop: "1px solid var(--border-code)",
          background: "#13171c",
          color: "#a7b0ba",
          fontSize: 12,
        }}
      >
        {scenario.note}
      </div>

      <div className="patcher-footer">
        <div className="patcher-stat">
          <div className="patcher-stat-label">Byte Span</div>
          <div className="patcher-stat-val">{scenario.spanBytes}</div>
        </div>
        <div className="patcher-stat">
          <div className="patcher-stat-label">Untouched Bytes</div>
          <div className="patcher-stat-val" style={{ color: "var(--green)" }}>
            {scenario.unchangedPct}
          </div>
        </div>
        <div className="patcher-stat">
          <div className="patcher-stat-label">Comments</div>
          <div className="patcher-stat-val">{scenario.commentsPreserved}</div>
        </div>
      </div>
    </div>
  );
}

#!/usr/bin/env python3
"""Sicily Revolution Mod — creates separatist events, focus tree, and decisions."""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))

from hoi4 import Mod, Focus, Country, Event, EventOption, Idea
from hoi4.parser import parse_pdx, serialize_pdx

mod = Mod.from_config()

# ═══════════════════════════════════════════════════════════════
# Countries
# ═══════════════════════════════════════════════════════════════

# Sicily
mod.create_country(
    "SCL", "Sicily", adjective="Sicilian",
    color=(255, 180, 50), capital=115,
    ruling_party="neutrality",
    popularities={"neutrality": 55, "democratic": 20, "fascism": 15, "communism": 10},
    leader_name="Andrea Finocchiaro Aprile", leader_ideology="socialist",
    ideas=["sicilian_resistance"],
)

# Sardinia
mod.create_country(
    "SRD", "Sardinia", adjective="Sardinian",
    color=(180, 80, 60), capital=114,
    ruling_party="neutrality",
    popularities={"neutrality": 50, "democratic": 25, "fascism": 15, "communism": 10},
    leader_name="Emilio Lussu", leader_ideology="socialist",
)

# Veneto
mod.create_country(
    "VNT", "Veneto", adjective="Venetian",
    color=(60, 140, 200), capital=160,
    ruling_party="neutrality",
    popularities={"neutrality": 45, "democratic": 30, "fascism": 15, "communism": 10},
    leader_name="Silvio Trentin", leader_ideology="liberalism",
)

# Zara
mod.create_country(
    "ZAR", "Zara", adjective="Zaran",
    color=(140, 100, 180), capital=163,
    ruling_party="neutrality",
    popularities={"neutrality": 50, "democratic": 20, "fascism": 20, "communism": 10},
    leader_name="Antonio Bajamonti", leader_ideology="liberalism",
)

# ═══════════════════════════════════════════════════════════════
# National Spirits
# ═══════════════════════════════════════════════════════════════

mod.create_idea("sicilian_resistance", modifier={
    "defence_factor": 0.10,
    "war_support_factor": 0.10,
    "political_power_gain": -0.10,
})
mod.set_loc("sicilian_resistance", "Sicilian Resistance")
mod.set_loc("sicilian_resistance_desc",
    "The people of Sicily have risen against Italian rule. "
    "Determination is high, but the fledgling government struggles to maintain authority.")

mod.create_idea("etna_stronghold", modifier={
    "dig_in_speed_factor": 0.25,
    "supply_consumption_factor": -0.10,
    "heat_attrition_factor": -0.15,
})
mod.set_loc("etna_stronghold", "Fortress Etna")
mod.set_loc("etna_stronghold_desc",
    "Mount Etna has been transformed into a natural fortress. "
    "Our troops dig in deep and the volcanic terrain exhausts any invader.")

# ═══════════════════════════════════════════════════════════════
# Main Event: Sicilian Revolution (Feb 28, 1936)
# ═══════════════════════════════════════════════════════════════

rev_event = mod.create_event(
    "sicily_revolution.1",
    title="sicily_revolution.1.t",
    description="sicily_revolution.1.d",
    event_type="country_event",
    picture="GFX_report_event_generic_italy",
    is_triggered_only=False,
    trigger=(
        "tag = ITA\n"
        "date > 1936.2.27\n"
        "has_war = no\n"
        "has_country_flag = SCL_revolution_fired = no\n"
        "controls_state = 115"
    ),
    mean_time_to_happen="days = 1",
    options=[
        EventOption(
            name="sicily_revolution.1.a",
            effect=(
                "set_country_flag = SCL_revolution_fired\n"
                "SCL = { transfer_state = 115 }\n"
                "SCL = { add_core_of = SCL }\n"
                "custom_effect_tooltip = SCL_revolution_suppress_tt\n"
                "hidden_effect = {\n"
                "    SCL = { add_political_power = 50 }\n"
                "}\n"
                "declare_war_on = { target = SCL type = annex_everything }"
            ),
        ),
    ],
)
mod.set_event_namespace("sicily_revolution.1", "sicily_revolution")
mod.update_event("sicily_revolution.1", fire_only_once=True)

# Transfer state 115 to SCL core via SDK
mod.set_state_owner(115, "SCL", add_core=True)

# ═══════════════════════════════════════════════════════════════
# Revolution Spawn Events (triggered by SCL focus tree)
# ═══════════════════════════════════════════════════════════════

def create_uprising_event(event_id, target_tag, state_id, region_name, picture):
    evt = mod.create_event(
        event_id,
        title=f"{event_id}.t",
        description=f"{event_id}.d",
        event_type="country_event",
        picture=picture,
        is_triggered_only=True,
        options=[
            EventOption(
                name=f"{event_id}.a",
                effect=(
                    f"{target_tag} = {{ transfer_state = {state_id} }}\n"
                    f"{target_tag} = {{ add_core_of = {target_tag} }}\n"
                    f"{target_tag} = {{ add_political_power = 50 }}\n"
                    f"declare_war_on = {{ target = {target_tag} type = annex_everything }}\n"
                    f"set_country_flag = {target_tag}_uprising_happened"
                ),
            ),
        ],
    )
    mod.set_event_namespace(event_id, "sicily_revolution")
    mod.set_state_owner(state_id, target_tag, add_core=True)
    return evt

create_uprising_event("sicily_revolution.2", "SRD", 114, "Sardinia",
                      "GFX_report_event_generic_mediterranean")
create_uprising_event("sicily_revolution.3", "VNT", 160, "Veneto",
                      "GFX_report_event_generic_europe")
create_uprising_event("sicily_revolution.4", "ZAR", 163, "Zara",
                      "GFX_report_event_generic_europe")

# ═══════════════════════════════════════════════════════════════
# Flavor Events
# ═══════════════════════════════════════════════════════════════

# Italian response event
ita_response = mod.create_event(
    "sicily_revolution.5",
    title="sicily_revolution.5.t",
    description="sicily_revolution.5.d",
    event_type="news_event",
    picture="GFX_report_event_generic_italy",
    is_triggered_only=True,
    options=[
        EventOption(
            name="sicily_revolution.5.a",
            effect="add_political_power = -25",
        ),
    ],
)
mod.set_event_namespace("sicily_revolution.5", "sicily_revolution")

# International reaction
intl_reaction = mod.create_event(
    "sicily_revolution.6",
    title="sicily_revolution.6.t",
    description="sicily_revolution.6.d",
    event_type="news_event",
    picture="GFX_report_event_generic_europe",
    is_triggered_only=True,
    options=[
        EventOption(name="sicily_revolution.6.a", effect="add_stability = -0.02"),
    ],
)
mod.set_event_namespace("sicily_revolution.6", "sicily_revolution")

# ═══════════════════════════════════════════════════════════════
# Focus Tree: Sicily
# ═══════════════════════════════════════════════════════════════

tree = mod.create_focus_tree("scl_focus", "SCL")
print(f"Focus tree: {tree.id} for {tree.country_tag}")

# --- Row 0: Starting focuses ---
mod.add_focus("scl_focus", Focus(
    id="SCL_sicilian_identity", x=5, y=0, cost=10,
    icon="GFX_goal_generic_national_unity",
    search_filters=["FOCUS_FILTER_POLITICAL"],
    completion_reward=(
        "add_stability = 0.10\n"
        "add_war_support = 0.05\n"
        "add_political_power = 100"
    ),
))
mod.add_focus("scl_focus", Focus(
    id="SCL_seek_recognition", x=10, y=0, cost=10,
    icon="GFX_goal_generic_diplomatic_treaty",
    prerequisites=[["SCL_sicilian_identity"]],
    search_filters=["FOCUS_FILTER_POLITICAL"],
    available="has_war_with = ITA",
    completion_reward=(
        "add_political_power = 50\n"
        "every_country = {\n"
        "    limit = { has_war_with = ITA }\n"
        "    add_opinion_modifier = { target = SCL modifier = positive_50 }\n"
        "}"
    ),
))

# --- Row 1: Industrial branch (auto-generated, anchored to map_domestic_industry) ---
ind_branch = mod.create_industrial_branch("scl_focus", "SCL")

# --- Row 1 (parallel): Military/political ---
mod.add_focus("scl_focus", Focus(
    id="SCL_peoples_militia", x=5, y=1, cost=10,
    icon="GFX_goal_generic_army_doctrine",
    prerequisites=[["SCL_sicilian_identity"]],
    search_filters=["FOCUS_FILTER_MANPOWER"],
    completion_reward=(
        "add_manpower = 50000\n"
        "add_army_experience = 30\n"
        "set_rule = { can_create_faction = yes }"
    ),
))

mod.add_focus("scl_focus", Focus(
    id="SCL_arms_smuggling", x=10, y=1, cost=10,
    icon="GFX_goal_generic_army_artillery",
    prerequisites=[["SCL_peoples_militia"]],
    search_filters=["FOCUS_FILTER_INDUSTRY"],
    completion_reward=(
        "add_equipment_to_stockpile = {\n"
        "    type = infantry_equipment_0\n"
        "    amount = 2000\n"
        "}\n"
        "add_equipment_to_stockpile = {\n"
        "    type = artillery_equipment_0\n"
        "    amount = 200\n"
        "}"
    ),
))

# --- Row 2: Defense ---
mod.add_focus("scl_focus", Focus(
    id="SCL_fortify_messina", x=5, y=2, cost=10,
    icon="GFX_goal_generic_fortify_city",
    prerequisites=[["SCL_peoples_militia"]],
    search_filters=["FOCUS_FILTER_INDUSTRY"],
    completion_reward=(
        "115 = {\n"
        "    add_building_construction = {\n"
        "        type = coastal_bunker level = 3 instant_build = yes\n"
        "    }\n"
        "    add_building_construction = {\n"
        "        type = bunker level = 2 instant_build = yes\n"
        "    }\n"
        "}"
    ),
))

mod.add_focus("scl_focus", Focus(
    id="SCL_corsican_raiders", x=10, y=2, cost=10,
    icon="GFX_goal_generic_navy_submarine",
    prerequisites=[["SCL_arms_smuggling"]],
    search_filters=["FOCUS_FILTER_INDUSTRY", "FOCUS_FILTER_MANPOWER"],
    completion_reward=(
        "add_navy_experience = 50\n"
        "add_equipment_to_stockpile = {\n"
        "    type = submarine_0\n"
        "    amount = 3\n"
        "}\n"
        "create_naval_leader = {\n"
        "    name = \"Salvatore Todaro\"\n"
        "    skill = 3\n"
        "    traits = { seawolf }\n"
        "}"
    ),
))

# --- Row 3: Defensive/Resistance ---
mod.add_focus("scl_focus", Focus(
    id="SCL_etna_stronghold", x=5, y=3, cost=10,
    icon="GFX_goal_generic_defend_country",
    prerequisites=[["SCL_fortify_messina"]],
    search_filters=["FOCUS_FILTER_POLITICAL"],
    completion_reward=(
        "add_timed_idea = { idea = etna_stronghold days = 3650 }\n"
        "add_stability = 0.05\n"
        "add_war_support = 0.05"
    ),
))

mod.add_focus("scl_focus", Focus(
    id="SCL_call_to_arms", x=10, y=3, cost=10,
    icon="GFX_goal_generic_allies_build_infantry",
    prerequisites=[["SCL_corsican_raiders"]],
    search_filters=["FOCUS_FILTER_MANPOWER"],
    completion_reward=(
        "add_manpower = 75000\n"
        "army_experience = 0.02\n"
        "add_war_support = 0.10"
    ),
))

# --- Row 4: Liberation branch ---
mod.add_focus("scl_focus", Focus(
    id="SCL_spark_sardinia", x=5, y=4, cost=10,
    icon="GFX_goal_generic_territory_or_war",
    prerequisites=[["SCL_etna_stronghold"]],
    search_filters=["FOCUS_FILTER_POLITICAL"],
    available="has_war_with = ITA",
    will_lead_to_war_with="ITA",
    completion_reward="ITA = { country_event = { id = sicily_revolution.2 } }",
))

mod.add_focus("scl_focus", Focus(
    id="SCL_spark_veneto", x=5, y=5, cost=10,
    icon="GFX_goal_generic_territory_or_war",
    prerequisites=[["SCL_spark_sardinia"]],
    search_filters=["FOCUS_FILTER_POLITICAL"],
    available="has_war_with = ITA",
    will_lead_to_war_with="ITA",
    completion_reward="ITA = { country_event = { id = sicily_revolution.3 } }",
))

mod.add_focus("scl_focus", Focus(
    id="SCL_spark_zara", x=5, y=6, cost=10,
    icon="GFX_goal_generic_territory_or_war",
    prerequisites=[["SCL_spark_veneto"]],
    search_filters=["FOCUS_FILTER_POLITICAL"],
    available="has_war_with = ITA",
    will_lead_to_war_with="ITA",
    completion_reward="ITA = { country_event = { id = sicily_revolution.4 } }",
))

# --- Row 5: Peace negotiations (alternative path) ---
mod.add_focus("scl_focus", Focus(
    id="SCL_negotiate_ceasefire", x=0, y=4, cost=10,
    icon="GFX_goal_generic_diplomatic_treaty",
    prerequisites=[["SCL_modernize_transport_links"]],
    search_filters=["FOCUS_FILTER_POLITICAL"],
    available=(
        "has_war_with = ITA\n"
        "has_army_manpower = { ratio > 0.3 }\n"
    ),
    ai_will_do="factor = 0.5",
    completion_reward=(
        "ITA = { country_event = { id = sicily_revolution.7 hours = 6 } }"
    ),
))

# Peace offer event for Italy
peace_event = mod.create_event(
    "sicily_revolution.7",
    title="sicily_revolution.7.t",
    description="sicily_revolution.7.d",
    event_type="country_event",
    picture="GFX_report_event_generic_italy",
    is_triggered_only=True,
    options=[
        EventOption(
            name="sicily_revolution.7.a",
            effect=(
                "SCL = { white_peace = ITA }\n"
                "SCL = { add_political_power = 100 }\n"
                "SCL = { add_stability = 0.20 }\n"
                "SCL = { add_opinion_modifier = { target = ITA modifier = positive_100 } }\n"
                "set_country_flag = SCL_peace_signed"
            ),
        ),
        EventOption(
            name="sicily_revolution.7.b",
            effect=(
                "add_war_support = 0.05\n"
                "add_political_power = 25"
            ),
            ai_chance="factor = 0",
        ),
    ],
)
mod.set_event_namespace("sicily_revolution.7", "sicily_revolution")

# --- Row 5: United separatists ---
mod.add_focus("scl_focus", Focus(
    id="SCL_united_separatists", x=5, y=7, cost=10,
    icon="GFX_goal_generic_alliance",
    prerequisites=[["SCL_spark_zara"]],
    search_filters=["FOCUS_FILTER_POLITICAL"],
    available=(
        "has_country_flag = SCL_allied_SRD\n"
        "has_country_flag = SCL_allied_VNT\n"
        "has_country_flag = SCL_allied_ZAR"
    ),
    completion_reward=(
        "add_stability = 0.15\n"
        "add_war_support = 0.10\n"
        "add_political_power = 150\n"
        "ITA = { add_stability = -0.10 }\n"
        "world_tension = 2"
    ),
))

# ═══════════════════════════════════════════════════════════════
# Focus Localization
# ═══════════════════════════════════════════════════════════════

focus_loc = {
    "SCL_sicilian_identity": (
        "Sicilian Identity", "Forge a national consciousness distinct from Rome. "
        "Sicily has its own language, culture, and history stretching back millennia."
    ),
    "SCL_seek_recognition": (
        "Seek International Recognition", "Win diplomatic support from nations "
        "opposed to Italian expansion. The world must acknowledge our struggle."
    ),
    "SCL_peoples_militia": (
        "The People's Militia", "Every able-bodied Sicilian must be ready to "
        "defend the homeland. Establish a decentralized militia structure."
    ),
    "SCL_arms_smuggling": (
        "Arms Smuggling Networks", "Weapons flow through Malta, Tunisia, and "
        "the underground. Tap into Mediterranean arms trafficking to equip our forces."
    ),
    "SCL_fortify_messina": (
        "Fortify the Strait of Messina", "The narrow strait is our shield. "
        "Construct coastal batteries and bunkers to make any Italian landing a bloodbath."
    ),
    "SCL_corsican_raiders": (
        "Corsican Raider Flotilla", "Recruit exiled naval officers and "
        "smugglers into a guerrilla submarine force. Hit Italian shipping in the Tyrrhenian."
    ),
    "SCL_etna_stronghold": (
        "Fortress Etna", "Turn Mount Etna into an impregnable redoubt. "
        "The volcanic terrain provides natural defenses no army can easily overcome."
    ),
    "SCL_call_to_arms": (
        "Call to Arms", "Issue a general mobilization. Sicily's survival "
        "depends on every son and daughter taking up the struggle."
    ),
    "SCL_spark_sardinia": (
        "Spark Sardinian Revolution", "Sardinia has long chafed under Italian rule. "
        "Send agents and arms to ignite a sister revolution in Cagliari."
    ),
    "SCL_spark_veneto": (
        "Spark Venetian Revolution", "Venice was once the jewel of the Mediterranean. "
        "Fan the embers of Venetian separatism and open a northern front."
    ),
    "SCL_spark_zara": (
        "Spark Zaran Uprising", "The Dalmatian enclave of Zara is isolated and "
        "vulnerable. A well-timed uprising could draw Italian forces to the Adriatic."
    ),
    "SCL_united_separatists": (
        "United Separatist Front", "With Sardinia, Veneto, and Zara at our side, "
        "we present a united front against Italian domination. Together we stand."
    ),
    "SCL_negotiate_ceasefire": (
        "Negotiate a Ceasefire", "The bloodshed has gone on long enough. "
        "Offer Rome a face-saving exit — our independence in exchange for peace."
    ),
}

for fid, (name, desc) in focus_loc.items():
    mod.set_focus_loc(fid, name, desc)

# ═══════════════════════════════════════════════════════════════
# Event/General Localization
# ═══════════════════════════════════════════════════════════════

event_loc = {
    "sicily_revolution.1.t": "Sicilian Revolution!",
    "sicily_revolution.1.d": (
        "On February 28th, 1936, separatist forces led by the Sicilian Independence "
        "Movement seized control of Palermo. The triskelion flag now flies over Sicily, "
        "and the rebels have declared an independent Sicilian Republic. Rome must respond."
    ),
    "sicily_revolution.1.a": "Crush the rebellion!",
    "sicily_revolution.2.t": "Sardinia Rises Up!",
    "sicily_revolution.2.d": (
        "Inspired by the Sicilian example, Sardinian nationalists have taken to the "
        "streets of Cagliari. The Four Moors flag waves once more over the island."
    ),
    "sicily_revolution.2.a": "Another front opens...",
    "sicily_revolution.3.t": "Venice Declares Independence!",
    "sicily_revolution.3.d": (
        "Venetian separatists, emboldened by the rebellions in the south, have "
        "proclaimed the restoration of the Most Serene Republic of Venice."
    ),
    "sicily_revolution.3.a": "The north rises too...",
    "sicily_revolution.4.t": "Zara in Revolt!",
    "sicily_revolution.4.d": (
        "The Dalmatian city of Zara has erupted in revolt. Cut off from the Italian "
        "mainland, the garrison struggles to maintain control."
    ),
    "sicily_revolution.4.a": "Even Zara burns...",
    "sicily_revolution.5.t": "Italy Faces Separatist Crisis",
    "sicily_revolution.5.d": (
        "The Sicilian revolution has sent shockwaves through the Italian peninsula. "
        "Regionalist movements in Sardinia, Veneto, and Dalmatia grow bolder by the day."
    ),
    "sicily_revolution.5.a": "A dark day for Italy.",
    "sicily_revolution.6.t": "Mediterranean Separatism Spreads",
    "sicily_revolution.6.d": (
        "The Sicilian revolt has inspired separatist movements across the Mediterranean. "
        "The old order of nation-states faces a new challenge."
    ),
    "sicily_revolution.6.a": "The world watches.",
    "sicily_revolution.7.t": "Sicily Offers Peace",
    "sicily_revolution.7.d": (
        "The Sicilian government has extended an olive branch. They offer to cease "
        "hostilities in exchange for full recognition of their independence."
    ),
    "sicily_revolution.7.a": "Accept their independence.",
    "sicily_revolution.7.b": "Never! Sicily is Italian!",
}

for key, value in event_loc.items():
    mod.set_loc(key, value)

# Additional tooltip localization
mod.set_loc("SCL_revolution_suppress_tt",
    "Sicily declares independence and seizes control of the island.")

print("Mod built. Running validation...")
print("=" * 60)

# ═══════════════════════════════════════════════════════════════
# Validation
# ═══════════════════════════════════════════════════════════════

errors = mod.validate()
error_count = 0
warning_count = 0
for e in errors:
    if e.severity == "error":
        error_count += 1
        print(f"[ERROR] {e.message}")
    else:
        warning_count += 1
        print(f"[WARN]  {e.message}")

print(f"\n{error_count} errors, {warning_count} warnings")

# ═══════════════════════════════════════════════════════════════
# Preview
# ═══════════════════════════════════════════════════════════════

preview = mod.preview()
print("\n" + "=" * 60)
print("PREVIEW (first 400 lines):")
print("=" * 60)
preview_lines = preview.split("\n")
print("\n".join(preview_lines[:400]))
if len(preview_lines) > 400:
    print(f"\n... ({len(preview_lines) - 400} more lines)")

# Decision tooltip localization
for key, value in {
    "SCL_ally_sardinia_tt": "Sardinia joins the Mediterranean Separatist League.",
    "SCL_ally_veneto_tt": "Veneto joins the Mediterranean Separatist League.",
    "SCL_ally_zara_tt": "Zara joins the Mediterranean Separatist League.",
}.items():
    mod.set_loc(key, value)

print("\n" + "=" * 60)
print("Saving to disk...")
print("=" * 60)

mod.save()
print("Saved.")

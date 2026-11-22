"""The public API: what it holds, and that every callable in it is documented and typed."""

from __future__ import annotations

import ast
import dataclasses
import inspect
import re
import textwrap
from collections.abc import Callable, Iterable, Iterator
from typing import Any

import hoi4
import hoi4.fields
from hoi4 import Mod, effects


def test_root_api_exposes_project_and_asset_workflows_without_optional_dependencies() -> None:
    expected = {
        "create_mod_structure",
        "detect_launcher_mod_directory",
        "DynamicModifier",
        "export_flag_from_mod",
        "export_portrait_from_mod",
        "ExternalModificationError",
        "find_flag_path",
        "find_portrait_path",
        "GeminiImageGenerator",
        "GeminiImageResult",
        "MapRenderCancelled",
        "Character",
        "CharacterInstance",
        "CharacterPortrait",
        "CountryLeaderRole",
        "AdvisorRole",
        "ArmyCommanderRole",
        "NavyLeaderRole",
        "OrderOfBattle",
        "OOBReference",
        "EquipmentVariant",
        "DivisionTemplate",
        "DivisionUnit",
        "Battalion",
        "Fleet",
        "TaskForce",
        "Ship",
        "ShipEquipment",
        "AirWing",
        "CountryPackageReport",
        "ContentLivenessReport",
        "GameScriptVocabulary",
        "ScriptTokenInfo",
        "ScriptSource",
        "load_game_script_vocabulary",
        "TerritoryComponent",
        "scan_mod_descriptors",
    }

    assert expected <= set(hoi4.__all__)
    # The immediate-write asset functions live in hoi4.assets only; Mod has
    # staged methods of the same names.
    for name in ("import_flag_to_mod", "import_portrait_to_mod", "write_portrait_gfx"):
        assert name not in hoi4.__all__
        assert not hasattr(hoi4, name)
    assert not hasattr(hoi4, "version")
    assert not hasattr(hoi4, "PackageNotFoundError")
    assert all(callable(getattr(hoi4, name)) for name in expected)
    assert "ValidationStage" in hoi4.__all__


# ── Every public callable is documented and annotated ───────────────
#
# Public means: every public method and property of ``Mod``, every function
# in ``hoi4.effects``, every function and class in ``hoi4.__all__``, and the
# public methods and properties those classes define themselves.


def _public_callables() -> Iterator[tuple[str, Any, bool]]:
    """``(qualified name, object, is_builder)`` for every public callable."""

    for name in sorted(dir(Mod)):
        if name.startswith("_"):
            continue
        member = inspect.getattr_static(Mod, name)
        if isinstance(member, (property, classmethod, staticmethod)) or inspect.isfunction(member):
            yield f"Mod.{name}", member, False
    for name in sorted(effects.__all__):
        yield f"effects.{name}", getattr(effects, name), True
    for name in sorted(hoi4.__all__):
        value = getattr(hoi4, name)
        if isinstance(value, type):
            yield name, value, False
            if value is Mod:
                continue
            for member_name, member in sorted(vars(value).items()):
                if member_name.startswith("_"):
                    continue
                if isinstance(member, (property, classmethod, staticmethod)) or inspect.isfunction(
                    member
                ):
                    yield f"{name}.{member_name}", member, False
        elif inspect.isfunction(value):
            yield name, value, False


def _function(member: Any) -> Callable[..., Any] | None:
    if isinstance(member, property):
        return member.fget
    if isinstance(member, (classmethod, staticmethod)):
        return member.__func__
    if inspect.isfunction(member):
        return member
    return None


def _own_doc(member: Any) -> str:
    if isinstance(member, type):
        doc = member.__dict__.get("__doc__") or ""
        if dataclasses.is_dataclass(member) and doc.startswith(f"{member.__name__}("):
            return ""  # the signature dataclasses writes when there is no docstring
        return inspect.cleandoc(doc)
    function = _function(member)
    return inspect.cleandoc((function.__doc__ if function else None) or "")


def _missing_annotations(member: Any) -> list[str]:
    if isinstance(member, type):
        init = next(
            (c.__dict__["__init__"] for c in member.__mro__ if "__init__" in c.__dict__), None
        )
        if init is None or not inspect.isfunction(init):
            return []  # a builtin __init__ (exceptions, enums): nothing to annotate
        signature = inspect.signature(init)
        check_return = False
    else:
        function = _function(member)
        assert function is not None
        signature = inspect.signature(function)
        check_return = True
    missing = [
        parameter.name
        for index, parameter in enumerate(signature.parameters.values())
        if parameter.annotation is inspect.Parameter.empty
        and not (index == 0 and parameter.name in {"self", "cls"})
    ]
    if check_return and signature.return_annotation is inspect.Signature.empty:
        missing.append("return")
    return missing


_BARE_GENERIC = re.compile(r"(?<![\w.])(dict|list|tuple|set|frozenset|Sequence|Mapping)\b(?!\[)")


def _bare_generics(member: Any) -> list[str]:
    function = member.__init__ if isinstance(member, type) else _function(member)
    if function is None or not inspect.isfunction(function):
        return []
    annotations = getattr(function, "__annotations__", {})
    return [
        f"{name}: {annotation}"
        for name, annotation in annotations.items()
        if isinstance(annotation, str) and _BARE_GENERIC.search(annotation)
    ]


def _can_fail(member: Any, *, mod_method: bool) -> bool:
    """Whether the body raises, or (for a ``Mod`` method) calls a ``Mod`` lookup that raises."""

    function = _function(member)
    if function is None:
        return False
    try:
        source = textwrap.dedent(inspect.getsource(function))
    except (OSError, TypeError):
        return False
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Raise):
            return True
        if (
            mod_method
            and isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
            and (node.func.attr.startswith(("get_", "_require")))
            and not any(keyword.arg == "default" for keyword in node.keywords)
        ):
            return True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "not_found":
            return True
    return False


PUBLIC = list(_public_callables())


def test_every_public_callable_has_a_docstring() -> None:
    missing = [name for name, member, _ in PUBLIC if not _own_doc(member)]
    assert missing == [], f"{len(missing)} public callables without a docstring: {missing}"


def test_every_public_callable_that_can_fail_says_what_it_raises() -> None:
    silent = [
        name
        for name, member, builder in PUBLIC
        if not builder
        and not isinstance(member, type)
        and _can_fail(member, mod_method=name.startswith("Mod."))
        and "Raises:" not in _own_doc(member)
    ]
    assert silent == [], f"{len(silent)} callables that can fail without a Raises: section: {silent}"


def test_every_public_callable_is_fully_annotated() -> None:
    incomplete = {
        name: missing for name, member, _ in PUBLIC if (missing := _missing_annotations(member))
    }
    assert incomplete == {}, f"{len(incomplete)} callables with missing annotations: {incomplete}"


def test_no_public_signature_uses_a_bare_container_type() -> None:
    bare = {name: found for name, member, _ in PUBLIC if (found := _bare_generics(member))}
    assert bare == {}, f"{len(bare)} callables with bare container types: {bare}"


# ── The frozen list of public names ─────────────────────────────────
#
# Adding or removing a public name is a deliberate change: edit the list
# below in the same commit (and, for a removal or a rename, add the old name
# to scripts/check_removed_api_names.py and the migration table in
# CHANGELOG.md).

MOD_NAMES = [
    "add_bookmark_country",
    "add_character_instance",
    "add_character_role",
    "add_event_option",
    "add_focus",
    "add_map_building",
    "add_railway",
    "add_state_core",
    "add_supply_node",
    "all_loc",
    "analyze_content_liveness",
    "append_to_focus_reward",
    "assert_no_visual_overlap",
    "assign_country_oob",
    "auto_layout_branch",
    "available_leader_ideologies",
    "available_ruling_parties",
    "content_files",
    "copy_country_states_to_mod",
    "copy_loc_to_languages",
    "country_tag_conflicts",
    "create_ai_strategy",
    "create_bookmark",
    "create_character",
    "create_country",
    "create_decision",
    "create_decision_category",
    "create_decision_chain",
    "create_definition",
    "create_dynamic_modifier",
    "create_equipment_variant",
    "create_event",
    "create_focus_tree",
    "create_idea",
    "create_ideology",
    "create_industrial_branch",
    "create_mission",
    "create_on_action",
    "create_oob",
    "create_opinion_modifier",
    "create_recovery_decision",
    "create_scripted_localisation",
    "create_state",
    "create_static_modifier",
    "create_strategic_region",
    "create_targeted_decision",
    "default_loc_file",
    "definition_kinds",
    "delete_bookmark",
    "delete_bookmark_country",
    "delete_character",
    "delete_character_instance",
    "delete_country",
    "delete_decision",
    "delete_definition",
    "delete_dynamic_modifier",
    "delete_event",
    "delete_focus_tree",
    "delete_idea",
    "delete_ideology",
    "delete_loc",
    "delete_on_action",
    "delete_oob",
    "discard",
    "ensure_decision",
    "ensure_decision_category",
    "ensure_event",
    "ensure_focus",
    "ensure_focus_tree",
    "ensure_idea",
    "ensure_on_action",
    "find_disconnected_states",
    "find_enclosed_foreign_states",
    "find_state",
    "focus_tree_bounds",
    "from_config",
    "game_script_vocabulary",
    "get_bookmark",
    "get_character",
    "get_country",
    "get_country_context",
    "get_decision",
    "get_decision_category",
    "get_definition",
    "get_dynamic_modifier",
    "get_event",
    "get_focus",
    "get_focus_tree",
    "get_idea",
    "get_ideology",
    "get_loc",
    "get_on_action",
    "get_on_action_occurrences",
    "get_oob",
    "get_state",
    "get_state_name_map",
    "get_strategic_region",
    "import_decision_category_icon",
    "import_decision_category_picture",
    "import_decision_icon",
    "import_event_picture",
    "import_flag_to_mod",
    "import_focus_icon",
    "import_idea_icon",
    "import_portrait_to_mod",
    "import_script_file",
    "insert_branch",
    "insert_focus_after",
    "is_country_tag_available",
    "list_bookmarks",
    "list_characters",
    "list_countries",
    "list_decision_categories",
    "list_decisions",
    "list_definitions",
    "list_dynamic_modifiers",
    "list_events",
    "list_focus_trees",
    "list_ideas",
    "list_ideologies",
    "list_map_buildings",
    "list_on_actions",
    "list_oobs",
    "list_railways",
    "list_states",
    "list_strategic_regions",
    "list_supply_nodes",
    "load_diagnostics",
    "load_inherited_content",
    "move_provinces",
    "move_provinces_to_strategic_region",
    "place_continuous_focus_below_tree",
    "preview",
    "preview_summary",
    "rebuild_country_color_table",
    "reload",
    "remove_character_role",
    "remove_focus",
    "remove_map_buildings",
    "remove_railway",
    "remove_state_core",
    "remove_supply_node",
    "resolve_content_path",
    "save",
    "search_loc",
    "set_bookmark_date_range",
    "set_country_name_pool",
    "set_default_idea_file",
    "set_event_namespace",
    "set_focus_loc",
    "set_focuses_mutually_exclusive",
    "set_loc",
    "set_railway_level",
    "set_state_owner",
    "split_state",
    "state_index",
    "suggest_focus_icons",
    "suggest_idea_icons",
    "suggest_tags",
    "transaction",
    "unassign_country_oob",
    "update_bookmark",
    "update_bookmark_country",
    "update_character",
    "update_character_instance",
    "update_character_role",
    "update_country",
    "update_decision",
    "update_decision_category",
    "update_definition",
    "update_dynamic_modifier",
    "update_event",
    "update_event_option",
    "update_focus",
    "update_focus_tree",
    "update_idea",
    "update_ideology",
    "update_on_action",
    "update_oob",
    "update_state",
    "validate",
    "validate_country_package",
    "validate_effect",
    "validate_game_log",
    "validate_script_vocabulary",
    "write_portrait_gfx",
]

HOI4_NAMES = [
    "AdvisorRole",
    "AirWing",
    "AlreadyExistsError",
    "ArmyCommanderRole",
    "Battalion",
    "Bookmark",
    "BookmarkCountry",
    "BookmarkPictureAsset",
    "COUNTRY_PACKAGE_REQUIREMENT_CODES",
    "Character",
    "CharacterInstance",
    "CharacterPortrait",
    "CommonsImage",
    "CommonsImageClient",
    "CommonsImageError",
    "Config",
    "ContentLivenessReport",
    "Country",
    "CountryLeaderRole",
    "CountryPackageReport",
    "DDSExportUnsupportedError",
    "DEFAULT_GEMINI_IMAGE_MODEL",
    "DEFAULT_GEMINI_IMAGE_SIZE",
    "DEFINITION_KINDS",
    "Decision",
    "DecisionCategory",
    "DefinitionKind",
    "DiscoveredMod",
    "DivisionTemplate",
    "DivisionUnit",
    "DownloadedCommonsImage",
    "DynamicModifier",
    "EFFECT_CATEGORIES",
    "EquipmentVariant",
    "Event",
    "EventOption",
    "EventText",
    "ExternalModificationError",
    "FlagAssetSet",
    "Fleet",
    "Focus",
    "FocusTree",
    "GameLogEntry",
    "GameLogReport",
    "GameScriptVocabulary",
    "GeminiAuthenticationError",
    "GeminiBackendUnavailableError",
    "GeminiImageError",
    "GeminiImageGenerationError",
    "GeminiImageGenerator",
    "GeminiImageResponseError",
    "GeminiImageResult",
    "Hoi4Error",
    "ICON_KINDS",
    "IconAsset",
    "Idea",
    "Ideology",
    "ImageBackendUnavailableError",
    "Leader",
    "LoadDiagnostic",
    "MODIFIER_CATEGORIES",
    "MapBuilding",
    "MapEditReport",
    "MapRenderCancelled",
    "Mod",
    "ModDescriptorFiles",
    "ModDiscoveryIssue",
    "ModDiscoveryResult",
    "NavyLeaderRole",
    "NotFoundError",
    "OOBKind",
    "OOBReference",
    "OnAction",
    "OperationCancelled",
    "OrderOfBattle",
    "ParseError",
    "PdxNode",
    "PoliticalMapResult",
    "ProgressEvent",
    "Railway",
    "ReadOnlyError",
    "SaveResult",
    "ScriptDefinition",
    "ScriptSource",
    "ScriptTokenInfo",
    "Ship",
    "ShipEquipment",
    "State",
    "StrategicRegion",
    "SubIdeology",
    "TECHNOLOGY_CATEGORIES",
    "TaskForce",
    "TerritoryComponent",
    "UnsupportedImageFormatError",
    "VALIDATION_CODES",
    "VALIDATION_ERROR_CODES",
    "VALIDATION_INFO_CODES",
    "VALIDATION_SEVERITIES",
    "VALIDATION_WARNING_CODES",
    "ValidationIssue",
    "ValidationStage",
    "__version__",
    "create_mod_structure",
    "detect_launcher_mod_directory",
    "discover_mods",
    "effects",
    "export_flag_from_mod",
    "export_portrait_from_mod",
    "find_config",
    "find_flag_path",
    "find_portrait_path",
    "format_game_log_report",
    "import_bookmark_picture_to_mod",
    "import_icon_to_mod",
    "load_game_script_vocabulary",
    "normalize_block_body",
    "parse_hoi4_error_log",
    "parse_pdx",
    "pdx_string",
    "pdx_value",
    "render_focus_shine_sprite",
    "render_political_map",
    "render_sprite_type",
    "scan_mod_descriptors",
    "serialize_pdx",
    "upsert_sprite_type",
    "validate_script_syntax",
    "write_mod_descriptors",
]

EFFECTS_NAMES = [
    "add_air_experience",
    "add_army_experience",
    "add_building",
    "add_bunker",
    "add_civilian_factory",
    "add_coastal_bunker",
    "add_dynamic_modifier",
    "add_equipment",
    "add_industry_bonus",
    "add_infrastructure",
    "add_manpower",
    "add_military_factory",
    "add_navy_experience",
    "add_political_power",
    "add_stability",
    "add_state_building",
    "add_state_core",
    "add_target_to_faction",
    "add_tech_bonus",
    "add_timed_idea",
    "add_to_faction",
    "add_war_support",
    "block",
    "convert_existing_or_spawn_revolt",
    "convert_puppet_to_ally",
    "create_faction",
    "create_unit",
    "create_wargoal",
    "declare_war",
    "declare_war_from",
    "division_template",
    "end_puppet",
    "force_update_dynamic_modifier",
    "join_faction",
    "load_focus_tree",
    "release",
    "release_puppet",
    "remove_dynamic_modifier",
    "remove_state_core",
    "schedule_country_event",
    "scope_block",
    "set_autonomy",
    "set_politics",
    "set_rule",
    "set_technologies",
    "set_technology",
    "spawn_civil_war_with_focus_tree",
    "spawn_revolution",
    "start_civil_war",
    "swap_idea",
    "transfer_state",
    "transfer_state_with_core",
    "upgrade_idea_chain",
    "white_peace",
]

FIELDS_NAMES = [
    "BookmarkCountryFields",
    "BookmarkFields",
    "CharacterFields",
    "CharacterInstanceFields",
    "CharacterRoleFields",
    "CountryFields",
    "CreateStateOptions",
    "DecisionCategoryFields",
    "DecisionFields",
    "DynamicModifierFields",
    "EnsureDecisionCategoryFields",
    "EnsureDecisionFields",
    "EnsureEventFields",
    "EnsureIdeaFields",
    "EnsureOnActionFields",
    "EventFields",
    "EventOptionFields",
    "FocusFields",
    "FocusTreeFields",
    "IdeaFields",
    "IdeologyFields",
    "MissionOptions",
    "OnActionFields",
    "StateFields",
    "TargetedDecisionOptions",
]


def _compare(label: str, frozen: Iterable[str], actual: Iterable[str]) -> None:
    frozen_set, actual_set = set(frozen), set(actual)
    added = sorted(actual_set - frozen_set)
    removed = sorted(frozen_set - actual_set)
    message = [f"The public names of {label} changed."]
    if added:
        message.append(f"Added (add them to the list if intended): {added}")
    if removed:
        message.append(f"Removed (take them off the list if intended): {removed}")
    assert not added and not removed, " ".join(message)
    assert list(frozen) == sorted(frozen), f"Keep the list for {label} sorted."


def test_mod_public_names_are_the_frozen_list() -> None:
    _compare("Mod", MOD_NAMES, (name for name in dir(Mod) if not name.startswith("_")))


def test_hoi4_all_is_the_frozen_list() -> None:
    _compare("hoi4.__all__", HOI4_NAMES, hoi4.__all__)
    assert len(hoi4.__all__) == len(set(hoi4.__all__)), "hoi4.__all__ names a name twice"


def test_effects_all_is_the_frozen_list() -> None:
    _compare("hoi4.effects.__all__", EFFECTS_NAMES, effects.__all__)


def test_fields_all_is_the_frozen_list() -> None:
    _compare("hoi4.fields.__all__", FIELDS_NAMES, hoi4.fields.__all__)

from __future__ import annotations

from pathlib import Path

import pytest

from hoi4 import Mod
from hoi4.characters import (
    AdvisorRole,
    ArmyCommanderRole,
    Character,
    CharacterInstance,
    CharacterPortrait,
    CountryLeaderRole,
    load_characters_file,
    serialize_characters_file,
)


CHARACTERS = """# retain header
characters = {
    ABC_leader = {
        name = ABC_LEADER
        custom_character_key = keep
        portraits = {
            civilian = { large = GFX_portrait_ABC_leader }
        }
        country_leader = {
            ideology = liberalism
            expire = "1965.1.1.1"
            id = -1
        }
    }
    ABC_variant = {
        instance = {
            allowed = { NOT = { has_dlc = "Example DLC" } }
            name = ABC_VARIANT
            corps_commander = {
                skill = 3
                attack_skill = 4
                defense_skill = 2
                planning_skill = 3
                logistics_skill = 2
                legacy_id = -1
            }
        }
        instance = {
            allowed = { has_dlc = "Example DLC" }
            name = ABC_VARIANT
            advisor = {
                slot = political_advisor
                idea_token = ABC_variant
                traits = { silent_workhorse }
                cost = 100
            }
        }
    }
}
"""


def _write(path: Path, text: str = CHARACTERS) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_character_files_round_trip_byte_identically(tmp_path: Path) -> None:
    path = _write(tmp_path / "common/characters/ABC.txt")
    characters = load_characters_file(path)

    assert [character.id for character in characters] == ["ABC_leader", "ABC_variant"]
    assert len(characters[1].instances) == 2
    assert serialize_characters_file(characters, CHARACTERS) == CHARACTERS


def test_repeated_instance_edit_targets_only_selected_occurrence(tmp_path: Path) -> None:
    path = _write(tmp_path / "common/characters/ABC.txt")
    characters = load_characters_file(path)
    second = characters[1].instances[1]
    advisor = second.roles[0]
    assert isinstance(advisor, AdvisorRole)
    advisor.cost = 125
    advisor.touched_fields.add("cost")
    second.touched_fields.add("roles")
    characters[1].touched_fields.add("instances")

    rendered = serialize_characters_file(characters, CHARACTERS)

    assert rendered == CHARACTERS.replace("cost = 100", "cost = 125")
    assert 'NOT = { has_dlc = "Example DLC" }' in rendered


def test_new_character_supports_multiple_roles_and_dlc_instances() -> None:
    character = Character(
        id="ABC_new",
        country_tag="ABC",
        name="ABC_NEW",
        portraits=[
            CharacterPortrait(
                channel="civilian",
                large="GFX_portrait_ABC_new",
            )
        ],
        roles=[
            CountryLeaderRole(ideology="conservatism"),
            AdvisorRole(
                slot="political_advisor",
                idea_token="ABC_new",
                traits=["popular_figurehead"],
            ),
            ArmyCommanderRole(
                kind="field_marshal",
                skill=3,
                attack_skill=2,
                defense_skill=4,
                planning_skill=3,
                logistics_skill=2,
            ),
        ],
        instances=[
            CharacterInstance(
                allowed='has_dlc = "Example DLC"',
                name="ABC_NEW",
                roles=[ArmyCommanderRole(skill=4)],
            )
        ],
    )

    rendered = serialize_characters_file([character])

    assert "country_leader = {" in rendered
    assert "advisor = {" in rendered
    assert "field_marshal = {" in rendered
    assert "instance = {" in rendered
    assert 'has_dlc = "Example DLC"' in rendered
    assert "roles =" not in rendered


def test_character_scalar_edit_preserves_unknown_fields(tmp_path: Path) -> None:
    path = _write(tmp_path / "common/characters/ABC.txt")
    characters = load_characters_file(path)
    characters[0].name = "ABC_NEW_LEADER"
    characters[0].touched_fields.add("name")

    rendered = serialize_characters_file(characters, CHARACTERS)

    assert 'name = "ABC_NEW_LEADER"' in rendered
    assert "custom_character_key = keep" in rendered
    assert "GFX_portrait_ABC_leader" in rendered


@pytest.mark.parametrize("source", ["loaded", "loaded_edited", "created"])
@pytest.mark.parametrize("country_tag", ["ABC", "BBB"])
@pytest.mark.parametrize("mixed_fields", [False, True])
def test_country_tag_update_rejected_without_disturbing_pending_work(
    tmp_path: Path, source: str, country_tag: str, mixed_fields: bool,
) -> None:
    if source != "created":
        _write(tmp_path / "common/characters/ABC.txt")
    mod = Mod(tmp_path)
    if source == "created":
        mod.create_character(
            "ABC", Character(id="ABC_leader", name="Original Leader"), recruit=False,
        )
    elif source == "loaded_edited":
        assert mod.update_character("ABC_leader", name="Staged Leader")
    character = mod.get_character("ABC_leader")
    original_name = character.name
    mod.set_loc("PENDING_WORK", "Keep this staged localization")
    before = mod._snapshot()
    preview = mod.preview()
    # Insert supported fields first to catch partial mutation in mixed calls.
    fields = (
        {"name": "Rejected Leader", "portraits": [CharacterPortrait(large="GFX_rejected")]}
        if mixed_fields else {}
    )
    fields["country_tag"] = country_tag

    with pytest.raises(TypeError, match="Remove country_tag.*reassignment.*not supported"):
        mod.update_character("ABC_leader", **fields)

    assert mod._snapshot() == before
    assert mod.preview() == preview
    assert character.country_tag == "ABC"
    assert character.name == original_name
    result = mod.save(require_changes=True)
    assert result.written_files
    reloaded = Mod(tmp_path)
    assert reloaded.get_character("ABC_leader").country_tag == "ABC"
    assert reloaded.get_character("ABC_leader").name == original_name
    assert reloaded.get_loc("PENDING_WORK") == "Keep this staged localization"

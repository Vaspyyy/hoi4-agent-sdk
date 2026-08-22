from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest

from hoi4 import (
    AdvisorRole,
    AirWing,
    ArmyCommanderRole,
    Battalion,
    Character,
    CharacterPortrait,
    DivisionTemplate,
    DivisionUnit,
    Focus,
    Mod,
    import_flag_to_mod,
    import_portrait_to_mod,
    write_portrait_gfx,
)
from hoi4.characters import load_characters_file, serialize_characters_file
from hoi4.oob import validate_oob

Image = pytest.importorskip("PIL.Image")


GAME_ROOT = Path(
    "/home/ransom/.local/share/Steam/steamapps/common/Hearts of Iron IV"
)
EMPIRE_ROOT = Path(
    "/home/ransom/.local/share/hoi4-agent-sdk/audit-corpus/Empire"
)


def _write_state(
    root: Path,
    state_id: int,
    *,
    owner: str,
    cores: tuple[str, ...],
    provinces: tuple[int, ...],
) -> None:
    path = root / "history" / "states" / f"{state_id}-Test.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        (
            "state = {\n"
            f"    id = {state_id}\n"
            f'    name = "STATE_{state_id}"\n'
            "    manpower = 1000\n"
            "    state_category = rural\n"
            "    history = {\n"
            f"        owner = {owner}\n"
            + "".join(f"        add_core_of = {tag}\n" for tag in cores)
            + "    }\n"
            f"    provinces = {{ {' '.join(str(value) for value in provinces)} }}\n"
            "}\n"
        ),
        encoding="utf-8",
    )


def _source_image(path: Path, size: tuple[int, int] = (300, 300)) -> Path:
    Image.new("RGB", size, (90, 40, 120)).save(path)
    return path


def _add_portrait(root: Path, tag: str, slug: str, source: Path) -> str:
    texture = import_portrait_to_mod(
        root,
        tag,
        slug,
        source,
        output_format="tga",
    )
    sprite = f"GFX_portrait_{tag}_{slug}"
    write_portrait_gfx(
        root,
        tag,
        slug,
        portrait_path=texture,
        sprite_name=sprite,
    )
    return sprite


def _build_complete_country(root: Path) -> Mod:
    _write_state(root, 1, owner="OLD", cores=("OLD",), provinces=(1,))
    source = _source_image(root / "source.png")
    mod = Mod(root)
    mod.create_country("ABC", "Authorland", capital=1)
    mod.set_country_name_pool(
        "ABC",
        male_names=("Alex", "Boris"),
        female_names=("Anna",),
        surnames=("Example", "Writer"),
    )
    mod.set_state_owner(1, "ABC")
    import_flag_to_mod(root, "ABC", source)
    _add_portrait(root, "ABC", "leader_1", source)

    for index in (1, 2):
        advisor_id = f"ABC_advisor_{index}"
        advisor_sprite = _add_portrait(root, "ABC", f"advisor_{index}", source)
        mod.create_character(
            "ABC",
            Character(
                id=advisor_id,
                name=f"Advisor {index}",
                portraits=[
                    CharacterPortrait(
                        channel="civilian",
                        large=advisor_sprite,
                        small=advisor_sprite,
                    )
                ],
                roles=[
                    AdvisorRole(
                        slot="political_advisor",
                        traits=["silent_workhorse"],
                    )
                ],
            ),
        )
        commander_id = f"ABC_commander_{index}"
        commander_sprite = _add_portrait(
            root,
            "ABC",
            f"commander_{index}",
            source,
        )
        mod.create_character(
            "ABC",
            Character(
                id=commander_id,
                name=f"Commander {index}",
                portraits=[
                    CharacterPortrait(channel="army", large=commander_sprite)
                ],
                roles=[
                    ArmyCommanderRole(
                        skill=2,
                        attack_skill=2,
                        defense_skill=2,
                        planning_skill=2,
                        logistics_skill=2,
                    )
                ],
            ),
        )

    mod.create_oob(
        "ABC_1936",
        "ABC",
        templates=[
            DivisionTemplate(
                name="Infantry",
                battalions=[Battalion("infantry", 0, 0)],
            )
        ],
        divisions=[
            DivisionUnit(
                name="First Division",
                location=1,
                division_template="Infantry",
                start_experience_factor=0.1,
                start_equipment_factor=1.0,
            )
        ],
    )
    return mod


def test_complete_country_can_be_authored_through_public_apis(
    tmp_path: Path,
) -> None:
    mod = _build_complete_country(tmp_path)

    report = mod.validate_country_package("ABC", check_geography=False)

    assert report.complete
    assert report.structurally_complete
    assert report.analysis_scope == "structural"
    assert not report.proves_dynamic_achievability
    assert report.advisor_count == 2
    assert report.commander_count == 2
    assert report.character_count == 5
    assert report.owned_state_count == 1
    assert report.to_dict()["complete"] is True
    assert report.to_dict()["structurally_complete"] is True
    assert "common/names/00_generated_names.txt" in mod.preview()
    assert not [
        issue
        for issue in mod.validate()
        if issue.country_tag == "ABC" and issue.severity == "error"
    ]
    mod.save()
    names = (tmp_path / "common/names/00_generated_names.txt").read_text(
        encoding="utf-8"
    )
    assert 'names = { "Alex" "Boris" }' in names
    reloaded = Mod(tmp_path)
    reloaded_report = reloaded.validate_country_package(
        "ABC",
        check_geography=False,
    )
    assert reloaded_report.complete, reloaded_report.to_dict()


def test_incomplete_sdk_country_is_enforced_by_normal_validation(
    tmp_path: Path,
) -> None:
    _write_state(tmp_path, 1, owner="OLD", cores=("OLD",), provinces=(1,))
    mod = Mod(tmp_path)
    mod.create_country("ABC", "Incomplete", capital=1)

    codes = {
        issue.code
        for issue in mod.validate()
        if issue.country_tag == "ABC"
    }

    assert {
        "missing_country_flag",
        "missing_character_portrait_gfx",
        "insufficient_political_advisors",
        "insufficient_military_commanders",
        "missing_country_activation",
    } <= codes


@pytest.mark.parametrize("pool_root", ["mod", "install"])
def test_default_name_pool_satisfies_air_country_package(
    tmp_path: Path,
    pool_root: str,
) -> None:
    mod_root = tmp_path / "mod"
    install_root = tmp_path / "install"
    names_root = mod_root if pool_root == "mod" else install_root
    names = names_root / "common" / "names" / "00_names.txt"
    names.parent.mkdir(parents=True, exist_ok=True)
    names.write_text(
        'default = { male = { names = { "Alex" } } surnames = { "Smith" } }\n',
        encoding="utf-8",
    )
    mod = Mod(mod_root, hoi4_install=install_root)
    mod.create_country("ABC", "Authorland", capital=1)
    mod.create_oob(
        "ABC_1936_air",
        "ABC",
        kind="air",
        air_wings=[AirWing(1, "fighter_equipment_0", 12, owner="ABC")],
    )

    codes = {
        issue.code
        for issue in mod.validate_country_package(
            "ABC",
            check_geography=False,
        ).findings
    }

    assert "missing_country_name_pool" not in codes


def test_air_country_package_requires_tag_or_default_name_pool(
    tmp_path: Path,
) -> None:
    mod = Mod(tmp_path)
    mod.create_country("ABC", "Authorland", capital=1)
    mod.create_oob(
        "ABC_1936_air",
        "ABC",
        kind="air",
        air_wings=[AirWing(1, "fighter_equipment_0", 12, owner="ABC")],
    )

    codes = {
        issue.code
        for issue in mod.validate_country_package(
            "ABC",
            check_geography=False,
        ).findings
    }

    assert "missing_country_name_pool" in codes


def test_country_package_skips_name_pool_scan_without_air_wings(
    tmp_path: Path,
) -> None:
    mod = Mod(tmp_path)
    mod.create_country("ABC", "Authorland", capital=1)

    mod.validate_country_package("ABC", check_geography=False)

    assert "country_name_pools" not in mod._scan_cache


def test_country_package_requires_advisor_small_portrait(tmp_path: Path) -> None:
    mod = _build_complete_country(tmp_path)
    advisor = mod.get_character("ABC_advisor_1")
    advisor.portraits[0].small = ""

    report = mod.validate_country_package("ABC", check_geography=False)

    assert not report.complete
    assert "missing_character_small_portrait" in {
        finding.code for finding in report.findings
    }


def test_complete_country_report_names_each_remediation_failure(
    tmp_path: Path,
) -> None:
    mod = _build_complete_country(tmp_path)
    country = mod.get_country("ABC")
    leader = mod.get_character("ABC_leader_1")
    leader.portraits.clear()
    leader.touched_fields.add("portraits")
    mod.create_character(
        "ABC",
        Character(id="ABC_expected", name="Expected", roles=[AdvisorRole()]),
    )
    country.recruited_characters = [
        "ABC_missing",
    ]
    mod._loc_entries.pop("ABC_expected")
    mod.update_oob("ABC_1936", divisions=[])
    mod.get_state(1).cores.remove("ABC")
    (tmp_path / "gfx" / "flags" / "small" / "ABC.tga").unlink()
    (tmp_path / "gfx" / "leaders" / "ABC" / "advisor_1.tga").unlink()

    codes = {
        issue.code
        for issue in mod.validate_country_package(
            "ABC",
            check_geography=False,
            lifecycle="starting",
        ).findings
    }

    assert {
        "missing_country_flag",
        "missing_country_leader",
        "missing_character_portrait",
        "missing_character_portrait_texture",
        "insufficient_political_advisors",
        "insufficient_military_commanders",
        "undefined_recruited_character",
        "unrecruited_character",
        "missing_character_localization",
        "empty_land_oob",
        "capital_not_cored",
    } <= codes


def test_runtime_released_country_is_not_treated_as_starting_country(
    tmp_path: Path,
) -> None:
    mod = _build_complete_country(tmp_path)
    mod.set_state_owner(1, "OLD")
    assert mod.delete_oob("ABC_1936")
    tree = mod.create_focus_tree("old_focus", "OLD")
    mod.add_focus(
        tree.id,
        Focus(
            id="OLD_release_abc",
            completion_reward=(
                "ABC = { transfer_state = 1 }\n"
                "1 = { add_core_of = ABC }\n"
                "puppet = ABC"
            ),
        ),
    )

    report = mod.validate_country_package("ABC", check_geography=False)

    assert report.complete, report.to_dict()
    assert report.lifecycle == "runtime"
    assert report.runtime_state_ids == (1,)
    assert any(
        source.endswith("focus OLD_release_abc completion_reward")
        for source in report.activation_sources
    )
    assert not {
        "missing_land_oob",
        "no_owned_territory",
        "capital_not_owned",
    } & {finding.code for finding in report.findings}


def test_explicit_starting_lifecycle_retains_starting_package_requirements(
    tmp_path: Path,
) -> None:
    mod = _build_complete_country(tmp_path)
    mod.set_state_owner(1, "OLD")
    assert mod.delete_oob("ABC_1936")

    codes = {
        finding.code
        for finding in mod.validate_country_package(
            "ABC",
            check_geography=False,
            lifecycle="starting",
        ).findings
    }

    assert {
        "missing_land_oob",
        "no_owned_territory",
        "capital_not_owned",
    } <= codes


@pytest.mark.parametrize(
    ("completion_reward", "expected_code"),
    [
        (
            "ABC = { transfer_state = 2 }\n"
            "2 = { add_core_of = ABC }\n"
            "puppet = ABC",
            "runtime_capital_not_assigned",
        ),
        (
            "ABC = { transfer_state = 1 }\n"
            "puppet = ABC",
            "runtime_capital_not_cored",
        ),
    ],
)
def test_runtime_release_validates_declared_capital_setup(
    tmp_path: Path,
    completion_reward: str,
    expected_code: str,
) -> None:
    mod = _build_complete_country(tmp_path)
    mod.set_state_owner(1, "OLD")
    assert mod.delete_oob("ABC_1936")
    tree = mod.create_focus_tree("old_focus", "OLD")
    mod.add_focus(
        tree.id,
        Focus(
            id="OLD_release_abc",
            completion_reward=completion_reward,
        ),
    )

    report = mod.validate_country_package("ABC", check_geography=False)

    assert report.lifecycle == "runtime"
    assert expected_code in {finding.code for finding in report.errors}


def test_country_package_rejects_unknown_lifecycle(tmp_path: Path) -> None:
    mod = _build_complete_country(tmp_path)

    with pytest.raises(ValueError, match="lifecycle must be"):
        mod.validate_country_package("ABC", lifecycle="future")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("province_type", "owner", "expected_code"),
    [
        ("sea", "ABC", "oob_water_location"),
        ("land", "DEF", "oob_location_not_owned"),
    ],
)
def test_oob_rejects_water_and_non_owned_starting_locations(
    tmp_path: Path,
    province_type: str,
    owner: str,
    expected_code: str,
) -> None:
    _write_state(tmp_path, 1, owner=owner, cores=(owner,), provinces=(1,))
    map_dir = tmp_path / "map"
    map_dir.mkdir()
    (map_dir / "definition.csv").write_text(
        f"1;10;20;30;{province_type};false;plains;1\n",
        encoding="utf-8",
    )
    mod = Mod(tmp_path)
    mod.create_country("ABC", "Placement", capital=1)
    mod.create_oob(
        "ABC_1936",
        "ABC",
        templates=[
            DivisionTemplate(
                name="Infantry",
                battalions=[Battalion("infantry", 0, 0)],
            )
        ],
        divisions=[
            DivisionUnit(
                division_template="Infantry",
                location=1,
            )
        ],
    )

    assert expected_code in {
            issue.code
            for issue in mod.validate_country_package(
                "ABC",
                check_geography=False,
                lifecycle="starting",
            ).findings
        }


def test_character_role_mutation_requires_occurrence_when_ambiguous(
    tmp_path: Path,
) -> None:
    mod = Mod(tmp_path)
    character = mod.create_character(
        "ABC",
        Character(
            id="ABC_multi",
            roles=[
                AdvisorRole(cost=100),
                AdvisorRole(cost=150),
            ],
        ),
        recruit=False,
    )

    with pytest.raises(ValueError, match="pass occurrence"):
        mod.update_character_role("ABC_multi", "advisor", cost=75)

    assert mod.update_character_role(
        "ABC_multi",
        "advisor",
        occurrence=1,
        cost=75,
    )
    assert isinstance(character.roles[1], AdvisorRole)
    assert character.roles[1].cost == 75


def test_recruiting_character_requires_a_defined_country(tmp_path: Path) -> None:
    mod = Mod(tmp_path)

    with pytest.raises(KeyError, match="not defined"):
        mod.validate_country_package("ABC")
    with pytest.raises(KeyError, match="Create or load the country first"):
        mod.create_character("ABC", Character(id="ABC_orphan"))

    assert mod.create_character(
        "ABC",
        Character(id="ABC_event_character"),
        recruit=False,
    ).recruitment_expected is False


@pytest.mark.skipif(
    not (GAME_ROOT / "common/characters/AFG.txt").is_file(),
    reason="Local HOI4 install is not available",
)
@pytest.mark.parametrize("tag", ["AFG", "AUS"])
def test_real_vanilla_character_variants_round_trip_and_patch_one_occurrence(
    tmp_path: Path,
    tag: str,
) -> None:
    source = GAME_ROOT / "common" / "characters" / f"{tag}.txt"
    target = tmp_path / source.name
    shutil.copy2(source, target)
    original = target.read_text(encoding="utf-8", errors="ignore")
    characters = load_characters_file(target, country_tag=tag)

    assert serialize_characters_file(characters, original) == original
    repeated = next(
        character for character in characters if len(character.instances) >= 2
    )
    repeated.instances[0].name = f"{tag}_TEST_VARIANT"
    repeated.instances[0].touched_fields.add("name")
    repeated.touched_fields.add("instances")
    rendered = serialize_characters_file(characters, original)

    assert rendered.count(f"{tag}_TEST_VARIANT") == 1
    assert rendered != original


@pytest.mark.skipif(
    not EMPIRE_ROOT.is_dir(),
    reason="Private Empire audit corpus is not available",
)
def test_private_empire_roster_and_land_oob_are_read_only(tmp_path: Path) -> None:
    before = _tree_hash(EMPIRE_ROOT)
    copied = tmp_path / "Empire"
    shutil.copytree(EMPIRE_ROOT, copied)

    mod = Mod(copied, hoi4_install=GAME_ROOT)
    assert len(mod.list_characters(include_vanilla=False)) == 15
    for name in mod.list_oobs():
        oob = mod.get_oob(name)
        assert oob.name == name
        oob_findings = validate_oob(oob)
        context_findings = mod._validate_oob_context(oob, oob.country_tag)
        assert {finding.code for finding in oob_findings} == {
            "mixed_oob_kinds"
        }
        assert {finding.code for finding in context_findings} == {
            "legacy_naval_oob_without_dlc_fallback"
        }
    assert "missing_land_oob" not in {
        issue.code
        for issue in mod.validate_country_package(
            "AUH",
            check_geography=False,
        ).findings
    }
    illyria = mod.validate_country_package("ILL", check_geography=False)
    assert illyria.complete, illyria.to_dict()
    assert illyria.lifecycle == "runtime"
    assert illyria.runtime_state_ids == (102, 103, 104, 109, 163, 804, 852, 853)
    assert any(
        "AUH_crown_of_zvonimir" in source
        for source in illyria.activation_sources
    )
    danubian_soviet = mod.validate_country_package(
        "DSR",
        check_geography=False,
    )
    assert danubian_soviet.complete, danubian_soviet.to_dict()
    assert danubian_soviet.lifecycle == "runtime"
    assert danubian_soviet.runtime_state_ids == (43, 72, 75, 152, 976)
    assert any(
        "event empire.88 option 0" in source
        for source in danubian_soviet.activation_sources
    )

    assert _tree_hash(EMPIRE_ROOT) == before


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()

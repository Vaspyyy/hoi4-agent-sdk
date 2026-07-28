from __future__ import annotations

from pathlib import Path

import pytest

from hoi4 import Mod

Image = pytest.importorskip("PIL.Image")


COLORS = {
    1: (10, 10, 10),
    2: (20, 20, 20),
    3: (30, 30, 30),
    4: (40, 40, 40),
    5: (50, 50, 90),
    6: (60, 60, 60),
}


def _write_state(root: Path, state_id: int, provinces: tuple[int, ...]) -> None:
    path = root / "history" / "states" / f"{state_id}-Topology.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        (
            "state = {\n"
            f" id = {state_id}\n"
            f' name = "STATE_{state_id}"\n'
            " state_category = rural\n"
            " history = { owner = ABC add_core_of = ABC }\n"
            f" provinces = {{ {' '.join(str(value) for value in provinces)} }}\n"
            "}\n"
        ),
        encoding="utf-8",
    )


def _write_topology_fixture(root: Path) -> None:
    map_dir = root / "map"
    map_dir.mkdir(parents=True)
    rows = [
        f"{province};{red};{green};{blue};{'sea' if province == 5 else 'land'};false;plains;1"
        for province, (red, green, blue) in COLORS.items()
    ]
    (map_dir / "definition.csv").write_text(
        "\n".join(rows) + "\n",
        encoding="utf-8",
    )
    image = Image.new("RGB", (12, 5), COLORS[5])
    pixels = image.load()
    assert pixels is not None
    pixels[1, 1] = COLORS[1]
    pixels[1, 2] = COLORS[1]
    pixels[2, 1] = COLORS[2]
    pixels[2, 2] = COLORS[2]
    pixels[7, 1] = COLORS[3]
    pixels[7, 2] = COLORS[3]
    pixels[8, 1] = COLORS[4]
    pixels[8, 2] = COLORS[4]
    pixels[10, 3] = COLORS[6]
    image.save(map_dir / "provinces.bmp")

    _write_state(root, 1, (1, 2))
    _write_state(root, 2, (3, 4))
    _write_state(root, 3, (6,))


def test_find_disconnected_states_reports_significant_enclave_only(
    tmp_path: Path,
) -> None:
    _write_topology_fixture(tmp_path)
    mod = Mod(tmp_path)
    mod.create_country("ABC", "Topologia", capital=1)

    components = mod.find_disconnected_states("ABC")

    assert len(components) == 1
    assert components[0].state_ids == (2,)
    assert components[0].province_ids == (3, 4)
    assert components[0].land_province_count == 2


def test_find_disconnected_states_honors_threshold_and_allowlist(
    tmp_path: Path,
) -> None:
    _write_topology_fixture(tmp_path)
    mod = Mod(tmp_path)
    mod.create_country("ABC", "Topologia", capital=1)

    with_tiny_island = mod.find_disconnected_states(
        "ABC",
        minimum_land_provinces=1,
    )
    allowlisted = mod.find_disconnected_states(
        "ABC",
        minimum_land_provinces=1,
        allowed_state_ids=(3,),
    )

    assert [component.state_ids for component in with_tiny_island] == [(2,), (3,)]
    assert [component.state_ids for component in allowlisted] == [(2,)]


def test_explicit_adjacency_connects_an_otherwise_disconnected_state(
    tmp_path: Path,
) -> None:
    _write_topology_fixture(tmp_path)
    (tmp_path / "map" / "adjacencies.csv").write_text(
        "From;To;Type;Through;start_x;start_y;stop_x;stop_y;Comment\n"
        "2;3;sea;5;-1;-1;-1;-1;strait\n"
        "-1;-1;;-1;-1;-1;-1;-1;\n",
        encoding="utf-8",
    )
    mod = Mod(tmp_path)
    mod.create_country("ABC", "Topologia", capital=1)

    assert mod.find_disconnected_states("ABC") == ()


def test_normal_validation_reports_disconnected_generated_country_territory(
    tmp_path: Path,
) -> None:
    _write_topology_fixture(tmp_path)
    mod = Mod(tmp_path)
    mod.create_country("ABC", "Topologia", capital=1)

    findings = [
        issue
        for issue in mod.validate()
        if issue.code == "disconnected_country_territory"
    ]

    assert len(findings) == 1
    assert findings[0].severity == "warning"
    assert findings[0].state_id == 2

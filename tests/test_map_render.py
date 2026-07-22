from __future__ import annotations

from pathlib import Path

import pytest

from hoi4 import MapRenderCancelled
from hoi4.map_render import load_country_colors, load_map_state_data, parse_definition_csv
from hoi4.map_render import render_political_map, resolve_country_colors


def test_definition_csv_ignores_bad_rows_and_channels(tmp_path: Path) -> None:
    path = tmp_path / "definition.csv"
    path.write_text(
        "0;0;0;0;sea;false\n1;10;20;30;land;false\nbad;row\n2;999;0;0;land;false\n",
        encoding="utf-8",
    )

    assert parse_definition_csv(path) == {(0, 0, 0): 0, (10, 20, 30): 1}


def test_country_colors_use_colors_file_and_mod_precedence(tmp_path: Path) -> None:
    vanilla = tmp_path / "game"
    mod = tmp_path / "mod"
    for root in (vanilla, mod):
        (root / "common/country_tags").mkdir(parents=True)
        (root / "common/countries").mkdir(parents=True)
    (vanilla / "common/country_tags/00_tags.txt").write_text(
        'ABC = "countries/ABC.txt"\n', encoding="utf-8"
    )
    (vanilla / "common/countries/ABC.txt").write_text(
        "color = { 1 2 3 }\n", encoding="utf-8"
    )
    (vanilla / "common/countries/colors.txt").write_text(
        "ABC = { color = rgb { 4 5 6 } }\n", encoding="utf-8"
    )
    (mod / "common/countries/colors.txt").write_text(
        "ABC = { color = rgb { 7 8 9 } }\n", encoding="utf-8"
    )

    assert load_country_colors(mod, vanilla)["ABC"] == (7, 8, 9)


def test_fallback_country_colors_are_deterministic_and_distinct() -> None:
    first = resolve_country_colors({"ABC": (1, 2, 3), "DEF": (1, 2, 3)}, {"ABC", "DEF", "XYZ"})
    second = resolve_country_colors(
        {"ABC": (1, 2, 3), "DEF": (1, 2, 3)}, {"ABC", "DEF", "XYZ"}
    )

    assert first == second
    assert first["ABC"] == (1, 2, 3)
    assert len(set(first.values())) == 3


def test_mod_state_overrides_vanilla_state_geography(tmp_path: Path) -> None:
    vanilla = tmp_path / "game"
    mod = tmp_path / "mod"
    (vanilla / "history/states").mkdir(parents=True)
    (mod / "history/states").mkdir(parents=True)
    (vanilla / "history/states/1.txt").write_text(
        'state = { id = 1 name = "OLD" provinces = { 10 } history = { owner = AAA } }',
        encoding="utf-8",
    )
    (mod / "history/states/1.txt").write_text(
        'state = { id = 1 name = "NEW" provinces = { 20 } history = { owner = BBB } }',
        encoding="utf-8",
    )

    data = load_map_state_data(mod, vanilla)

    assert data.owners == {1: "BBB"}
    assert data.names == {1: "NEW"}
    assert data.province_to_state == {20: 1}


def test_render_small_political_map(tmp_path: Path) -> None:
    np = pytest.importorskip("numpy")
    image_module = pytest.importorskip("PIL.Image")
    mod = tmp_path / "mod"
    (mod / "history/states").mkdir(parents=True)
    (mod / "common/countries").mkdir(parents=True)
    (mod / "history/states/1.txt").write_text(
        'state = { id = 1 name = "ONE" provinces = { 1 } history = { owner = ABC } }',
        encoding="utf-8",
    )
    (mod / "common/countries/colors.txt").write_text(
        "ABC = { color = rgb { 12 34 56 } }\n", encoding="utf-8"
    )
    definition = mod / "map/definition.csv"
    definition.parent.mkdir(parents=True)
    definition.write_text("0;0;0;0;sea;false\n1;10;20;30;land;false\n", encoding="utf-8")
    provinces = mod / "map/provinces.bmp"
    source = np.array([[[10, 20, 30], [0, 0, 0]]], dtype=np.uint8)
    image_module.fromarray(source, mode="RGB").save(provinces)

    progress_events = []
    result = render_political_map(
        provinces, definition, mod, event_progress=progress_events.append
    )

    assert result.pixels[0, 0].tolist() == [12, 34, 56]
    assert result.pixels[0, 1].tolist() == [30, 80, 160]
    assert result.state_count == 1
    assert result.country_count == 1
    assert progress_events[0].operation == "map_render"
    assert progress_events[-1].phase == "done"


def test_render_cancellation_raises_public_exception(tmp_path: Path) -> None:
    pytest.importorskip("numpy")
    pytest.importorskip("PIL.Image")

    with pytest.raises(MapRenderCancelled):
        render_political_map(
            tmp_path / "provinces.bmp",
            tmp_path / "definition.csv",
            tmp_path / "mod",
            cancelled=lambda: True,
        )

from __future__ import annotations

# Optional map dependencies must be gated before importing hoi4.mapgen.
# ruff: noqa: E402

import csv
import json
import os
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
Image = pytest.importorskip("PIL.Image")
pytest.importorskip("scipy")

from hoi4.mapgen import (
    GenerationCancelled,
    MapGenerationConfig,
    compute_adjacencies,
    create_equator_density,
    create_uniform_density,
    export_all_map_files,
    export_buildings_txt,
    export_definition_csv,
    export_province_definitions,
    export_railways_txt,
    export_states,
    export_supply_nodes,
    export_territory_definitions,
    export_territory_history,
    generate_and_export_map,
    generate_provinces,
    generate_territories,
)
from hoi4.mapgen.config import DEFAULT_DENSITY_GREY, LAKE_COLOR, OCEAN_COLOR
from hoi4.mapgen.numb_gen import NumberSeries
from hoi4.mapgen import hoi4_export as export_module
from hoi4.mapgen.utils import clear_used_colors, color_from_id, extract_masks, random_seeds


def _land_image(width: int = 64, height: int = 32, *, lake: bool = False) -> Image.Image:
    array = np.full((height, width, 3), 200, dtype=np.uint8)
    array[: height // 2] = OCEAN_COLOR
    if lake:
        array[height * 3 // 4 : height * 3 // 4 + 2, width // 2 : width // 2 + 3] = LAKE_COLOR
    return Image.fromarray(array, mode="RGB")


class TestNumberSeries:
    def test_sequential_and_exhausted(self) -> None:
        series = NumberSeries("PRV", 1, 3)
        assert [series.get_id() for _ in range(4)] == [
            "PRV1",
            "PRV2",
            "PRV3",
            None,
        ]

    def test_rejects_backwards_range(self) -> None:
        with pytest.raises(ValueError):
            NumberSeries("X", 5, 4)


class TestInputUtilities:
    def test_masks_split_land_ocean_and_lake(self) -> None:
        masks = extract_masks(None, _land_image(lake=True))
        assert masks["map_w"] == 64
        assert masks["map_h"] == 32
        assert masks["sea_mask"].sum() == 64 * 16
        assert masks["lake_mask"].sum() == 6

    def test_no_images_is_invalid(self) -> None:
        with pytest.raises(ValueError):
            extract_masks(None, None)

    def test_seed_sampling_is_deterministic(self) -> None:
        mask = np.ones((10, 10), dtype=bool)
        assert random_seeds(mask, 8, rng_seed=9) == random_seeds(mask, 8, rng_seed=9)

    def test_color_generation_is_unique(self) -> None:
        clear_used_colors()
        colors = {color_from_id(index, "land") for index in range(500)}
        assert len(colors) == 500
        assert (0, 0, 0) not in colors

    def test_density_images(self) -> None:
        uniform = create_uniform_density(40, 20)
        equator = np.asarray(create_equator_density(40, 20))
        assert uniform.size == (40, 20)
        assert np.asarray(uniform).mean() == DEFAULT_DENSITY_GREY
        assert equator[10, 0] < equator[0, 0]


class TestGeneration:
    def test_territory_generation_is_seeded_and_reports_progress(self) -> None:
        progress: list[int] = []
        first = generate_territories(
            _land_image(), land_count=4, ocean_count=2, seed=17, progress_fn=progress.append
        )
        second = generate_territories(_land_image(), land_count=4, ocean_count=2, seed=17)
        assert first.metadata == second.metadata
        assert np.array_equal(first.pmap, second.pmap)
        assert len(first.metadata) == 6
        assert progress[0] == 0
        assert progress[-1] == 100
        assert progress == sorted(set(progress))

    def test_provinces_cover_map_and_enrich_territories(self) -> None:
        territories = generate_territories(
            _land_image(lake=True), land_count=3, ocean_count=2, seed=1
        )
        provinces = generate_provinces(
            territories.pmap,
            territories.metadata,
            territories.masks,
            land_count=8,
            ocean_count=4,
            seed=2,
        )
        assert (provinces.pmap >= 0).all()
        assert any(item["province_type"] == "lake" for item in provinces.metadata)
        assert all(item.get("province_ids") for item in territories.metadata)
        assert {item["province_terrain"] for item in provinces.metadata} <= {
            "plains",
            "deep_ocean",
            "lakes",
        }

    def test_generation_can_be_cancelled(self) -> None:
        calls = 0

        def cancelled() -> bool:
            nonlocal calls
            calls += 1
            return calls >= 2

        with pytest.raises(GenerationCancelled):
            generate_territories(_land_image(), land_count=10, ocean_count=5, cancel_fn=cancelled)

    def test_mismatched_density_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="density image"):
            generate_territories(
                _land_image(), Image.new("RGB", (64, 32), "white"), Image.new("L", (2, 2))
            )

    def test_nonempty_geography_rejects_zero_region_counts(self) -> None:
        with pytest.raises(ValueError, match="land_count"):
            generate_territories(_land_image(), land_count=0, ocean_count=2)
        with pytest.raises(ValueError, match="ocean_count"):
            generate_territories(_land_image(), land_count=2, ocean_count=0)


class TestExportHelpers:
    def test_staged_commit_rolls_back_if_replacement_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        destination = tmp_path / "mod"
        staged = tmp_path / "staged"
        destination.mkdir()
        staged.mkdir()
        (destination / "a.txt").write_text("old a", encoding="utf-8")
        (destination / "b.txt").write_text("old b", encoding="utf-8")
        (staged / "a.txt").write_text("new a", encoding="utf-8")
        (staged / "b.txt").write_text("new b", encoding="utf-8")
        real_replace = os.replace
        failed = False

        def fail_second_commit(source, target) -> None:
            nonlocal failed
            if Path(source) == staged / "b.txt" and not failed:
                failed = True
                raise OSError("simulated commit failure")
            real_replace(source, target)

        monkeypatch.setattr(export_module.os, "replace", fail_second_commit)

        with pytest.raises(OSError, match="simulated commit failure"):
            export_module._commit_staged_tree(staged, destination)

        assert (destination / "a.txt").read_text(encoding="utf-8") == "old a"
        assert (destination / "b.txt").read_text(encoding="utf-8") == "old b"

    def test_definition_csv_has_null_row_and_hoi4_types(self, tmp_path: Path) -> None:
        data = [
            {
                "province_id": 1,
                "R": 100,
                "G": 200,
                "B": 50,
                "province_type": "land",
                "province_terrain": "forest",
            },
            {
                "province_id": 2,
                "R": 1,
                "G": 2,
                "B": 3,
                "province_type": "ocean",
            },
        ]
        path = tmp_path / "definition.csv"
        export_definition_csv(data, path, coastal={1})
        rows = list(csv.reader(path.read_text().splitlines(), delimiter=";"))
        assert rows[0] == ["0", "0", "0", "0", "land", "false", "unknown", "0"]
        assert rows[1][4:8] == ["land", "true", "forest", "1"]
        assert rows[2][4:8] == ["sea", "false", "ocean", "0"]

    def test_legacy_definition_exports(self, tmp_path: Path) -> None:
        territories = [
            {
                "territory_id": "TRT000001",
                "territory_type": "land",
                "R": 1,
                "G": 2,
                "B": 3,
                "x": 4.0,
                "y": 5.0,
                "province_ids": ["PRV000001"],
            }
        ]
        provinces = [
            {
                "province_id": "PRV000001",
                "province_type": "land",
                "R": 6,
                "G": 7,
                "B": 8,
                "x": 9.0,
                "y": 10.0,
                "province_terrain": "plains",
            }
        ]
        export_territory_definitions(territories, tmp_path / "territories.json")
        export_province_definitions(provinces, tmp_path / "provinces.json")
        export_territory_history(territories, tmp_path / "history.csv", fmt="csv")
        assert (
            json.loads((tmp_path / "territories.json").read_text())["TRT000001"]["territory_type"]
            == "land"
        )
        assert (
            json.loads((tmp_path / "provinces.json").read_text())["PRV000001"]["province_terrain"]
            == "plains"
        )
        assert "PRV000001" in (tmp_path / "history.csv").read_text()

    def test_state_supply_and_buildings_exports(self, tmp_path: Path) -> None:
        territories = [
            {
                "territory_id": 5,
                "territory_type": "land",
                "province_ids": [1, 2],
            }
        ]
        provinces = [
            {"province_id": 1, "province_type": "land", "x": 10.5, "y": 20.3},
            {"province_id": 2, "province_type": "land", "x": 20.0, "y": 20.0},
        ]
        paths = export_states(territories, tmp_path, coastal={1})
        export_supply_nodes(provinces, territories, tmp_path / "map/supply_nodes.txt")
        export_buildings_txt(provinces, territories, tmp_path / "map/buildings.txt", coastal={1})
        state = paths[0].read_text()
        buildings = (tmp_path / "map/buildings.txt").read_text()
        assert "victory_points = { 1 1 }" in state
        assert "1 = {" in state and "naval_base = 1" in state
        assert (tmp_path / "map/supply_nodes.txt").read_text() == "1 1\n"
        assert "5;arms_factory;10.50;10.00;20.30;0.00;1" in buildings
        assert "infrastructure" not in buildings

    def test_railways_never_cross_water(self, tmp_path: Path) -> None:
        provinces = [
            {"province_id": 1, "province_type": "land"},
            {"province_id": 2, "province_type": "ocean"},
            {"province_id": 3, "province_type": "land"},
        ]
        territories = [
            {"territory_id": 1, "territory_type": "land", "province_ids": [1]},
            {"territory_id": 2, "territory_type": "land", "province_ids": [3]},
        ]
        path = tmp_path / "railways.txt"
        export_railways_txt(path, provinces, territories, {(1, 2), (2, 3)})
        assert path.read_text() == ""

    def test_adjacency_scan(self) -> None:
        image = Image.new("RGB", (4, 2), (10, 20, 30))
        pixels = np.asarray(image).copy()
        pixels[:, 2:] = (40, 50, 60)
        data = [
            {"province_id": 1, "R": 10, "G": 20, "B": 30, "province_type": "land"},
            {"province_id": 2, "R": 40, "G": 50, "B": 60, "province_type": "land"},
        ]
        assert compute_adjacencies(data, Image.fromarray(pixels)) == {(1, 2)}

    @pytest.mark.parametrize(
        ("rows", "water_type", "edges", "coastal"),
        [
            ([[1, 2, 3]] * 2, "ocean", {(1, 2), (2, 3), (1, 3)}, {1, 2}),
            ([[3, 2, 1]] * 2, "sea", {(1, 2), (2, 3), (1, 3)}, {1, 2}),
            ([[1, 3, 2]], "ocean", {(1, 2), (2, 3), (1, 3)}, {1, 2}),
            ([[1, 2, 3]], "lake", {(1, 2), (2, 3), (1, 3)}, set()),
            # Unknown pixels separate the poles and cannot become graph nodes.
            ([[1, 1, 1], [0, 0, 0], [3, 3, 3]], "ocean", set(), set()),
            # Even diagonal contact across the seam must not count.
            ([[1, 0, 0], [0, 0, 3]], "ocean", set(), set()),
            ([[1, 0, 3]], "ocean", {(1, 3)}, {1}),
            ([[0, 1, 3]], "ocean", {(1, 3)}, {1}),
            ([[1, 2, 1]], "ocean", {(1, 2)}, set()),
            ([[1]], "ocean", set(), set()),
            ([[3]], "ocean", set(), set()),
            ([[1], [3]], "ocean", {(1, 3)}, {1}),
            ([[1, 3]], "ocean", {(1, 3)}, {1}),
            ([[1, 2]], "ocean", {(1, 2)}, set()),
        ],
    )
    def test_wrapped_export_topology(self, rows, water_type, edges, coastal) -> None:
        provinces = [
            {"province_id": i, "R": i, "G": 0, "B": 0, "province_type": kind}
            for i, kind in [(1, "land"), (2, "land"), (3, water_type)]
        ]
        pixels = np.zeros((len(rows), len(rows[0]), 3), dtype=np.uint8)
        pixels[:, :, 0] = rows
        image = Image.fromarray(pixels)
        assert compute_adjacencies(provinces, image) == edges
        assert export_module.compute_coastal_provinces(provinces, image) == coastal

    @pytest.mark.parametrize("override", [False, True])
    @pytest.mark.parametrize("rows", [[1, 2, 3], [1, 3, 2]])
    def test_master_export_seam_contacts(self, tmp_path: Path, rows, override: bool) -> None:
        provinces = [
            {
                "province_id": i, "R": i, "G": 0, "B": 0,
                "province_type": kind, "territory_id": i, "x": float(rows.index(i)),
                "y": 0.0,
            }
            for i, kind in [(1, "land"), (2, "land"), (3, "ocean")]
        ]
        territories = [
            {"territory_id": i, "territory_type": kind, "province_ids": [i]}
            for i, kind in [(1, "land"), (2, "land"), (3, "ocean")]
        ]
        pixels = np.zeros((2, 3, 3), dtype=np.uint8)
        pixels[:, :, 0] = rows
        export_all_map_files(
            provinces, Image.fromarray(pixels), territories, tmp_path,
            coastal=set() if override else None,
            adjacencies=set() if override else None,
        )
        with (tmp_path / "map/definition.csv").open() as stream:
            definitions = {int(row[0]): row for row in csv.reader(stream, delimiter=";")}
        for province_id in (1, 2):
            assert definitions[province_id][5] == ("false" if override else "true")
            state = tmp_path / f"history/states/{province_id}.txt"
            content = state.read_text()
            assert ("naval_base = 1" in content) is not override
            if not override:
                assert f"{province_id} = {{\n\t\t\t\tnaval_base = 1" in content
        assert definitions[3][5] == "false"
        assert (tmp_path / "map/railways.txt").read_text() == (
            "" if override else "1 2 1 2\n"
        )

    def test_cancelled_master_export_does_not_touch_destination(self, tmp_path: Path) -> None:
        map_dir = tmp_path / "map"
        map_dir.mkdir()
        definition = map_dir / "definition.csv"
        definition.write_text("existing\n")
        progress = 0

        def report(value: int) -> None:
            nonlocal progress
            progress = value

        provinces = [
            {
                "province_id": 1,
                "province_type": "land",
                "R": 10,
                "G": 20,
                "B": 30,
                "x": 1.0,
                "y": 1.0,
                "territory_id": 1,
            }
        ]
        territories = [{"territory_id": 1, "territory_type": "land", "province_ids": [1]}]
        with pytest.raises(GenerationCancelled):
            export_all_map_files(
                provinces,
                Image.new("RGB", (4, 4), (10, 20, 30)),
                territories,
                tmp_path,
                progress_fn=report,
                cancel_fn=lambda: progress >= 20,
            )
        assert definition.read_text() == "existing\n"
        assert not (map_dir / "provinces.bmp").exists()

    def test_reexport_removes_files_from_previous_generated_manifest(
        self, tmp_path: Path
    ) -> None:
        pixels = np.zeros((4, 4, 3), dtype=np.uint8)
        pixels[:, :2] = (10, 20, 30)
        pixels[:, 2:] = (40, 50, 60)
        provinces = [
            {
                "province_id": 1,
                "province_type": "land",
                "R": 10,
                "G": 20,
                "B": 30,
                "x": 1.0,
                "y": 1.0,
                "territory_id": 1,
            },
            {
                "province_id": 2,
                "province_type": "land",
                "R": 40,
                "G": 50,
                "B": 60,
                "x": 3.0,
                "y": 1.0,
                "territory_id": 2,
            },
        ]
        territories = [
            {"territory_id": 1, "territory_type": "land", "province_ids": [1]},
            {"territory_id": 2, "territory_type": "land", "province_ids": [2]},
        ]
        export_all_map_files(provinces, Image.fromarray(pixels), territories, tmp_path)
        assert (tmp_path / "history/states/2.txt").is_file()
        assert (tmp_path / "map/strategicregions/2-Territory_2.txt").is_file()

        export_all_map_files(
            provinces[:1],
            Image.new("RGB", (4, 4), (10, 20, 30)),
            territories[:1],
            tmp_path,
        )

        assert not (tmp_path / "history/states/2.txt").exists()
        assert not (tmp_path / "map/strategicregions/2-Territory_2.txt").exists()

    def test_total_conversion_profile_stages_scaffold_and_tag_overrides(
        self, tmp_path: Path
    ) -> None:
        scaffold = tmp_path / "scaffold"
        game = tmp_path / "game"
        mod = tmp_path / "mod"
        (scaffold / "events").mkdir(parents=True)
        (scaffold / "events/base.txt").write_text("# compatible base\n", encoding="utf-8")
        (scaffold / "descriptor.mod").write_text("skip me", encoding="utf-8")
        (game / "common/country_tags").mkdir(parents=True)
        (game / "common/country_tags/00_countries.txt").write_text(
            'GER = "countries/Germany.txt"\n', encoding="utf-8"
        )
        provinces = [
            {
                "province_id": 1,
                "province_type": "land",
                "R": 10,
                "G": 20,
                "B": 30,
                "x": 1.0,
                "y": 1.0,
                "territory_id": 1,
            }
        ]
        territories = [
            {"territory_id": 1, "territory_type": "land", "province_ids": [1]}
        ]

        report = export_all_map_files(
            provinces,
            Image.new("RGB", (4, 4), (10, 20, 30)),
            territories,
            mod,
            hoi4_install=game,
            total_conversion=export_module.TotalConversionProfile(scaffold_root=scaffold),
        )

        assert (mod / "events/base.txt").read_text(encoding="utf-8") == "# compatible base\n"
        assert not (mod / "descriptor.mod").exists()
        assert "total-conversion override" in (
            mod / "common/country_tags/00_countries.txt"
        ).read_text(encoding="utf-8")
        assert report["total_conversion"] == "enabled (1 scaffold files)"


def test_small_map_end_to_end_export(tmp_path: Path) -> None:
    progress: list[int] = []
    progress_events = []
    result = generate_and_export_map(
        _land_image(64, 32),
        tmp_path,
        config=MapGenerationConfig(
            land_territories=3,
            ocean_territories=2,
            land_provinces=8,
            ocean_provinces=4,
            seed=44,
        ),
        progress_fn=progress.append,
        event_progress=progress_events.append,
    )

    expected = {
        "map/definition.csv",
        "map/provinces.bmp",
        "map/terrain.bmp",
        "map/rivers.bmp",
        "map/heightmap.bmp",
        "map/trees.bmp",
        "map/world_normal.bmp",
        "map/default.map",
        "map/adjacencies.csv",
        "map/buildings.txt",
        "map/supply_nodes.txt",
        "tutorial/tutorial.txt",
    }
    assert expected <= {
        str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*") if path.is_file()
    }
    definition_rows = list(
        csv.reader((tmp_path / "map/definition.csv").read_text().splitlines(), delimiter=";")
    )
    assert len(definition_rows) == len(result.provinces.metadata) + 1
    assert len(list((tmp_path / "history/states").glob("*.txt"))) == 3
    assert progress[0] == 0
    assert progress[-1] == 100
    assert progress == sorted(progress)
    assert result.export_report["provinces.bmp"] == "ok"
    assert progress_events[0].operation == "map_generation"
    assert progress_events[-1].phase == "done"

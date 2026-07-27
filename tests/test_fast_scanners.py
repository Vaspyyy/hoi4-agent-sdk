from __future__ import annotations

from pathlib import Path

from hoi4.events import scan_event_ids_file
from hoi4.ideas import scan_idea_ids_file


def test_event_id_scanner_handles_multiple_types_comments_and_nested_ids(tmp_path: Path) -> None:
    path = tmp_path / "events.txt"
    path.write_text(
        """add_namespace = test
# country_event = { id = fake.1 }
country_event = { id = test.1 option = { id = nested_wrong } }
character_event = {
    id = "test.2"
}
""",
        encoding="utf-8",
    )

    assert scan_event_ids_file(path) == {"test.1", "test.2"}


def test_idea_id_scanner_handles_categories_and_direct_ideas(tmp_path: Path) -> None:
    path = tmp_path / "ideas.txt"
    path.write_text(
        """ideas = {
    country = {
        nested_idea = { modifier = { stability_factor = 0.1 } }
    }
    direct_idea = { icon = GFX_idea_generic }
}
dynamic_country_ideas = {
    name = dynamic_group
    dynamic_idea = { available = { always = yes } }
}
""",
        encoding="utf-8",
    )

    assert scan_idea_ids_file(path) == {"nested_idea", "direct_idea"}

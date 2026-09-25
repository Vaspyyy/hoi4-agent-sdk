import pytest

from hoi4 import Mod
import hoi4.mod as mod_module
import hoi4.parser as parser_module


def test_repeated_noop_overwrites_parse_existing_file_once_and_keep_comments(tmp_path, monkeypatch):
    path = tmp_path / "common/scripted_triggers/triggers.txt"
    path.parent.mkdir(parents=True)
    text = "# source comment\n" + "".join(
        f"trigger_{i} = {{ # keep inner comment\n always = yes\n}}\n" for i in range(100)
    )
    path.write_text(text)
    mod = Mod(tmp_path)
    actual = parser_module.parse_pdx
    parsed = []

    def counting_parse(body):
        parsed.append(body)
        return actual(body)

    monkeypatch.setattr(parser_module, "parse_pdx", counting_parse)
    for i in range(100):
        mod.create_scripted_trigger(
            f"trigger_{i}",
            "always = yes",
            path="common/scripted_triggers/triggers.txt",
            overwrite=True,
        )
    assert parsed.count(text) == 1
    assert mod.preview() == ""
    assert path.read_text() == text
    with pytest.warns(RuntimeWarning, match="No changes written"):
        assert mod.save().no_changes


def test_append_does_not_parse_or_patch_growing_text_and_rolls_back(tmp_path, monkeypatch):
    mod = Mod(tmp_path)
    actual_parse = parser_module.parse_pdx
    actual_set = mod_module.set_block
    parsed = []
    patched = []

    def counting_parse(body):
        parsed.append(body)
        return actual_parse(body)

    def counting_set(text, key, body):
        patched.append(text)
        return actual_set(text, key, body)

    monkeypatch.setattr(parser_module, "parse_pdx", counting_parse)
    monkeypatch.setattr(mod_module, "set_block", counting_set)
    with mod.transaction():
        for i in range(50):
            mod.create_scripted_trigger(f"trigger_{i}", "always = yes")
        assert "trigger_49" in mod.preview()
        # Every parse is either the initial empty file or one requested block.
        assert len(parsed) == 51
        assert all(text == "" for text in patched)
    assert not mod.preview()
    mod.create_scripted_trigger("trigger_0", "always = no")
    mod.save(require_changes=True)
    output = (tmp_path / "common/scripted_triggers/00_generated_triggers.txt").read_text()
    assert "always = no" in output and "trigger_49" not in output


def test_equivalent_overwrite_still_checks_other_file_collisions(tmp_path):
    folder = tmp_path / "common/scripted_triggers"
    folder.mkdir(parents=True)
    for name in ("first.txt", "second.txt"):
        (folder / name).write_text("same = { always = yes }")
    mod = Mod(tmp_path)
    with pytest.raises(ValueError, match="already exists"):
        mod.create_scripted_trigger(
            "same", "always = yes", path="common/scripted_triggers/first.txt", overwrite=True
        )


def test_append_separates_unterminated_comment_and_changed_overwrite_is_saved(tmp_path):
    folder = tmp_path / "common/scripted_triggers"
    folder.mkdir(parents=True)
    path = folder / "source.txt"
    path.write_bytes(b"existing = { always = yes }\r\n# last comment")
    mod = Mod(tmp_path)
    mod.create_scripted_trigger("new", "always = yes", path="common/scripted_triggers/source.txt")
    mod.create_scripted_trigger(
        "existing", "always = no", path="common/scripted_triggers/source.txt", overwrite=True
    )
    mod.save(require_changes=True)
    data = path.read_bytes()
    # read_text normalizes preexisting line endings, but comment boundaries and
    # all three semantic items survive the append/update sequence.
    parsed = parser_module.parse_pdx(data.decode())
    assert parsed.find("new") is not None
    assert parsed.find("existing").get_value("always") == "no"
    assert b"# last comment" in data

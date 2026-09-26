import pytest

from hoi4.diff import unified_diff
from hoi4.release_gate import measure_unified_diff


MARKER = "\\ No newline at end of file\n"


@pytest.mark.parametrize("old_ending", ["", "\n", "\r\n"])
@pytest.mark.parametrize("new_ending", ["", "\n", "\r\n"])
def test_replacement_preserves_line_endings_and_marks_unterminated_records(
    old_ending, new_ending
):
    diff = unified_diff("old" + old_ending, "new" + new_ending, "test.txt")

    assert diff == (
        "--- a/test.txt\n+++ b/test.txt\n@@ -1 +1 @@\n"
        + "-old" + (old_ending or "\n" + MARKER)
        + "+new" + (new_ending or "\n" + MARKER)
    )
    stats = measure_unified_diff(diff)
    assert (stats.additions, stats.deletions, stats.changed_lines) == (1, 1, 2)
    assert stats.files == ("test.txt",)
    assert stats.hunks == 1


@pytest.mark.parametrize("ending", ["\n", "\r\n"])
@pytest.mark.parametrize("remove_newline", [False, True])
def test_newline_only_changes(ending, remove_newline):
    original, modified = "same", "same" + ending
    removed = "-same\n" + MARKER
    added = "+same" + ending
    if remove_newline:
        original, modified = modified, original
        removed, added = "-same" + ending, "+same\n" + MARKER

    diff = unified_diff(original, modified)

    assert diff == "--- original\n+++ modified\n@@ -1 +1 @@\n" + removed + added
    stats = measure_unified_diff(diff)
    assert (stats.additions, stats.deletions) == (1, 1)


@pytest.mark.parametrize("text", ["", "same", "first\nlast", "first\r\nlast"])
def test_unchanged_unterminated_input_has_no_diff(text):
    assert unified_diff(text, text) == ""


@pytest.mark.parametrize("ending", ["\n", "\r\n"])
def test_unterminated_context_line(ending):
    diff = unified_diff("old" + ending + "tail", "new" + ending + "tail")

    assert diff == (
        "--- original\n+++ modified\n@@ -1,2 +1,2 @@\n"
        + "-old" + ending + "+new" + ending + " tail\n" + MARKER
    )
    stats = measure_unified_diff(diff)
    assert (stats.additions, stats.deletions, stats.changed_lines) == (1, 1, 2)


@pytest.mark.parametrize("delete", [False, True])
def test_unterminated_file_creation_and_deletion(delete):
    original, modified = ("content", "") if delete else ("", "content")
    hunk = "@@ -1 +0,0 @@\n-content\n" if delete else "@@ -0,0 +1 @@\n+content\n"

    diff = unified_diff(original, modified)

    assert diff == "--- original\n+++ modified\n" + hunk + MARKER
    stats = measure_unified_diff(diff)
    assert (stats.additions, stats.deletions) == ((0, 1) if delete else (1, 0))

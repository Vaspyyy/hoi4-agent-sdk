from hoi4.structured_patching import patch_scalar_mapping


def test_patch_mapping_preserves_comments_nested_blocks_and_order() -> None:
    source = """# before
first = 1.0 # inline
unknown_block = { future = yes }
second = yes
"""

    rendered = patch_scalar_mapping(source, {"first": 2, "third": "value"})

    assert "first = 2 # inline" in rendered
    assert "unknown_block = { future = yes }" in rendered
    assert "second" not in rendered
    assert "third = value" in rendered


def test_patch_mapping_removes_duplicate_scalar_assignments() -> None:
    rendered = patch_scalar_mapping("key = 1\nkey = 2\n", {"key": 3})

    assert rendered.count("key =") == 1
    assert "key = 3" in rendered

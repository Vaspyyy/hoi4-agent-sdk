"""Shared scalar quoting rules, independent of parsing and script builders."""


def needs_quotes(value: str) -> bool:
    """Quote empty values and characters that delimit tokenizer bare tokens."""
    return not value or any(ch.isspace() or ch in '{}#"=<>!' for ch in value)

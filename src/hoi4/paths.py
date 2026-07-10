"""Safe identifier and output-path handling for mod writers."""

from __future__ import annotations

import re
from pathlib import Path


COUNTRY_TAG_RE = re.compile(r"^[A-Z0-9]{3}$")
EVENT_NAMESPACE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SCRIPT_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")


def require_country_tag(tag: str) -> str:
    normalized = str(tag).upper()
    if not COUNTRY_TAG_RE.fullmatch(normalized):
        raise ValueError(f"Invalid country tag {tag!r}; expected exactly 3 alphanumeric characters")
    return normalized


def require_event_namespace(namespace: str) -> str:
    value = str(namespace)
    if not EVENT_NAMESPACE_RE.fullmatch(value):
        raise ValueError(
            f"Invalid event namespace {namespace!r}; expected a letter/underscore followed by alphanumerics/underscores"
        )
    return value


def require_script_id(value: str, *, label: str = "identifier") -> str:
    text = str(value)
    if not SCRIPT_ID_RE.fullmatch(text):
        raise ValueError(
            f"Invalid {label} {value!r}; path separators, whitespace, and control characters are not allowed"
        )
    return text


def safe_file_stem(value: str, *, fallback: str = "generated") -> str:
    """Return a filename-only slug; never preserve path traversal components."""
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("._")
    return slug or fallback


def resolve_mod_output_path(mod_root: str | Path, path: str | Path) -> Path:
    """Resolve an API output path beneath ``mod_root`` and reject escapes/symlinks."""
    root = Path(mod_root).resolve()
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve(strict=False)
    if not resolved.is_relative_to(root):
        raise ValueError(f"Output path escapes mod root: {path!s}")
    return resolved

"""
Configuration file support for the HOI4 Agent SDK.

Reads .hoi4.json from the current or parent directories to discover
mod_path and hoi4_install paths. This allows agents and tools to
find the correct paths without asking the user each time.

Config file format (.hoi4.json):
{
    "mod_path": "/path/to/my_mod",
    "hoi4_install": "/path/to/Hearts of Iron IV"
}

Both fields are optional. The SDK will search parent directories
up to the filesystem root for the config file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

DEFAULT_CONFIG_FILENAME = ".hoi4.json"


class Config:
    mod_path: Optional[Path]
    hoi4_install: Optional[Path]

    def __init__(
        self,
        mod_path: str | Path | None = None,
        hoi4_install: str | Path | None = None,
    ):
        self.mod_path = Path(mod_path) if mod_path else None
        self.hoi4_install = Path(hoi4_install) if hoi4_install else None

    @classmethod
    def from_dict(cls, data: dict) -> Config:
        return cls(
            mod_path=data.get("mod_path"),
            hoi4_install=data.get("hoi4_install"),
        )

    def to_dict(self) -> dict:
        d: dict[str, str] = {}
        if self.mod_path:
            d["mod_path"] = str(self.mod_path)
        if self.hoi4_install:
            d["hoi4_install"] = str(self.hoi4_install)
        return d

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")


def find_config(start: str | Path | None = None) -> Optional[Config]:
    """Search for .hoi4.json starting from `start` dir, walking up to root.

    If start is None, uses the current working directory.
    Returns Config if found, None otherwise.
    """
    current = Path(start or ".").resolve()

    while True:
        candidate = current / DEFAULT_CONFIG_FILENAME
        if candidate.is_file():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
                return Config.from_dict(data)
            except (json.JSONDecodeError, ValueError):
                return None

        parent = current.parent
        if parent == current:
            break
        current = parent

    return None

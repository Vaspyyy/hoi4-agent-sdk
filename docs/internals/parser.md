# HOI4 Agent SDK Parser Internals

Low-level Paradox parser and serializer reference. Prefer the public `Mod` facade; use this only when the public API cannot represent the required change.

## Low-Level Parser

For direct file manipulation outside the `Mod` facade:

```python
from hoi4 import parse_pdx, serialize_pdx, PdxNode

node = parse_pdx('capital = 1\nset_politics = { ruling_party = democratic }')

node.get_value("capital")          # "1"
node.get_int("capital")            # 1
block = node.get_block("set_politics")
block.get_value("ruling_party")    # "democratic"

node.set_value("capital", "999")
print(serialize_pdx(node))
```

### PdxNode Methods

| Method | Description |
|--------|-------------|
| `is_block() -> bool` | Has children (not a scalar) |
| `is_assignment() -> bool` | Has scalar value |
| `find(key) -> PdxNode \| None` | First child by key |
| `find_all(key) -> list[PdxNode]` | All children by key |
| `get_value(key, default="") -> str` | Scalar value of child |
| `get_int(key, default=0) -> int` | Integer value of child |
| `get_float(key, default=0.0) -> float` | Float value of child |
| `get_block(key) -> PdxNode \| None` | Block child by key |
| `set_value(key, value) -> None` | Update or add child |
| `remove(key) -> None` | Remove all children with key |
| `add_child(node) -> None` | Append child node |

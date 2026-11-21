# How the SDK reads and writes script

Prefer the public `Mod` facade. This page is for people changing the SDK, and
for the rare edit the facade cannot express.

There is exactly one reader for Paradox script, `hoi4.syntax`. Everything else
is built on it:

| Module | Job |
| --- | --- |
| `hoi4.textio` | Bytes on disk to text in memory and back, byte for byte. |
| `hoi4.syntax` | Text to a lossless syntax tree with exact positions. |
| `hoi4.patching` | Change one statement of a text by replacing its exact span. |
| `hoi4.parser` | `PdxNode`: an editable tree for building or re-rendering a whole snippet. |

## Rules for SDK code

1. **Never find script structure with a regular expression over raw text.** A
   regex cannot tell a comment, a quoted string or a nested block from the real
   thing. Ask the tree (`syntax.parse(text)`), or use the span helpers in
   `patching`.
2. **Never re-render a file to change one field.** Replace the span of that
   field (`patching.set_scalar`, `set_block`, `replace_assignment`, ...). All
   other bytes of the file stay as they were, including constructs the model
   does not know.
3. **Read and write files only through `textio`.** `read_text(path)` /
   `decode_text(bytes)` in, `encode_text(text, like=original_bytes)` out. Never
   pass `errors="ignore"`.
4. **Text in memory uses `"\n"` and has no byte-order mark.** The writer puts
   the file's own line endings and byte-order mark back.

## `hoi4.textio`: byte-exact files

```python
from hoi4.textio import read_text, decode_text, encode_text, detect_format

text = read_text(path)                    # "\n" line endings, no BOM, nothing dropped
data = encode_text(new_text, like=path.read_bytes())
```

- Decoding tries UTF-8, then Windows-1252, then Latin-1. The last one maps
  every byte, so no byte is ever dropped.
- `encode_text(text, like=original)` keeps the original's encoding, byte-order
  mark and line endings. Unchanged text returns `original` itself. In a file
  that mixes line endings, each unchanged line keeps its own.
- New text that does not fit a legacy code page upgrades the file to UTF-8,
  which is what the game reads.
- Localisation (`.yml`) is always written with the byte-order mark the game
  requires.

When `Mod.save()` creates a file that overrides one from the game or a base
mod, the new file copies the format of the file it shadows.

## `hoi4.syntax`: the tree

```python
from hoi4.syntax import parse

doc = parse(text)            # never raises
for item in doc.root.items:  # Assignment | Scalar | Block | Tagged, in source order
    ...
focus = doc.root.block("focus_tree").first("focus")
focus.start, focus.end       # exact offsets into text
focus.block.scalar("id")     # value of the first `id = ...`
```

Grammar (the one the game accepts):

```
file   := item*
item   := key OP value | value
value  := scalar | "{" item* "}" | TAG "{" item* "}"     (rgb { 1 2 3 })
OP     := "=" | "<" | ">" | "<=" | ">=" | "!=" | "==" | "?="
scalar := bare word | "quoted string"
```

Node types, all with `start`/`end` offsets and `raw` (the exact source):

| Node | Meaning | Useful members |
| --- | --- | --- |
| `Scalar` | bare word or quoted string | `value` (unquoted), `quoted` |
| `Block` | `{ ... }`, or the whole file | `items`, `body`, `body_start`, `body_end`, `closed` |
| `Tagged` | `rgb { ... }`, `hsv { ... }` | `tag`, `block` |
| `Assignment` | `key OP value` | `key`, `name`, `op`, `value`, `scalar`, `block` |

`Block` queries: `assignments(key=None)`, `first(key)`, `scalar(key, default)`,
`block(key)`, `blocks(key)`, `values()` (the bare scalars of a list), and
`walk()` (every assignment at any depth with its parents). Pass `fold=True` to
compare keys without regard to case, the way the game reads effect and trigger
names such as `IF`.

`Document` also has `comments` (their spans), `in_comment(offset)`,
`without_comments()`, `line_col(offset)` and `problems`.

### Malformed script

The parser is as forgiving as the game: a block still open at the end of the
file is closed there, and a stray closing brace is skipped. Each repair is a
`Problem` in `doc.problems` with a `kind`:
`unclosed_block`, `stray_close`, `missing_value`, `stray_operator`,
`unterminated_string`.

- `syntax.require_well_formed(text)` raises `ParseError` if there is any.
- `syntax.repair(text)` spells out the game's reading: it appends the missing
  closing braces and removes stray ones.

The loading policy built on this:

- **Files from the game or a base mod are read the way the game reads them.**
  The game ships files with unbalanced braces (Norway's history, France's 1936
  army), and a mod author cannot fix those.
- **A malformed file in the mod's own folder is a load failure**, reported in
  `mod.load_diagnostics` and by `validate()`. It is the author's to fix, so the
  SDK does not guess.
- When the SDK copies a tolerated inherited file into the mod as an override,
  it writes the repaired form, so the mod's copy is well formed.

## `hoi4.patching`: change one statement

```python
from hoi4.patching import top_level_assignments, set_scalar, set_block

text = set_scalar(text, "cost", "7.2")           # only the value span changes
text = set_block(text, "completion_reward", "add_political_power = 50")
for span in top_level_assignments(body):         # AssignmentSpan, depth zero
    span.key, span.operator, span.tag, body[span.value_start:span.value_end]
```

- `top_level_assignments(text)` returns `key = value` statements at depth zero.
  Pass `comparisons=True` to also get `key > value` and friends.
- An `AssignmentSpan` for `color = rgb { 1 2 3 }` has `tag == "rgb"` and a
  value span that covers the tag and the block.
- These helpers work on any text, so the usual pattern for a nested edit is:
  take the body of the block (`text[span.body_start:span.body_end]`), patch it,
  and splice it back with `replace_assignment_body`.

## `hoi4.parser`: `PdxNode`

```python
from hoi4 import parse_pdx, serialize_pdx, PdxNode

node = parse_pdx('capital = 1\nset_politics = { ruling_party = democratic }')
node.get_int("capital")                           # 1
node.get_block("set_politics").get_value("ruling_party")
node.set_value("capital", "999")
print(serialize_pdx(node))
```

`serialize_pdx` renders the tree again, one statement per line, so it
normalises whitespace. Use it to build new script, not to edit a file in place.
It keeps quoted keys and values, nested lists (`{ { 1 2 } { 3 4 } }`), typed
blocks (`rgb { ... }`) and comments.

`parse_pdx(text)` raises `ParseError` for malformed input. Pass `strict=False`
to read it the way the game does.

| `PdxNode` method | Description |
| --- | --- |
| `is_block() -> bool` | Has children (not a scalar) |
| `is_assignment() -> bool` | Has a scalar value |
| `find(key) -> PdxNode \| None` | First child by key |
| `find_all(key) -> list[PdxNode]` | All children by key |
| `get_value(key, default="") -> str` | Scalar value of a child |
| `get_int(key, default=0) -> int` | Integer value of a child |
| `get_float(key, default=0.0) -> float` | Float value of a child |
| `get_block(key) -> PdxNode \| None` | Block child by key |
| `set_value(key, value) -> None` | Update or add a scalar child |
| `remove(key) -> None` | Remove all children with the key |
| `add_child(node) -> None` | Append a child node |

Fields: `key`, `value`, `children`, `operator`, `is_comment`, `block`,
`quoted`, `key_quoted`, and `tag` (`"rgb"` for `color = rgb { ... }`).

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
from hoi4.textio import read_text, decode_text, encode_text, detect_format, write_text

text = read_text(path)                    # "\n" line endings, no BOM, nothing dropped
data = encode_text(new_text, like=path.read_bytes())
detect_format(data)                       # TextFormat(encoding, bom, newline, mixed_newlines, stray_bytes)
write_text(path, new_text, like=original) # encode_text, then write; returns the bytes
```

- Decoding works line by line, so what a line means never depends on the
  rest of the file:
  - a valid UTF-8 line is read as UTF-8;
  - a line with stray bytes is read as UTF-8 when the file has a byte-order
    mark or when at least as many of the line's high bytes belong to valid
    UTF-8 sequences as do not. Valid sequences are read as UTF-8 and each
    stray byte as its Windows-1252 character;
  - any other line is a legacy line and is read as Windows-1252.

  The five bytes Windows-1252 leaves undefined are read as Latin-1. Every
  byte maps to one character and back, so no byte is dropped and text in
  memory never holds U+FFFD or a lone surrogate. `detect_format` names the
  file's encoding (`utf-8`, or `cp1252` / `latin-1` when most high bytes are
  not valid UTF-8 and there is no byte-order mark) and counts the stray bytes
  of a UTF-8 file in `stray_bytes`.
- Why per line and not per file: the writer re-encodes only edited lines (see
  below), so a legacy file can gain a UTF-8 line. A per-file guess would read
  that line back as mojibake whenever the legacy lines outnumber it. Per
  line, a saved file always reads back as the text that was saved.
- Only CR LF, a lone CR and LF are line breaks. A form feed or a Unicode line
  separator stays inside its line.
- `encode_text(text, like=original)` keeps the original's encoding, byte-order
  mark and line endings. Unchanged text returns `original` itself. Every
  unchanged line keeps its exact bytes: its own line ending in a file that
  mixes styles, and its stray bytes in a UTF-8 file.
- Only edited lines are encoded: in the file's encoding when the line fits
  and reads back the same, otherwise as UTF-8, which is what the game reads.
  The rest of a legacy file stays in its code page. (A Windows-1252 pair such
  as `ß“` is also valid UTF-8, so such a line is written as UTF-8 too.) A
  clean file with one line-ending style is still encoded in one go.
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

`parse` never modifies the text and never raises. It caches the documents of
the last few large texts, so treat a `Document` as read-only.

Grammar (the one the game accepts):

```
file   := item*
item   := key OP value | value
value  := scalar | "{" item* "}" | TAG "{" item* "}"     (rgb { 1 2 3 })
OP     := "=" | "<" | ">" | "<=" | ">=" | "!=" | "==" | "?="
scalar := bare word | "quoted string"
```

Details the game's reader shares, and so does this one:

- Whitespace is only space, tab, CR, LF, form feed and vertical tab, plus a
  stray byte-order mark. Any other character, such as a no-break space, is
  part of a word, so the parser can never stop early.
- A comment runs from `#` to the end of the line. A comment may sit between a
  key and its operator, or between an operator and its value:
  `key # note` with `= value` on the next line is one statement.
- `TAG` is `rgb`, `hsv` or `hsv360`, in any case (`HSV { ... }` too). A bare
  `rgb { 1 2 3 }` inside a list is one `Tagged` value, not a word and a block.
- Inline maths such as `@[ 1 + 2 ]` is one word.
- A lone `!` or `?` belongs to a word (`foo?0`); only `!=` and `?=` are
  operators.

Node types, all with `start`/`end` offsets and `raw` (the exact source):

| Node | Meaning | Useful members |
| --- | --- | --- |
| `Scalar` | bare word or quoted string | `value` (unquoted), `quoted` |
| `Block` | `{ ... }`, or the whole file | `items`, `body`, `body_start`, `body_end`, `closed` |
| `Tagged` | `rgb { ... }`, `hsv { ... }`, `hsv360 { ... }` | `tag` (a `Scalar`), `block` |
| `Assignment` | `key OP value` | `key` (a `Scalar`), `name`, `op`, `value`, `scalar`, `block` |

`Block` queries: `assignments(key=None)`, `first(key)`, `scalar(key, default)`,
`block(key)`, `blocks(key)`, `values()` (the bare scalars of a list), and
`walk()` (every assignment at any depth with the chain of assignments around
it, in source order: an assignment, then everything inside its block, then the
next one). `scalar()` only looks at `key = value` statements, not comparisons
such as `key > value`. Pass `fold=True` to compare keys without regard to
case, the way the game reads effect and trigger names such as `IF`.

`Document` also has `text`, `root`, `comments` (their spans),
`in_comment(offset)`, `without_comments()` (line breaks are kept),
`line_col(offset)` and `problems`. The module also exports `quote(value)` and
`unquote(raw)`, which add or remove quotes and escapes only when needed.

### Malformed script

The parser is as forgiving as the game: a block still open at the end of the
file is closed there, and a stray closing brace is skipped. Each repair is a
`Problem` in `doc.problems` with a `kind`:
`unclosed_block`, `stray_close`, `missing_value`, `stray_operator`,
`unterminated_string`.

- `syntax.require_well_formed(text, source=None)` raises `ParseError` if there
  is any, with the line and column of the first one.
- `syntax.repair(text)` spells out the game's reading: it appends the missing
  closing braces and removes stray ones. Other problems (a missing value, an
  unterminated string) cannot be repaired and stay as they are.

The loading policy built on this:

- **Files from the game or a base mod are read the way the game reads them.**
  The game ships files with unbalanced braces (Norway's history, France's 1936
  army), and a mod author cannot fix those.
- **A malformed file in the mod's own folder is a load failure**, reported in
  `mod.load_diagnostics` and by `validate()` (or raised with
  `strict_loading=True`). It is the author's to fix, so the SDK does not guess.
  Saving refuses to rewrite any mod file that failed to load, because its
  content was never fully read. It also refuses to create a new override of
  a game file in the same folder: the broken file may already hold the mod's
  version of that content.
- When the SDK copies a tolerated inherited file into the mod as an override,
  it writes the repaired form, so the mod's copy is well formed. `preview()`
  shows that repaired form.

## `hoi4.patching`: change one statement

```python
from hoi4.patching import top_level_assignments, set_scalar, set_block, sync_body

text = set_scalar(text, "cost", "7.2")           # only the value span changes
text = set_block(text, "completion_reward", "add_political_power = 50")
for span in top_level_assignments(body):         # AssignmentSpan, depth zero
    span.key, span.operator, span.tag, body[span.value_start:span.value_end]
```

- `top_level_assignments(text)` returns `key = value` statements at depth zero.
  Pass `comparisons=True` to also get `key > value` and friends;
  `span.operator` says which.
- An `AssignmentSpan` for `color = rgb { 1 2 3 }` has `tag == "rgb"` and a
  value span that covers the tag and the block.
- Other helpers: `assignment_spans(text, key)`, `replace_assignment(text, span,
  replacement)` (`None` removes the statement), `append_assignment(text,
  statement)`, `move_assignment_before(text, span, target)`,
  `scalar_values_equivalent()` and `block_bodies_equivalent()`. The last two
  compare values by meaning: `7.0` equals `7`, and spacing and comments do not
  count.
- Removing or replacing the first statement of a line that holds several (or
  one followed by a comment) leaves the rest of the line in place.
- A replacement or appended statement is written at column zero; its later
  lines are moved to the indentation of the place it goes to. The first
  statement of an empty multi-line block gets its own line, one level deeper
  than the closing brace.
- These helpers work on any text, so the usual pattern for a nested edit is:
  take the body of the block (`text[span.body_start:span.body_end]`), patch it,
  and splice it back with `replace_assignment_body`.

### A block body changes statement by statement

`set_block(text, key, body)` does not write the block again. It hands the old
and the new body to `sync_body(current, new)`, which lines up their statements
and changes only the ones that differ:

- A statement both bodies have keeps its exact bytes, whatever its spacing.
- A statement that only the old body has is cut out, together with the comment
  on its line. One that only the new body has is put in, on its own line or,
  in a one-line block or a list that shares lines (`provinces = { 1 2 3 }`),
  on the line.
- A statement with the same key and a new value is the same statement: a
  scalar value is replaced, a block is edited the same way one level down, and
  the comment on its line stays.

So a list gets one more entry, or a reward gets one changed line, and every
comment and every other statement stays where it was. `sync_body` checks its
own result: if the layout cannot hold the edit (a comment put into a one-line
block would comment out the closing brace), it returns `None` and `set_block`
writes the body as a whole.

Comments: they are compared as soon as the new body has any. A changed, added
or dropped comment is then written. A new body without comments says nothing
about the comments in the file, so they are left alone; pass `comments=True`
to remove them too. `same_block_body(current, new)` answers "would this edit
change anything?" by the same rule, and is what the content modules use to
decide whether a text field changed.

`sync_body(current, new, keep=("name", "ai_chance"))` leaves the statements
with those keys alone and does not expect them in `new`. Event options use it:
an option's effect is every statement and comment that is not `name`,
`ai_chance` or `trigger`. `statement_spans(text, keep=...)` returns the spans
that such an edit owns.

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

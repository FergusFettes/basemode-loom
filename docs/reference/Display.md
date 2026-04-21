# Display

`basemode_loom.display`

UI-agnostic rendering layer. Takes `SessionState` and produces `DisplayLine` objects that any UI layer can convert to its own rendering primitives.

## Functions

### `build_loom_display`

```python
build_loom_display(state: SessionState, width: int) -> list[DisplayLine]
```

Builds the branch view display: the current path from root to active node, followed by the selected child's continuation, with siblings shown on the right.

### `build_tree_display`

```python
build_tree_display(state: SessionState, width: int) -> list[DisplayLine]
```

Builds the tree view display: the full tree structure as an indented outline.

### `wrap_text`

```python
wrap_text(text: str, width: int) -> list[str]
```

Word-wraps text to `width` columns, returning a list of lines.

### `word_wrap_inline`

```python
word_wrap_inline(text: str, first_width: int, full_width: int) -> list[str]
```

Word-wraps text where the first line has a different available width than subsequent lines (e.g., when inline text follows a label).

## DisplayLine

```python
@dataclass(frozen=True)
class DisplayLine:
    text: str
    style: Literal["normal", "bold", "dim", "path", "current", "selected"]
    spans: tuple[DisplaySpan, ...]
```

## DisplaySpan

```python
@dataclass(frozen=True)
class DisplaySpan:
    start: int
    end: int
    style: str
```

Marks a character range within the line's `text` with an additional style (e.g., model name annotations).

## Usage

UI layers call these functions and convert `DisplayLine` objects to their own primitives:

```python
# Textual TUI
lines = build_loom_display(state, width=terminal_width)
for line in lines:
    rich_text = convert_to_rich(line)
    widget.write(rich_text)

# Hypothetical web backend
lines = build_loom_display(state, width=80)
html_lines = [line_to_html(line) for line in lines]
```

This separation keeps display logic testable without a UI framework and makes it straightforward to add new rendering targets.

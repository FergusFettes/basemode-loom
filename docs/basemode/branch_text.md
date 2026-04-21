# branch_text

`basemode.continue_.branch_text`

Streams N parallel continuations simultaneously, interleaving tokens as they arrive.

## Signature

```python
async def branch_text(
    prefix: str,
    model: str = "gpt-4o-mini",
    *,
    n: int = 4,
    max_tokens: int = 200,
    temperature: float = 0.9,
    strategy: str | None = None,
    rewind: bool = False,
    **extra,
) -> AsyncGenerator[tuple[int, str], None]
```

## What it does

Launches `n` concurrent calls to `continue_text()` and multiplexes their output through an async queue. Tokens from all branches interleave in the order they arrive from the API.

## Parameters

Same as [[continue_text]], plus:

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `n` | `int` | `4` | Number of parallel continuations to generate |

Note: `context` is not a parameter of `branch_text` — pass it via `**extra` if needed, or use `continue_text` with a system prompt.

## Yields

`tuple[int, str]` — `(branch_index, token)`. `branch_index` is 0-based, ranging from `0` to `n-1`.

## Examples

```python
import asyncio
from basemode import branch_text

async def main():
    branches = [""] * 4

    async for idx, token in branch_text(
        "The message in the bottle read:",
        model="gpt-4o-mini",
        n=4,
        max_tokens=200,
    ):
        branches[idx] += token

    for i, text in enumerate(branches):
        print(f"\n[{i}] {text}")

asyncio.run(main())
```

```python
# Stream tokens as they arrive
async def stream_live():
    async for idx, token in branch_text("She reached for the door", n=3):
        print(f"[{idx}]{token}", end="", flush=True)
```

## Notes

- All `n` generations are started concurrently. On most APIs they run in parallel server-side as well, so wall-clock time is similar to a single generation.
- Tokens from slow branches will arrive less frequently; fast branches may complete before slow ones start yielding.
- The generator completes when all `n` branches have finished.
- This is the function that `basemode-loom` calls internally when you press Space in the TUI or run `basemode-loom run -n 4`.

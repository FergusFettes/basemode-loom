# continue_text

`basemode.continue_.continue_text`

Streams a single text continuation from a language model.

## Signature

```python
async def continue_text(
    prefix: str,
    model: str = "gpt-4o-mini",
    *,
    max_tokens: int = 200,
    temperature: float = 0.9,
    context: str = "",
    strategy: str | None = None,
    rewind: bool = False,
    **extra,
) -> AsyncGenerator[str, None]
```

## What it does

1. Normalizes the model name (see [[basemode Model Normalization]])
2. Detects the best continuation strategy for the model (see [[basemode Strategies]])
3. Optionally rewinds the prefix to the nearest word boundary (`rewind=True`)
4. Calls the strategy's `stream()` method
5. Applies token boundary healing to the output stream
6. Yields healed tokens one at a time

## Parameters

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `prefix` | `str` | required | Text to continue from |
| `model` | `str` | `"gpt-4o-mini"` | Model name; normalized automatically |
| `max_tokens` | `int` | `200` | Maximum tokens to generate |
| `temperature` | `float` | `0.9` | Sampling temperature |
| `context` | `str` | `""` | System context / framing (inserted as system message) |
| `strategy` | `str \| None` | `None` | Strategy override; `None` = auto-detect |
| `rewind` | `bool` | `False` | If `True`, trim prefix back to the last word boundary before sending |
| `**extra` | | | Additional kwargs forwarded to the model API |

## Yields

`str` — tokens as they arrive, with boundaries healed. Token size varies by model and position; typically 1–5 characters.

## Examples

```python
import asyncio
from basemode import continue_text

# Basic usage
async def main():
    async for token in continue_text("The old lighthouse keeper"):
        print(token, end="", flush=True)

asyncio.run(main())

# Collect full output
async def collect():
    tokens = []
    async for token in continue_text(
        "She examined the artifact carefully",
        model="anthropic/claude-opus-4-7",
        max_tokens=400,
        context="This is a science fiction story.",
    ):
        tokens.append(token)
    return "".join(tokens)

text = asyncio.run(collect())
```

## Notes

- The `rewind` option is useful when you want generation to feel continuous even when the prefix ends mid-word. With `rewind=True`, the last partial word is trimmed from the prefix before sending, so the model starts fresh from the previous word boundary.
- `**extra` is forwarded directly to the underlying LiteLLM call. Useful for provider-specific parameters like `top_p`, `seed`, etc.
- Temperature is silently removed for models that don't support it (GPT-5, o-series). Pass any temperature value — it won't cause an error.

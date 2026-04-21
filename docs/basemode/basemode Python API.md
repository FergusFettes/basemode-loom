# Python API

```python
from basemode import continue_text, branch_text, detect_strategy, GenerationParams
```

## `continue_text()`

Stream a single text continuation.

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

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `prefix` | `str` | The text to continue from |
| `model` | `str` | Model name (normalized automatically) |
| `max_tokens` | `int` | Max tokens to generate |
| `temperature` | `float` | Sampling temperature |
| `context` | `str` | System context / framing text |
| `strategy` | `str \| None` | Force a strategy; `None` = auto-detect |
| `rewind` | `bool` | Rewind prefix to word boundary before sending |
| `**extra` | | Extra kwargs passed to the model API |

**Yields:** `str` — one token at a time, with boundaries healed.

```python
import asyncio
from basemode import continue_text

async def main():
    async for token in continue_text(
        "The last transmission came through at midnight",
        model="gpt-4o-mini",
        max_tokens=300,
        temperature=1.0,
    ):
        print(token, end="", flush=True)
    print()

asyncio.run(main())
```

## `branch_text()`

Stream N parallel continuations simultaneously.

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

**Parameters:** same as `continue_text()`, plus:

| Name | Type | Description |
|------|------|-------------|
| `n` | `int` | Number of parallel continuations |

**Yields:** `tuple[int, str]` — `(branch_index, token)`. Branch indices are 0-based. Tokens from different branches interleave as they arrive.

```python
import asyncio
from basemode import branch_text

async def main():
    branches = [""] * 4
    async for idx, token in branch_text(
        "The experiment had one unexpected result:",
        model="gpt-4o-mini",
        n=4,
        max_tokens=200,
    ):
        branches[idx] += token

    for i, text in enumerate(branches):
        print(f"\n--- Branch {i} ---")
        print(text)

asyncio.run(main())
```

## `detect_strategy()`

Auto-detect or validate a continuation strategy for a model.

```python
def detect_strategy(model: str, override: str | None = None) -> ContinuationStrategy
```

Returns a `ContinuationStrategy` instance. If `override` is given, validates it's a known strategy name and returns that strategy instead.

```python
from basemode import detect_strategy

s = detect_strategy("gpt-4o-mini")
print(s.name)  # "system"

s = detect_strategy("claude-opus-4-7")
print(s.name)  # "prefill"
```

## `GenerationParams`

Dataclass for bundling generation parameters.

```python
@dataclass
class GenerationParams:
    model: str
    max_tokens: int = 200
    temperature: float = 0.9
    context: str = ""
    extra: dict = field(default_factory=dict)
```

Used internally by strategy implementations. You can construct one if you need to pass parameters around:

```python
from basemode import GenerationParams

params = GenerationParams(
    model="gpt-4o-mini",
    max_tokens=400,
    temperature=0.8,
    context="You are helping write a mystery novel.",
)
```

## Low-level: calling a strategy directly

If you need to call a strategy without the healing and normalization that `continue_text` applies:

```python
from basemode import detect_strategy, GenerationParams

strategy = detect_strategy("gpt-4o-mini")
params = GenerationParams(model="openai/gpt-4o-mini", max_tokens=200)

async for token in strategy.stream("The prefix text", params):
    print(token, end="", flush=True)
```

## Usage estimation

```python
from basemode.usage import get_price_info, estimate_usage

info = get_price_info("gpt-4o-mini")
print(info.input_cost_per_token)   # float | None
print(info.max_output_tokens)      # int | None

estimate = estimate_usage(
    "gpt-4o-mini",
    prompt="The ship rounded",
    completion=" the headland and entered the harbor.",
)
print(estimate.total_tokens)
print(estimate.cost_usd)
```

## Key management

```python
from basemode.keys import set_key, get_key, list_keys, load_into_environ

# Store a key
set_key("OPENAI_API_KEY", "sk-...")

# Retrieve (returns None if not stored)
key = get_key("OPENAI_API_KEY")

# Load all stored keys into os.environ
load_into_environ()

# List all keys (masked)
keys = list_keys()
```

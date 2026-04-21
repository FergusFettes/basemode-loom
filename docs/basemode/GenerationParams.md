# GenerationParams

`basemode.params.GenerationParams`

Dataclass for bundling generation parameters. Used internally by strategy implementations and available for callers who want to pass parameters as a single object.

## Definition

```python
@dataclass
class GenerationParams:
    model: str
    max_tokens: int = 200
    temperature: float = 0.9
    context: str = ""
    extra: dict = field(default_factory=dict)
```

## Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `model` | `str` | required | Normalized model name (e.g. `"openai/gpt-4o-mini"`) |
| `max_tokens` | `int` | `200` | Max tokens to generate |
| `temperature` | `float` | `0.9` | Sampling temperature |
| `context` | `str` | `""` | System context / framing text |
| `extra` | `dict` | `{}` | Additional kwargs forwarded to the model API |

## Usage

```python
from basemode import GenerationParams

params = GenerationParams(
    model="openai/gpt-4o-mini",
    max_tokens=400,
    temperature=0.8,
    context="This is a technical writing exercise.",
)
```

Passing to a strategy directly:

```python
from basemode import detect_strategy, GenerationParams

strategy = detect_strategy("gpt-4o-mini")
params = GenerationParams(model="openai/gpt-4o-mini")

async for token in strategy.stream("The function returns", params):
    print(token, end="", flush=True)
```

## Notes

`model` should be the already-normalized form (with provider prefix) when constructing `GenerationParams` manually. When using `continue_text()` or `branch_text()`, normalization happens automatically before `GenerationParams` is constructed.

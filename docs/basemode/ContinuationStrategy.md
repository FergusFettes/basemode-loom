# ContinuationStrategy

`basemode.strategies.base.ContinuationStrategy`

Abstract base class for all continuation strategies. Each strategy implements a different technique for coercing a chat-tuned LLM into generating raw text continuation.

## Abstract interface

```python
class ContinuationStrategy(ABC):
    name: str  # class variable; identifies the strategy

    @abstractmethod
    async def stream(
        self,
        prefix: str,
        params: GenerationParams,
    ) -> AsyncGenerator[str, None]:
        ...
```

`stream()` yields raw tokens as they arrive from the API, before any boundary healing is applied.

## Concrete implementations

| Class | `name` | Used for |
|-------|--------|---------|
| `CompletionStrategy` | `"completion"` | OpenAI base models (`/completions` endpoint) |
| `PrefillStrategy` | `"prefill"` | Anthropic models (assistant prefill trick) |
| `SystemPromptStrategy` | `"system"` | Most chat models (system instruction coercion) |
| `FewShotStrategy` | `"few_shot"` | Stubborn models that ignore system prompts |
| `FIMStrategy` | `"fim"` | Code models with FIM token support |

## Strategy registry

```python
from basemode.strategies import REGISTRY

# dict[str, type[ContinuationStrategy]]
print(list(REGISTRY.keys()))
# ["completion", "prefill", "system", "few_shot", "fim"]
```

## Instantiation

Strategies are typically obtained via `detect_strategy()`, not constructed directly:

```python
from basemode import detect_strategy

strategy = detect_strategy("gpt-4o-mini")
# strategy is a SystemPromptStrategy instance
```

Or by name from the registry:

```python
from basemode.strategies import REGISTRY

StrategyClass = REGISTRY["prefill"]
strategy = StrategyClass()
```

## Writing a custom strategy

```python
from basemode.strategies.base import ContinuationStrategy
from basemode.params import GenerationParams
from typing import AsyncGenerator

class MyStrategy(ContinuationStrategy):
    name = "my_strategy"

    async def stream(
        self,
        prefix: str,
        params: GenerationParams,
    ) -> AsyncGenerator[str, None]:
        # Call the model and yield tokens
        ...
        yield token
```

Register it to make it accessible by name:

```python
from basemode.strategies import REGISTRY
REGISTRY["my_strategy"] = MyStrategy
```

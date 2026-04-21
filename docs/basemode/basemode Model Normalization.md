# Model Normalization

basemode aggressively normalizes model names before strategy detection and API calls. This lets you use short names, aliases, and provider-prefixed forms interchangeably.

## What normalization does

1. **Resolves aliases** — short names like `claude-opus-4-7` expand to `anthropic/claude-opus-4-7`
2. **Infers provider prefix** — `gpt-4o-mini` becomes `openai/gpt-4o-mini`
3. **Fixes Anthropic name format** — dots become dashes (`4.6` → `4-6`)
4. **Passes through** already-normalized names unchanged

## Examples

| Input | Normalized |
|-------|-----------|
| `gpt-4o-mini` | `openai/gpt-4o-mini` |
| `claude-opus-4-7` | `anthropic/claude-opus-4-7` |
| `claude-3-5-sonnet` | `anthropic/claude-3-5-sonnet-20241022` |
| `gemini-2.0-flash` | `google/gemini-2.0-flash` |
| `deepseek-coder` | `deepseek/deepseek-coder` |
| `anthropic/claude-opus-4-7` | `anthropic/claude-opus-4-7` (unchanged) |

## Usage in code

```python
from basemode.detect import normalize_model

model = normalize_model("claude-opus-4-7")
# → "anthropic/claude-opus-4-7"
```

Normalization happens automatically inside `continue_text()` and `branch_text()` — you rarely need to call it directly.

## Provider inference

When a model name has no provider prefix, basemode scans a list of known model name fragments to determine the provider:

- `gpt`, `o1`, `o3`, `davinci`, `turbo` → `openai`
- `claude` → `anthropic`
- `gemini`, `gemma` → `google`
- `llama` → `together` (or `groq` depending on context)
- `deepseek` → `deepseek`
- `mistral` → `mistral`
- etc.

If detection fails, the name is passed through as-is and LiteLLM attempts its own resolution.

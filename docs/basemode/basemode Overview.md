# basemode

**basemode** is a Python library and CLI for clean text continuation with any LLM.

The problem it solves: modern LLMs are chat-tuned and respond to continuation prompts with preambles like *"Sure! Here's a continuation:"* rather than just writing the next words. basemode wraps any LLM with provider-specific coercion strategies that force it to output raw continuation text — no acknowledgment, no formatting, no wrapper.

It is the model layer underneath [[index]] (basemode-loom). loom handles trees, storage, and navigation; basemode handles the actual generation.

## What it provides

- **`continue_text()`** — stream a single continuation from any model
- **`branch_text()`** — stream N parallel continuations simultaneously
- **Automatic strategy detection** — picks the right coercion technique per model
- **Token boundary healing** — fixes split words and spacing artifacts during streaming
- **20+ provider support** — OpenAI, Anthropic, Google, Groq, Together, OpenRouter, and more
- **CLI** — `basemode "your text"` for one-shot use

## Quick example

```python
import asyncio
from basemode import continue_text

async def main():
    async for token in continue_text(
        "The ship rounded the headland and",
        model="gpt-4o-mini",
        max_tokens=200,
    ):
        print(token, end="", flush=True)

asyncio.run(main())
```

```bash
# CLI
basemode "The ship rounded the headland and" --model gpt-4o-mini
```

## Navigation

- [[basemode Quickstart]] — get running in 2 minutes
- [[basemode Strategies]] — how strategy detection and coercion work
- [[basemode Python API]] — full async API reference
- [[basemode CLI Reference]] — all CLI commands and flags
- [[continue_text]] · [[branch_text]] · [[GenerationParams]] — detailed references

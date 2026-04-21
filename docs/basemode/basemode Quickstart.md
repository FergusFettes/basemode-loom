# Quickstart

## CLI

```bash
# One-shot continuation
basemode "The detective opened the envelope and"

# Multiple branches
basemode "The detective opened the envelope and" --branches 4

# Explicit model
basemode "The detective opened the envelope and" --model anthropic/claude-opus-4-7

# Pipe input
cat chapter1.txt | basemode --max-tokens 500

# Find out what strategy will be used
basemode info gpt-4o-mini
```

## Python

```python
import asyncio
from basemode import continue_text, branch_text

# Single continuation
async def single():
    async for token in continue_text(
        "The detective opened the envelope and",
        model="gpt-4o-mini",
        max_tokens=200,
        temperature=0.9,
    ):
        print(token, end="", flush=True)
    print()

asyncio.run(single())

# Parallel branches
async def branches():
    results = ["", "", "", ""]
    async for idx, token in branch_text(
        "She looked at the map and",
        model="gpt-4o-mini",
        n=4,
        max_tokens=200,
    ):
        results[idx] += token
        print(f"\r[{idx}] {results[idx][-60:]}", end="", flush=True)
    print()

asyncio.run(branches())
```

## Set a default model

```bash
basemode default gpt-4o-mini
# Now you can omit --model
basemode "Continuation text"
```

## List available models

```bash
basemode models --available          # only models with keys set
basemode models --provider anthropic # filter by provider
basemode models --search claude      # search by name
```

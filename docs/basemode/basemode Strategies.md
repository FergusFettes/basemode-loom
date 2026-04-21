# Strategies

A **continuation strategy** is the technique used to coerce a chat-tuned LLM into outputting raw text continuation rather than a conversational response.

## Why strategies are needed

Chat-tuned models respond to user messages. If you send *"The ship rounded the headland and"* as a user message, most models reply with something like:

> "Sure! Here's a continuation of that sentence: *The ship rounded the headland and entered the calm harbor...*"

That preamble is useless. Strategies suppress it.

## The five strategies

### `completion`

Used for: OpenAI base models (`gpt-3.5-turbo-instruct`, `davinci-002`)

These models expose the `/completions` endpoint, which continues text natively — no coercion needed. The prefix is sent as-is and the model continues it.

### `prefill`

Used for: Anthropic models (Claude 2, Claude 3, some Claude 4 variants)

Anthropic's API allows seeding the assistant turn before generation begins. basemode puts the full prefix in the system prompt and seeds the assistant turn with the last ~20 characters. The model, seeing it has "already started" the response, continues naturally from that point.

This is the cleanest strategy when it works — output requires minimal post-processing.

### `system`

Used for: Most chat models as the primary or fallback strategy

Sends a system prompt instructing the model to output only the continuation text, with no acknowledgment. Works on GPT-4, most Gemini models, and any model that follows system instructions reliably. Requires space-prefix repair on outputs.

### `few_shot`

Used for: Stubborn models that ignore plain system instructions

Augments the system prompt with four varied examples showing the desired continuation behavior (fiction, technical, poetry, dialogue). The examples demonstrate the pattern clearly enough that even models resistant to direct instruction tend to comply.

### `fim`

Used for: Code-specialized models (DeepSeek Coder, StarCoder, CodeLlama)

Uses the model's native fill-in-the-middle tokens (`<fim_prefix>`, `<fim_suffix>`, `<fim_middle>` or equivalent). The prefix is provided as the FIM prefix, with an empty FIM suffix, so the model generates continuation tokens directly.

## Strategy detection

basemode auto-detects the right strategy from the model name:

```python
from basemode import detect_strategy

strategy = detect_strategy("gpt-4o-mini")       # → SystemPromptStrategy
strategy = detect_strategy("claude-opus-4-7")   # → PrefillStrategy
strategy = detect_strategy("deepseek-coder")    # → FIMStrategy
```

Detection logic:
1. Normalize the model name (resolve aliases, infer provider prefix)
2. Check for known completion-endpoint models
3. Check provider prefix for Anthropic → prefill
4. Check for code models → FIM
5. Default → system prompt

## Override

```python
async for token in continue_text(
    "prefix",
    model="gpt-4o-mini",
    strategy="few_shot",   # force a specific strategy
):
    ...
```

```bash
basemode "prefix" --strategy few_shot
```

## Compatibility handling

Some models need special treatment beyond strategy selection:

- **GPT-5, o-series**: temperature parameter is rejected — basemode strips it automatically
- **Claude 4.6+**: prefill is not supported — automatically falls back to system prompt
- **Gemini 2.5, Kimi K2.5**: thinking/reasoning models — basemode allocates a thinking budget automatically and strips the `<think>` block from output

These quirks are handled in `strategies/compat.py` and are transparent to callers.

## Token boundary healing

During streaming, word boundaries can fall mid-token. basemode's healing layer:

1. Buffers the final few tokens of each generation
2. Detects split compounds (`coward` + `ice` → should be `cowardice`)
3. Repairs leading/trailing spaces based on context
4. Collapses unnecessary newlines
5. Removes any rewound prefix fragments if `rewind=True`

This happens automatically and is transparent to callers.

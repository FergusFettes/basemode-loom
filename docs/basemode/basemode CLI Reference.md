# CLI Reference

The basemode CLI is invoked as `basemode`. The default command (`basemode "text"`) is shorthand for `basemode run "text"`.

## `basemode` / `basemode run`

Generate a continuation.

```bash
basemode "The ship rounded the headland and"
basemode run "The ship rounded the headland and"
```

| Option | Default | Description |
|--------|---------|-------------|
| `--model`, `-m` | stored default or `gpt-4o-mini` | Model to use |
| `--max-tokens`, `-M` | `200` | Max tokens to generate |
| `--temperature`, `-t` | `0.9` | Sampling temperature |
| `--branches`, `-n` | `1` | Number of parallel continuations |
| `--strategy`, `-s` | auto | Force a specific strategy (`system`, `prefill`, `few_shot`, `completion`, `fim`) |
| `--context`, `-c` | `""` | System context / framing |
| `--rewind` | off | Rewind prefix to word boundary before sending |
| `--show-strategy` | off | Print the detected strategy and exit |

```bash
# Pipe text in
cat chapter.txt | basemode --max-tokens 500

# Parallel branches, shown side-by-side
basemode "She opened the door to find" --branches 4

# Show what strategy will be used without generating
basemode "prefix" --show-strategy
```

## `basemode models`

List available models.

```bash
basemode models
basemode models --available          # only models with API keys configured
basemode models --provider openai    # filter by provider
basemode models --search claude      # search by name fragment
```

## `basemode providers`

List all known providers.

```bash
basemode providers
```

## `basemode strategies`

List all available continuation strategies with descriptions.

```bash
basemode strategies
```

## `basemode info`

Show strategy detection and pricing info for a model.

```bash
basemode info gpt-4o-mini
basemode info anthropic/claude-opus-4-7
```

Outputs:
- Detected strategy
- Input/output cost per 1M tokens
- Context and output token limits

## `basemode keys`

Manage stored API keys.

```bash
basemode keys set openai        # prompts for key value
basemode keys set anthropic
basemode keys get openai        # show stored key (masked)
basemode keys list              # show all stored keys (masked)
```

Keys are stored in `~/.config/basemode/auth.json` with `0o600` permissions.

## `basemode default`

Get or set the default model.

```bash
basemode default                  # show current default
basemode default gpt-4o-mini      # set default
basemode default --clear          # clear default
```

Once set, `--model` can be omitted from `basemode run`.

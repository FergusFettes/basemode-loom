# Installation

## From PyPI

```bash
pip install basemode
```

## From source

```bash
git clone https://github.com/fergus/basemode
cd basemode
uv install
```

## Requirements

Python 3.11+

## API keys

basemode uses LiteLLM under the hood, so any LiteLLM-compatible key works. Set them as environment variables, in a `.env` file, or store them with the `basemode keys` CLI:

```bash
# Store permanently
basemode keys set openai       # prompts for key
basemode keys set anthropic
basemode keys set openrouter

# Or set env vars directly
export OPENAI_API_KEY=sk-...
export ANTHROPIC_API_KEY=sk-ant-...
```

Keys stored via `basemode keys set` are saved to `~/.config/basemode/auth.json` and loaded automatically on every run.

Full list of supported key names:

| Provider | Env var |
|----------|---------|
| OpenAI | `OPENAI_API_KEY` |
| Anthropic | `ANTHROPIC_API_KEY` |
| Google | `GEMINI_API_KEY` |
| Groq | `GROQ_API_KEY` |
| Together | `TOGETHER_API_KEY` |
| OpenRouter | `OPENROUTER_API_KEY` |
| Moonshot | `MOONSHOT_API_KEY` |
| xAI | `XAI_API_KEY` |
| ZAI | `ZAI_API_KEY` |

## Verify install

```bash
basemode --help
basemode models --available   # lists models you have keys for
```

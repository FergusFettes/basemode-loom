# How It Works

## The core loop

basemode-loom is built around a simple loop:

1. You have a **node** — a piece of text at some point in a tree
2. You ask the LLM to generate N continuations from that node
3. Each continuation becomes a **child node**
4. You navigate to the most interesting child and repeat

This gives you a tree of generated text that you can explore non-linearly.

## Persistence

Everything is stored in a local SQLite database (default: `~/.local/share/basemode/generations.sqlite`, or `BASEMODE_DB` if set). Each session resumes exactly where you left off: the active node, which child was selected at each parent, and tree-level session config are all saved.

## Three layers

The codebase has three clean layers:

```
UI Layer (TUI / Web / CLI)
        ↓
  LoomSession  ← navigation, generation, bookmarks
        ↓
  GenerationStore ← SQLite persistence
```

UI layers only interact with `LoomSession` and never call the store directly. This makes it straightforward to add new interfaces (web frontend, REST clients, etc.) without duplicating logic.

## Text reconstruction

Each node stores **only its own text segment** — not the full text from root to node. The full text is reconstructed by walking the lineage from the target node up to the root and concatenating segments.

This keeps storage efficient and makes edit boundaries explicit: when you edit a node, only that segment changes.

## Generation settings

Model, temperature, max tokens, number of branches, context, model-plan entries, and model-name display are stored in the **root node's metadata**. When you open a tree, the session loads those settings automatically. You can override them mid-session and the new settings apply to subsequent generations without changing older nodes.

## Auto-naming

After generating from a tree that has accumulated enough text (>500 tokens in the longest branch), basemode-loom automatically asks a cheap LLM (gpt-4o-mini or claude-haiku) to generate a short slug name for the tree. This name shows up in `basemode-loom nodes` and the TUI tree picker.

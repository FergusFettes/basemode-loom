from __future__ import annotations

from basemode_loom.session import PromptEntry
from basemode_loom.tui.screens.prompt_screen import _render_entry


def test_render_entry_renders_a_plain_prompt() -> None:
    rendered = _render_entry(
        PromptEntry(
            model="model",
            strategy="system",
            prefix="Complete this.",
            messages=None,
        )
    )

    assert "strategy: system" in rendered.plain
    assert "PROMPT" in rendered.plain
    assert "Complete this." in rendered.plain


def test_render_entry_renders_role_labels_for_messages() -> None:
    rendered = _render_entry(
        PromptEntry(
            model="model",
            strategy="chat",
            prefix="",
            messages=(("system", "Rules"), ("custom", "Value")),
        )
    )

    assert "SYSTEM" in rendered.plain
    assert "CUSTOM" in rendered.plain
    assert "Rules" in rendered.plain
    assert "Value" in rendered.plain

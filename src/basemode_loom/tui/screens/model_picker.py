from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static


def _fuzzy_match(query: str, text: str) -> bool:
    if not query:
        return True
    it = iter(text.lower())
    return all(c in it for c in query.lower())


class ModelPickerScreen(ModalScreen[list[str] | None]):
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("space", "toggle_select", "Toggle", priority=True),
        Binding("enter", "submit_selection", "Apply", priority=True),
        Binding("escape", "dismiss_none", "Cancel"),
    ]

    def __init__(self, current_models: list[str]) -> None:
        super().__init__()
        self._current_models = current_models
        self._selected_models: set[str] = set(current_models)
        self._all_models: list[str] = []
        self._visible_models: list[str] = []
        self._model_to_label: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Models  j/k=move  Space=toggle  Enter=apply  Esc=cancel")
            yield Input(placeholder="filter...", id="search")
            yield OptionList(id="model-list")

    def on_mount(self) -> None:
        models: list[str]
        labels: list[str] = []
        try:
            from basemode.models import list_model_picker_entries

            entries = list_model_picker_entries(available_only=True, verified_only=True)
            if not entries:
                entries = list_model_picker_entries(available_only=True)
            models = [str(e["model"]) for e in entries]
            for e in entries:
                mark = e.get("reliability") or " "
                labels.append(f"{mark} {e['model']}")
        except Exception:
            from basemode.models import list_models

            models = list_models(available_only=True)
            labels = models[:]

        models = models or self._current_models
        if not models:
            models = ["gpt-4o-mini"]
        if not labels:
            labels = models[:]

        # Ensure existing selections remain visible even if they're missing
        # from the current provider catalog.
        label_by_model = {
            model: label for label, model in zip(labels, models, strict=False)
        }
        for model in self._current_models:
            if model not in label_by_model:
                label_by_model[model] = f"* {model}"
                models.append(model)

        # Keep currently selected models near the top in their given order.
        chosen = [m for m in self._current_models if m in models]
        if chosen:
            ordered = chosen + [m for m in models if m not in set(chosen)]
            models = ordered
            labels = [label_by_model[m] for m in models]
        else:
            labels = [label_by_model[m] for m in models]

        self._all_models = models
        self._model_to_label = {
            model: label for label, model in zip(labels, models, strict=False)
        }
        self._update_list("")
        self.query_one(OptionList).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        self._update_list(event.value)

    def _render_label(self, model: str) -> str:
        marker = "☑" if model in self._selected_models else "☐"
        label = self._model_to_label.get(model, model)
        return f"{marker} {label}"

    def _update_list(self, query: str) -> None:
        filtered = [m for m in self._all_models if _fuzzy_match(query, m)]
        opt = self.query_one(OptionList)
        old_visible = self._visible_models
        prev_idx = opt.highlighted
        prev_model = (
            old_visible[prev_idx]
            if prev_idx is not None and 0 <= prev_idx < len(old_visible)
            else None
        )
        self._visible_models = filtered
        opt.clear_options()
        for m in filtered:
            opt.add_option(self._render_label(m))
        if not filtered:
            return
        if prev_model in filtered:
            opt.highlighted = filtered.index(prev_model)
        else:
            opt.highlighted = 0

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.action_submit_selection()

    def action_dismiss_none(self) -> None:
        self.dismiss(None)

    def action_toggle_select(self) -> None:
        opt = self.query_one(OptionList)
        idx = opt.highlighted
        if idx is None or not (0 <= idx < len(self._visible_models)):
            return
        self._toggle_model(self._visible_models[idx])

    def action_cursor_down(self) -> None:
        self.query_one(OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one(OptionList).action_cursor_up()

    def _toggle_model(self, model: str) -> None:
        if model in self._selected_models:
            self._selected_models.remove(model)
        else:
            self._selected_models.add(model)
        opt = self.query_one(OptionList)
        for idx, visible in enumerate(self._visible_models):
            opt.replace_option_prompt_at_index(idx, self._render_label(visible))

    def action_submit_selection(self) -> None:
        selected = [m for m in self._all_models if m in self._selected_models]
        if not selected:
            self.notify("Select at least one model", severity="warning")
            return
        self.dismiss(selected)

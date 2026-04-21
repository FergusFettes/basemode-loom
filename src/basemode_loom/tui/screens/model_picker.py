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


class ModelPickerScreen(ModalScreen[str | None]):
    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "dismiss_none", "Cancel")]

    def __init__(self, current: str) -> None:
        super().__init__()
        self._current = current
        self._all_models: list[str] = []
        self._label_to_model: dict[str, str] = {}
        self._model_to_label: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Model  j/k=move  Enter=pick  Esc=cancel")
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

        models = models or [self._current]
        if not labels:
            labels = models[:]
        if self._current in models:
            idx = models.index(self._current)
            models.insert(0, models.pop(idx))
            labels.insert(0, labels.pop(idx))
        self._all_models = models
        self._label_to_model = {
            label: model for label, model in zip(labels, models, strict=False)
        }
        self._model_to_label = {
            model: label for label, model in zip(labels, models, strict=False)
        }
        self._update_list("")
        self.query_one("#search", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        self._update_list(event.value)

    def _update_list(self, query: str) -> None:
        filtered = [m for m in self._all_models if _fuzzy_match(query, m)]
        opt = self.query_one(OptionList)
        opt.clear_options()
        for m in filtered:
            label = self._model_to_label.get(m, m)
            opt.add_option(label)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        label = str(event.option.prompt)
        self.dismiss(self._label_to_model.get(label, label))

    def action_dismiss_none(self) -> None:
        self.dismiss(None)

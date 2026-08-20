from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static

_HELP = (
    "Models  j/k=move  Space=toggle  Enter=apply  Esc=cancel\n"
    "Filters (mix into the search box): provider:<name>  since:<10d|4w|6m|1y>"
    "  verified  all"
)

FilterKey = tuple[str | None, str | None, bool, bool]


def _fuzzy_match(query: str, text: str) -> bool:
    if not query:
        return True
    it = iter(text.lower())
    return all(c in it for c in query.lower())


def _parse_query(query: str) -> tuple[str | None, str | None, bool, bool, str]:
    """Split ``provider:``/``since:``/``verified``/``all`` tokens out of free text.

    Returns ``(provider, since, verified_only, available_only, search_terms)``.
    ``available_only`` defaults to True, matching the ``basemode models`` CLI;
    the bare word ``all`` turns it off to include models without a stored key.
    """
    provider: str | None = None
    since: str | None = None
    verified_only = False
    available_only = True
    terms: list[str] = []
    for token in query.split():
        lowered = token.lower()
        if lowered.startswith(("provider:", "p:")):
            value = token.split(":", 1)[1]
            provider = value or None
        elif lowered.startswith("since:"):
            value = token.split(":", 1)[1]
            since = value or None
        elif lowered == "verified":
            verified_only = True
        elif lowered == "all":
            available_only = False
        else:
            terms.append(token)
    return provider, since, verified_only, available_only, " ".join(terms)


class ModelPickerScreen(ModalScreen[list[str] | None]):
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("up", "cursor_up", "Up", show=False),
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
        self._filter_key: FilterKey | None = None

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(_HELP)
            yield Input(
                placeholder="filter, or provider:openai / since:6m / verified / all",
                id="search",
            )
            yield OptionList(id="model-list")
            yield Static("", id="status")

    def on_mount(self) -> None:
        self._update_list("")
        self.query_one(OptionList).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        self._update_list(event.value)

    def _render_label(self, model: str) -> str:
        marker = "☑" if model in self._selected_models else "☐"
        label = self._model_to_label.get(model, model)
        return f"{marker} {label}"

    def _load_entries(
        self,
        provider: str | None,
        since: str | None,
        verified_only: bool,
        available_only: bool,
    ) -> tuple[list[str], list[str], str | None]:
        try:
            from basemode.models import list_model_picker_entries, parse_since

            if since:
                try:
                    parse_since(since)
                except ValueError as exc:
                    return [], [], str(exc)

            entries = list_model_picker_entries(
                provider=provider,
                available_only=available_only,
                verified_only=verified_only,
                since=since,
            )
            models = [str(e["model"]) for e in entries]
            labels = [f"{e.get('reliability') or ' '} {e['model']}" for e in entries]
            return models, labels, None
        except Exception:
            try:
                from basemode.models import list_models

                models = list_models(available_only=available_only)
                return models, models[:], None
            except Exception as exc:
                return [], [], str(exc)

    def _update_list(self, query: str) -> None:
        provider, since, verified_only, available_only, search_terms = _parse_query(
            query
        )
        key: FilterKey = (provider, since, verified_only, available_only)

        if key != self._filter_key:
            models, labels, error = self._load_entries(
                provider, since, verified_only, available_only
            )
            status = self.query_one("#status", Static)
            if error:
                status.update(f"[red]{error}[/red]")
                return
            self._filter_key = key

            models = models or self._current_models
            if not models:
                models = ["gpt-4o-mini"]
            if not labels:
                labels = models[:]

            # Ensure existing selections remain visible even if they're missing
            # from the current filtered catalog.
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

            self._all_models = models
            self._model_to_label = {model: label_by_model[model] for model in models}

        filtered = [m for m in self._all_models if _fuzzy_match(search_terms, m)]
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
        if filtered:
            opt.highlighted = (
                filtered.index(prev_model) if prev_model in filtered else 0
            )

        status = self.query_one("#status", Static)
        bits = [f"{len(filtered)} models"]
        if provider:
            bits.append(f"provider={provider}")
        if since:
            bits.append(f"since={since}")
        if verified_only:
            bits.append("verified")
        if not available_only:
            bits.append("all (incl. unavailable)")
        status.update(" · ".join(bits))

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

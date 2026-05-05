from textual.app import App

from ..config import DEFAULT_CONFIG, Config
from ..logging_utils import configure_logging
from ..session import LoomSession
from .screens.loom import LoomScreen


class BasemodeApp(App[None]):
    """Basemode loom TUI."""

    TITLE = "basemode-loom"
    CSS_PATH = "app.tcss"

    def __init__(self, session: LoomSession, config: Config = DEFAULT_CONFIG) -> None:
        super().__init__()
        configure_logging("tui")
        self.session = session
        self.config = config

    def on_mount(self) -> None:
        self.push_screen(LoomScreen(self.session, self.config))

    def on_unmount(self) -> None:
        self.session.save()

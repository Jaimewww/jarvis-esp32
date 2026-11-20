# Pyright: "textual" is installed from host/requirements.txt, so a checker running
# without it cannot resolve the import; that is not a defect in this module.
# pyright: reportMissingImports=false

"""The ``/login`` panel: sign in to an AI account from the window, in both views.

It sits between the views and the input, a rounded card in the theme of the window, and
is drawn only from ``HostModel.login``: the accounts the daemon lists, the steps of a
sign-in (an address to open, a device code, progress), the open prompt and the outcome.
What the person types is never part of it: a ``secret`` prompt hides the input itself
(``JarvisApp``), and no answer is stored, so the panel cannot show one.

The panel never takes more than ``MAX_ROWS`` rows (nor half the window); when the steps
do not fit, the oldest go first, and the address and the prompt stay.
"""

from __future__ import annotations

import textwrap
from typing import Final

from rich.text import Text
from textual.geometry import Size
from textual.widget import Widget

from .client import AuthFlowEvent, AuthProvider
from .model import HostModel, Login, LoginStage
from .theme import DEFAULT_DARK, THEMES, Theme, to_text
from .userview import bottom_border, side, top_border

Seg = tuple[str, str]

#: The most rows the panel takes, borders included.
MAX_ROWS: Final = 12
METHOD_NAMES: Final = {"oauth": "subscription", "api_key": "API key"}


def method_label(provider: AuthProvider, method: str) -> str:
    if method == "oauth" and provider.oauth_name:
        return f"subscription ({provider.oauth_name})"
    return METHOD_NAMES.get(method, method)


def _wrap(text: str, room: int, cls: str, first: str = "", indent: str = "") -> list[list[Seg]]:
    lines = textwrap.wrap(text, max(1, room - len(first))) or [""]
    return [[(first if i == 0 else indent, cls), (line, cls)] for i, line in enumerate(lines)]


def _hard(text: str, room: int, cls: str) -> list[list[Seg]]:
    """An address split at the edge, never at a word, so a copy joins it whole."""

    room = max(1, room)
    return [[(text[i : i + room], cls)] for i in range(0, len(text), room)] or [[("", cls)]]


def _numbered(n: int, label: str, room: int, cls: str = "t1", tail: list[Seg] | None = None) -> list[Seg]:
    mark = f"{n:>2} "
    segs: list[Seg] = [(mark, "ac b"), (label, cls)]
    for text, c in tail or []:
        segs.append((text, c))
    used = sum(len(t) for t, _ in segs)
    if used > room:  # cut the tail first, then the label
        out: list[Seg] = []
        left = room
        for text, c in segs:
            out.append((text[:left], c))
            left -= len(text[:left])
            if left <= 0:
                break
        return out
    return segs


def provider_rows(login: Login, room: int) -> list[list[Seg]]:
    if not login.providers:
        text = "Gentle Dot lists no accounts to sign in to." if login.listed else "Asking Gentle Dot for the accounts…"
        return [[(text, "mu")]]
    name_w = min(24, max(len(p.name) for p in login.providers) + 2)
    rows = [[("Choose an account: type its number and press enter.", "t2")]]
    for n, provider in enumerate(login.providers, 1):
        state: Seg = ("connected", "ok") if provider.configured else ("not connected", "mu")
        ways = " · ".join(method_label(provider, m) for m in provider.methods)
        tail: list[Seg] = [(" " * (name_w - len(provider.name[:name_w])), ""), state,
                           (" " * (16 - len(state[0])), ""), (ways, "t2")]
        rows.append(_numbered(n, provider.name[:name_w], room, "t1 b", tail))
    return rows


def method_rows(login: Login, room: int) -> list[list[Seg]]:
    provider = login.provider
    if provider is None:
        return []
    rows = [[(f"Sign in to {provider.name} how? Type its number.", "t2")]]
    for n, method in enumerate(provider.methods, 1):
        rows.append(_numbered(n, method_label(provider, method), room))
    return rows


def step_rows(step: AuthFlowEvent, room: int) -> list[list[Seg]]:
    if step.kind == "auth_url":
        lead = _wrap(step.instructions or "Open this address to sign in:", room, "t2")
        return lead + _hard(step.url, room, "ac")
    if step.kind == "device_code":
        rows: list[list[Seg]] = [[("Code  ", "mu"), (step.user_code, "t1 b")]]
        rows += [[("At    ", "mu"), (line[0][0], "ac")] for line in _hard(step.verification_uri, room - 6, "ac")]
        if step.expires_in_seconds:
            minutes = max(1, round(step.expires_in_seconds / 60))
            rows.append([(f"expires in {minutes} min", "mu")])
        return rows
    cls = "t2" if step.kind == "info" else "mu"
    rows = _wrap(step.message, room, cls)
    for link in step.links:
        label = f"{link.label}: " if link.label else ""
        rows += [[(label, "t2")] + line for line in _hard(link.url, room - len(label), "ac")]
    return rows


def prompt_rows(login: Login, room: int) -> list[list[Seg]]:
    prompt = login.prompt
    if prompt is None:
        name = login.provider.name if login.provider else "the account"
        text = "Cancelling…" if login.cancel_pending or login.cancelling else f"Signing in to {name}…"
        return [[(text, "lv")]]
    rows = _wrap(prompt.message, room, "t1 b")
    if prompt.kind == "select":
        rows += [_numbered(n, option.label, room, tail=[(f"  {option.description}", "mu")] if option.description else None)
                 for n, option in enumerate(prompt.options, 1)]
    elif prompt.kind == "secret":
        rows.append([("What you type stays hidden and is not kept.", "mu")])
    if prompt.optional:
        rows.append([("Optional: press enter with nothing to skip.", "mu")])
    return rows


def login_rows(login: Login, w: int, max_rows: int = MAX_ROWS) -> list[list[Seg]]:
    """The panel, ``w`` cells wide and at most ``max_rows`` rows tall (borders included)."""

    if not login.open or w < 8 or max_rows < 3:
        return []
    room = w - 4
    head: list[list[Seg]] = []
    if login.result is not None:
        ok, text = login.result
        head = _wrap(text, room, "ok" if ok else "er", "✓ " if ok else "✗ ", "  ")
    steps: list[list[Seg]] = []
    if login.stage is LoginStage.RUNNING:
        for step in login.steps:
            steps += step_rows(step, room)
        body = prompt_rows(login, room)
    elif login.stage is LoginStage.METHOD:
        body = method_rows(login, room)
    else:
        body = provider_rows(login, room)
    if login.hint:
        body = body + [[(login.hint, "wa")]]
    inner = max_rows - 2
    # The oldest steps go first, then the end of the body; an address is never cut alone.
    if len(head) + len(steps) + len(body) > inner:
        steps = steps[len(head) + len(steps) + len(body) - inner :]
    rows = (head + steps + body)[:inner]
    title = [("Sign in", "t2 b")]
    if login.provider is not None and login.stage is not LoginStage.PROVIDERS:
        title += [(f"  {login.provider.name}", "mu")]
    out = [top_border(title, w)]
    out += [side([(" ", "")] + row, w) for row in rows]
    out.append(bottom_border(w))
    return out


class LoginPanel(Widget):
    """The ``/login`` card above the input; hidden while the panel is closed."""

    DEFAULT_CSS = """
    LoginPanel {
        height: auto;
        background: $jarvis-background;
    }
    """

    def __init__(self, model: HostModel, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.model = model
        self.palette: Theme = THEMES[DEFAULT_DARK]

    def budget(self) -> int:
        """Rows the panel may take: ``MAX_ROWS``, and never more than half the window."""

        return max(3, min(MAX_ROWS, self.app.size.height // 2))

    def get_content_height(self, container: Size, viewport: Size, width: int) -> int:
        return len(login_rows(self.model.login, width, self.budget()))

    def render(self) -> Text:
        return to_text(login_rows(self.model.login, self.size.width, self.budget()), self.palette)

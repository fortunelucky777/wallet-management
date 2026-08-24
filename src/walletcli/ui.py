"""Shared Rich-based UI helpers: banner, panels, prompts, tables, spinners."""

from __future__ import annotations

import os
import sys
from decimal import Decimal, InvalidOperation

from rich.align import Align
from rich.columns import Columns
from rich.console import Console, Group
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

from .config import ENV_PASSPHRASE

theme = Theme(
    {
        "brand": "bold magenta",
        "accent": "bold cyan",
        "ok": "bold green",
        "warn": "bold yellow",
        "err": "bold red",
        "dim": "grey58",
        "addr": "bold bright_cyan",
        "amount": "bold bright_white",
    }
)

console = Console(theme=theme)

BANNER_LINES = [
    "██╗    ██╗ █████╗ ██╗     ██╗     ███████╗████████╗",
    "██║    ██║██╔══██╗██║     ██║     ██╔════╝╚══██╔══╝",
    "██║ █╗ ██║███████║██║     ██║     █████╗     ██║   ",
    "██║███╗██║██╔══██║██║     ██║     ██╔══╝     ██║   ",
    "╚███╔███╔╝██║  ██║███████╗███████╗███████╗   ██║   ",
    " ╚══╝╚══╝ ╚═╝  ╚═╝╚══════╝╚══════╝╚══════╝   ╚═╝   ",
]

_GRADIENT = ["magenta", "medium_orchid", "medium_purple", "slate_blue1", "dodger_blue1", "cyan"]


def banner(subtitle: str = "self-custody wallet · Ethereum ✦ Tron") -> None:
    art = Text()
    for line, color in zip(BANNER_LINES, _GRADIENT):
        art.append(line + "\n", style=color)
    art.append(subtitle, style="dim")
    console.print(Align.center(art))
    console.print()


def success(message: str, title: str = "Success") -> None:
    console.print(Panel(message, title=f"[ok]✔ {title}[/ok]", border_style="green", expand=False))


def error(message: str, title: str = "Error") -> None:
    console.print(Panel(message, title=f"[err]✘ {title}[/err]", border_style="red", expand=False))


def warning(message: str, title: str = "Warning") -> None:
    console.print(Panel(message, title=f"[warn]⚠ {title}[/warn]", border_style="yellow", expand=False))


def info(message: str) -> None:
    console.print(f"[accent]›[/accent] {message}")


def rule(title: str) -> None:
    console.rule(f"[brand]{title}[/brand]", style="magenta")


def clear_screen() -> None:
    """Clear the visible screen *and* the scrollback buffer.

    ``console.clear()`` emits ESC[2J (viewport only), so the recovery phrase
    would still be recoverable by scrolling up. ESC[3J additionally purges the
    scrollback buffer in xterm-compatible terminals.
    """
    console.clear()
    if sys.stdout.isatty():
        sys.stdout.write("\033[3J")
        sys.stdout.flush()


def require_tty(hint: str) -> None:
    if not sys.stdin.isatty():
        error(
            f"This step needs interactive input, but stdin is not a terminal.\n{hint}",
            title="Not a terminal",
        )
        raise SystemExit(2)


# ----------------------------------------------------------------- prompts

def ask(prompt: str, default: str | None = None) -> str:
    return Prompt.ask(f"[accent]?[/accent] {prompt}", default=default, console=console)


def confirm(prompt: str, default: bool = False) -> bool:
    return Confirm.ask(f"[accent]?[/accent] {prompt}", default=default, console=console)


def ask_passphrase(confirm_new: bool = False) -> str:
    """Prompt for the vault passphrase (hidden input).

    ``WALLETCLI_PASSPHRASE`` overrides the prompt — automation/testing only:
    environment variables can leak into shell history and process listings.
    """
    env = os.environ.get(ENV_PASSPHRASE)
    if env is not None:
        # When creating a new secret, the env value must still clear the bar the
        # interactive path enforces — otherwise an empty/typo'd variable would
        # silently seal the vault under a trivially crackable passphrase.
        if confirm_new and len(env) < 8:
            error(
                f"{ENV_PASSPHRASE} must be at least 8 characters to protect a new wallet "
                f"(got {len(env)}). Refusing to create a wallet with a weak passphrase.",
                title="Weak passphrase",
            )
            raise SystemExit(3)
        return env
    require_tty(f"Set {ENV_PASSPHRASE} for non-interactive use (automation only).")
    while True:
        first = Prompt.ask("[accent]🔑[/accent] Passphrase", password=True, console=console)
        if not confirm_new:
            return first
        if len(first) < 8:
            warning("Use at least 8 characters — this passphrase is all that protects your keys.")
            continue
        second = Prompt.ask("[accent]🔑[/accent] Confirm passphrase", password=True, console=console)
        if first == second:
            return first
        warning("Passphrases did not match — try again.")


def ask_hidden(prompt: str) -> str:
    """Prompt for sensitive input with echo disabled (nothing shown while typing)."""
    require_tty("Hidden input needs a terminal.")
    return Prompt.ask(f"[accent]🔒[/accent] {prompt}", password=True, console=console)


def ask_amount(prompt: str, max_value: Decimal | None = None) -> Decimal:
    while True:
        raw = ask(prompt).strip()
        try:
            value = parse_amount(raw, max_value=max_value)
        except ValueError as exc:
            warning(str(exc))
            continue
        return value


def parse_amount(raw: str, max_value: Decimal | None = None) -> Decimal:
    """Parse a user/flag-supplied amount, raising ValueError with a clear message.

    A decimal comma is rejected rather than silently stripped: treating ``1,5``
    as a thousands separator would turn 1.5 into 15 and send 10x the intended
    amount. NaN/Infinity are rejected too (``Decimal('nan') <= 0`` would raise
    an uncaught ``InvalidOperation`` deep in the send flow).
    """
    text = raw.strip()
    if "," in text:
        raise ValueError("Use a dot for decimals (e.g. 12.5), not a comma.")
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise ValueError(f"'{raw}' is not a number — enter something like 12.5")
    if not value.is_finite():
        raise ValueError(f"'{raw}' is not a valid amount.")
    if value <= 0:
        raise ValueError("Amount must be greater than zero.")
    if max_value is not None and value > max_value:
        raise ValueError(f"Amount exceeds available balance ({max_value}).")
    return value


def choose(title: str, options: list[tuple[str, str]], default: str | None = None) -> str:
    """Numbered menu. ``options`` is a list of (key, label); returns the key."""
    console.print(f"[accent]?[/accent] {title}")
    for i, (_, label) in enumerate(options, 1):
        console.print(f"    [brand]{i}[/brand]  {label}")
    keys = [k for k, _ in options]
    default_num = str(keys.index(default) + 1) if default in keys else None
    while True:
        raw = Prompt.ask(
            "  [accent]›[/accent] choice",
            choices=[str(i) for i in range(1, len(options) + 1)],
            default=default_num,
            show_choices=False,
            console=console,
        )
        try:
            return keys[int(raw) - 1]
        except (ValueError, IndexError):
            warning(f"Pick a number between 1 and {len(options)}.")


# ------------------------------------------------------------------ panels

def mnemonic_panel(mnemonic: str) -> None:
    words = mnemonic.split()
    cells = [
        Text.assemble((f"{i:>2} ", "dim"), (w, "bold bright_white"))
        for i, w in enumerate(words, 1)
    ]
    grid = Columns(cells, equal=True, column_first=True, padding=(0, 3))
    body = Group(
        Text("Write these words down on paper, in order.", style="warn"),
        Text("Anyone with these words controls your funds. Never share or photograph them.\n", style="dim"),
        grid,
    )
    console.print(
        Panel(body, title="[brand]🔐 Recovery phrase[/brand]", border_style="magenta", padding=(1, 2))
    )


def addresses_panel(name: str, eth_address: str, tron_address: str) -> None:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="dim", justify="right")
    table.add_column(style="addr")
    table.add_row("Ethereum", eth_address)
    table.add_row("Tron", tron_address)
    console.print(
        Panel(table, title=f"[accent]👛 {name}[/accent]", border_style="cyan", expand=False)
    )


def short_addr(addr: str) -> str:
    return addr if len(addr) <= 16 else f"{addr[:8]}…{addr[-6:]}"


def qr(data: str, caption: str) -> None:
    import io

    import qrcode

    code = qrcode.QRCode(border=1, box_size=1)
    code.add_data(data)
    buf = io.StringIO()
    code.print_ascii(out=buf, invert=True)
    console.print(Panel(buf.getvalue().rstrip("\n"), title=f"[accent]{caption}[/accent]",
                        border_style="cyan", expand=False))


def spinner(message: str):
    return console.status(f"[accent]{message}[/accent]", spinner="dots")

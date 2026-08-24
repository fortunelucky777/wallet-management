"""walletcli — command-line interface.

Every command works two ways:
* **Interactive** (recommended): run it with no flags and follow the prompts.
* **Scripted**: pass flags (and ``WALLETCLI_PASSPHRASE`` for automation).
"""

from __future__ import annotations

import functools
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, InvalidOperation

import typer
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import __version__, ui
from .addressbook import AddressBook, AddressBookError
from .assets import ASSETS, CHAIN_LABEL, ETHEREUM, TRON, Asset, asset_choices, get_asset
from .chains.base import ChainError, FeeEstimate, TxResult
from .chains.ethereum import EthereumChain
from .chains.tron import TronChain
from .config import Config, config_path, vault_path, wallet_home
from .derivation import derive_keyring, generate_mnemonic, normalize_mnemonic, validate_mnemonic
from .swap import SwapClient, SwapError
from .vault import Vault, VaultError, WrongPassphrase

app = typer.Typer(
    name="wallet",
    help="🪙 A beautiful, secure, self-custody wallet for Ethereum & Tron.",
    add_completion=True,
    pretty_exceptions_enable=False,  # tracebacks could leak secrets from locals
    no_args_is_help=False,
)
config_app = typer.Typer(help="View or change configuration (RPC endpoints, API keys, fees).")
app.add_typer(config_app, name="config")
address_app = typer.Typer(help="Save recipient addresses under easy aliases for 'wallet send'.")
app.add_typer(address_app, name="address")

NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,32}$")


def guard(fn):
    """Convert known errors into pretty panels + clean exit codes."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except WrongPassphrase as exc:
            ui.error(str(exc), title="Locked")
            raise typer.Exit(3)
        except (VaultError, ChainError, SwapError, AddressBookError) as exc:
            ui.error(str(exc))
            raise typer.Exit(1)
        except (KeyboardInterrupt, EOFError):
            ui.console.print()
            ui.info("Cancelled — nothing was sent or saved.")
            raise typer.Exit(130)

    return wrapper


# ------------------------------------------------------------------ helpers

def _eth(cfg: Config) -> EthereumChain:
    return EthereumChain(cfg.eth_rpc_url)


def _tron(cfg: Config) -> TronChain:
    return TronChain(cfg.tron_api_url, cfg.tron_api_key, cfg.tron_fee_limit_trx)


def fmt(amount: Decimal, max_places: int = 8) -> str:
    q = amount.quantize(Decimal(1)) if amount == amount.to_integral_value() \
        else amount.normalize()
    text = f"{q:,f}"
    if "." in text:
        whole, frac = text.split(".")
        text = f"{whole}.{frac[:max_places]}".rstrip(".")
    return text


def pick_wallet(vault: Vault, name: str | None, prompt: str = "Which wallet?") -> str:
    if name:
        vault.info(name)  # raises with a helpful message if missing
        return name
    names = vault.names()
    if not names:
        raise VaultError("No wallets registered yet. Create one with:  wallet register")
    if len(names) == 1:
        ui.info(f"Using wallet [accent]{names[0]}[/accent] (the only one registered).")
        return names[0]
    ui.require_tty("Pass --wallet <name> in non-interactive mode.")
    infos = {i.name: i for i in vault.list_info()}
    options = [
        (n, f"[bold]{n}[/bold]  [dim]{ui.short_addr(infos[n].eth_address)} · "
            f"{ui.short_addr(infos[n].tron_address)}[/dim]")
        for n in names
    ]
    return ui.choose(prompt, options)


def pick_asset(key: str | None, prompt: str = "Which asset?",
               allowed: list[Asset] | None = None) -> Asset:
    choices = allowed or asset_choices()
    if key:
        asset = get_asset(key)
        if asset not in choices:
            valid = ", ".join(a.key for a in choices)
            raise VaultError(f"Asset '{key}' is not valid here. Choose one of: {valid}")
        return asset
    ui.require_tty("Pass --asset <key> in non-interactive mode "
                   f"(one of: {', '.join(a.key for a in choices)}).")
    return get_asset(ui.choose(prompt, [(a.key, a.label) for a in choices]))


def chain_for(cfg: Config, asset: Asset):
    return _eth(cfg) if asset.chain == ETHEREUM else _tron(cfg)


def wallet_address_on(info, chain: str) -> str:
    return info.eth_address if chain == ETHEREUM else info.tron_address


def unlock_keyring(vault: Vault, name: str):
    secret = vault.unlock(name, ui.ask_passphrase())
    with ui.spinner("Deriving keys…"):
        keyring = derive_keyring(secret["mnemonic"], secret.get("bip39_passphrase", ""))
    info = vault.info(name)
    if keyring.eth_address.lower() != info.eth_address.lower():
        raise VaultError(
            "Derived address does not match the stored one — the vault entry is inconsistent."
        )
    return keyring


def fetch_balance(cfg: Config, asset: Asset, address: str) -> Decimal:
    chain = chain_for(cfg, asset)
    if asset.is_native:
        return chain.native_balance(address)
    return chain.token_balance(address, asset.contract, asset.decimals)


def tx_success_panel(asset: Asset, amount: Decimal, to: str, result: TxResult) -> None:
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim", justify="right")
    grid.add_column()
    grid.add_row("Sent", f"[amount]{fmt(amount)} {asset.symbol}[/amount]  [dim]({asset.label})[/dim]")
    grid.add_row("To", f"[addr]{to}[/addr]")
    grid.add_row("Tx hash", result.txid)
    grid.add_row("Explorer", f"[link={result.explorer_url}]{result.explorer_url}[/link]")
    ui.console.print(Panel(grid, title="[ok]✔ Transaction broadcast[/ok]",
                           border_style="green", expand=False))


# ----------------------------------------------------------------- overview

@app.callback(invoke_without_command=True)
def default(ctx: typer.Context,
            version: bool = typer.Option(False, "--version", "-V",
                                         help="Print version and exit.", is_eager=True)):
    if version:
        ui.console.print(f"walletcli [brand]{__version__}[/brand]")
        raise typer.Exit()
    if ctx.invoked_subcommand is not None:
        return
    ui.banner()
    try:
        count = len(Vault().names())
        status = (f"[ok]{count}[/ok] wallet(s) in [dim]{vault_path()}[/dim]"
                  if count else "[warn]no wallets yet[/warn] — start with [accent]wallet register[/accent]")
    except VaultError as exc:
        status = f"[err]{exc}[/err]"
    ui.console.print(Panel(status, title="[accent]vault[/accent]", border_style="cyan", expand=False))

    table = Table(border_style="grey37", header_style="brand", pad_edge=True, expand=False)
    table.add_column("Command", style="accent", no_wrap=True)
    table.add_column("What it does")
    rows = [
        ("wallet register", "Create or import a wallet (mnemonic + passphrase)"),
        ("wallet list", "List registered wallets"),
        ("wallet show <name>", "Show a wallet's receive addresses (with QR codes)"),
        ("wallet balance", "Check ETH, TRX, USDT & USDC balances"),
        ("wallet send", "Send ETH, TRX, or USDT/USDC (ERC20 & TRC20)"),
        ("wallet swap", "Swap between ERC20 ⇄ TRC20 (e.g. USDT-ERC20 → USDT-TRC20)"),
        ("wallet swap-status <id>", "Track a swap order"),
        ("wallet address add/list/remove", "Address book: save recipients under aliases"),
        ("wallet export <name>", "Reveal a wallet's recovery phrase"),
        ("wallet remove <name>", "Remove a wallet from the vault"),
        ("wallet config show", "View endpoints, API keys and fee settings"),
        ("wallet doctor", "Check connectivity & setup health"),
    ]
    for cmd, desc in rows:
        table.add_row(cmd, desc)
    ui.console.print(table)
    ui.console.print("[dim]Tip: every command is interactive — run it without flags "
                     "and follow the prompts. Add --help to any command for details.[/dim]")


# ----------------------------------------------------------------- register

@app.command()
@guard
def register(
    name: str = typer.Option(None, "--name", "-n", help="Wallet name."),
    words: int = typer.Option(None, "--words", help="Mnemonic length for new wallets: 12/15/18/21/24."),
    mnemonic_opt: str = typer.Option(None, "--mnemonic",
                                     help="Import this BIP39 phrase (careful: visible in shell history)."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the written-down verification quiz."),
):
    """Create a new wallet (or import one) and store it encrypted in the vault."""
    ui.banner("register a wallet")
    vault = Vault()

    if name is None:
        ui.require_tty("Pass --name in non-interactive mode.")
        name = ui.ask("Wallet name (e.g. [dim]main[/dim], [dim]savings[/dim])")
    name = name.strip()
    if not NAME_RE.match(name):
        raise VaultError("Names can use letters, digits, dot, dash and underscore (max 32 chars).")
    if vault.exists(name):
        raise VaultError(f"A wallet named '{name}' already exists — pick another name.")

    is_new = mnemonic_opt is None
    if is_new and sys.stdin.isatty() and words is None:
        mode = ui.choose("Create a brand-new wallet, or import an existing phrase?", [
            ("new", "Create new — generate a fresh recovery phrase [dim](recommended)[/dim]"),
            ("import", "Import — I already have a 12–24 word phrase"),
        ], default="new")
        is_new = mode == "new"

    if is_new:
        if words is None:
            if sys.stdin.isatty():
                words = int(ui.choose("Recovery phrase length?", [
                    ("24", "24 words — strongest [dim](recommended)[/dim]"),
                    ("12", "12 words — standard, fits on one line"),
                ], default="24"))
            else:
                words = 24
        mnemonic = generate_mnemonic(words)
        ui.mnemonic_panel(mnemonic)
        if not yes:
            ui.require_tty("Pass --yes to skip the backup quiz in non-interactive mode.")
            ui.info("Quick check that the backup is written down:")
            word_list = mnemonic.split()
            for pos in sorted(random.sample(range(1, len(word_list) + 1), 2)):
                while ui.ask(f"Word #{pos}").strip().lower() != word_list[pos - 1]:
                    ui.warning(f"That's not word #{pos} — check your paper backup.")
    else:
        if mnemonic_opt:
            mnemonic = normalize_mnemonic(mnemonic_opt)
            if not validate_mnemonic(mnemonic):
                raise VaultError(
                    "That is not a valid BIP39 phrase (typo, wrong word count, or bad checksum)."
                )
        else:
            ui.require_tty("Pass --mnemonic in non-interactive mode.")
            ui.info("Your phrase will be [bold]hidden[/bold] while you type — "
                    "separate the words with spaces.")
            while True:
                mnemonic = normalize_mnemonic(
                    ui.ask_hidden("Paste or type your recovery phrase (12–24 words)")
                )
                if validate_mnemonic(mnemonic):
                    count = len(mnemonic.split())
                    ui.info(f"Phrase accepted — {count} words, checksum OK.")
                    break
                count = len(mnemonic.split())
                ui.warning(
                    f"Not a valid BIP39 phrase (read {count} word(s)). Check for typos, "
                    "missing words, or extra separators like commas — then try again."
                )

    bip39_pass = ""
    if sys.stdin.isatty() and ui.confirm(
        "Advanced: add a BIP39 passphrase (a '25th word' — changes all addresses)?", default=False
    ):
        ui.warning(
            "The BIP39 passphrase is part of your backup. Lose it and the recovery "
            "phrase alone will NOT restore these addresses."
        )
        bip39_pass = ui.ask_passphrase(confirm_new=True)

    ui.info("Choose the [bold]encryption passphrase[/bold] that locks this wallet on disk.")
    passphrase = ui.ask_passphrase(confirm_new=True)

    with ui.spinner("Deriving Ethereum & Tron keys…"):
        keyring = derive_keyring(mnemonic, bip39_pass)
    with ui.spinner("Encrypting with Argon2id + AES-256-GCM…"):
        vault.add(name, mnemonic=mnemonic, bip39_passphrase=bip39_pass,
                  passphrase=passphrase, eth_address=keyring.eth_address,
                  tron_address=keyring.tron_address)

    if is_new and sys.stdin.isatty() and not yes:
        ui.console.clear()
        ui.info("Screen cleared so the recovery phrase is no longer visible.")
    ui.success(f"Wallet [bold]{name}[/bold] registered and encrypted in [dim]{vault.path}[/dim]")
    ui.addresses_panel(name, keyring.eth_address, keyring.tron_address)
    ui.console.print("[dim]Next:[/dim] [accent]wallet balance[/accent] · "
                     "[accent]wallet show " + name + " --qr[/accent] · "
                     "[accent]wallet send[/accent]")


# --------------------------------------------------------------------- list

@app.command("list")
@guard
def list_wallets():
    """List all registered wallets."""
    vault = Vault()
    infos = vault.list_info()
    if not infos:
        ui.warning("No wallets yet.\nCreate your first one with:  [accent]wallet register[/accent]",
                   title="Empty vault")
        return
    table = Table(title=f"👛 {len(infos)} wallet(s)", border_style="grey37",
                  header_style="brand", title_style="accent")
    table.add_column("Name", style="bold")
    table.add_column("Ethereum", style="addr")
    table.add_column("Tron", style="addr")
    table.add_column("Created", style="dim")
    for i in infos:
        table.add_row(i.name, ui.short_addr(i.eth_address), ui.short_addr(i.tron_address),
                      i.created_at[:10])
    ui.console.print(table)
    ui.console.print(f"[dim]Vault: {vault.path}[/dim]")


@app.command()
@guard
def show(
    name: str = typer.Argument(None, help="Wallet name."),
    qr: bool = typer.Option(False, "--qr", help="Also print QR codes for both addresses."),
):
    """Show a wallet's receive addresses (optionally as QR codes)."""
    vault = Vault()
    wallet_name = pick_wallet(vault, name)
    info = vault.info(wallet_name)
    ui.addresses_panel(info.name, info.eth_address, info.tron_address)
    ui.console.print("[dim]  ETH / USDT-ERC20 / USDC-ERC20 → Ethereum address\n"
                     "  TRX / USDT-TRC20 / USDC-TRC20 → Tron address[/dim]")
    if qr:
        ui.qr(info.eth_address, f"Ethereum · {ui.short_addr(info.eth_address)}")
        ui.qr(info.tron_address, f"Tron · {ui.short_addr(info.tron_address)}")


# ------------------------------------------------------------------ balance

@app.command()
@guard
def balance(
    name: str = typer.Argument(None, help="Wallet name."),
    asset_key: str = typer.Option(None, "--asset", "-a",
                                  help="Only this asset (eth, usdt-erc20, usdc-erc20, trx, usdt-trc20, usdc-trc20)."),
):
    """Check balances across all supported assets."""
    cfg = Config.load()
    vault = Vault()
    wallet_name = pick_wallet(vault, name)
    info = vault.info(wallet_name)
    assets = [get_asset(asset_key)] if asset_key else asset_choices()

    results: dict[str, Decimal | Exception] = {}

    def fetch_chain(chain_key: str):
        for asset in assets:
            if asset.chain != chain_key:
                continue
            address = wallet_address_on(info, asset.chain)
            try:
                results[asset.key] = fetch_balance(cfg, asset, address)
            except Exception as exc:  # keep other rows alive
                results[asset.key] = exc

    with ui.spinner(f"Fetching balances for [bold]{wallet_name}[/bold]…"):
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(fetch_chain, [ETHEREUM, TRON]))

    table = Table(title=f"💰 {wallet_name}", border_style="grey37",
                  header_style="brand", title_style="accent")
    table.add_column("Asset")
    table.add_column("Network", style="dim")
    table.add_column("Balance", justify="right", style="amount")
    table.add_column("Address", style="dim")
    for asset in assets:
        outcome = results.get(asset.key)
        if isinstance(outcome, Exception):
            shown = Text("unavailable", style="err")
        else:
            shown = Text(f"{fmt(outcome)} {asset.symbol}")
            if outcome == 0:
                shown.stylize("dim")
        table.add_row(asset.label.split(" · ")[0] + f" [dim]{asset.key}[/dim]",
                      "Ethereum" if asset.chain == ETHEREUM else "Tron",
                      shown, ui.short_addr(wallet_address_on(info, asset.chain)))
    ui.console.print(table)
    failures = [k for k, v in results.items() if isinstance(v, Exception)]
    if failures:
        first = results[failures[0]]
        ui.warning(f"Some balances were unavailable ({', '.join(failures)}).\nFirst error: {first}")


# --------------------------------------------------------------------- send

@app.command()
@guard
def send(
    wallet: str = typer.Option(None, "--wallet", "-w", help="Wallet to send from."),
    asset_key: str = typer.Option(None, "--asset", "-a", help="Asset key (e.g. usdt-trc20)."),
    to: str = typer.Option(None, "--to", "-t", help="Recipient address."),
    amount_opt: str = typer.Option(None, "--amount", "-m", help="Amount to send."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
):
    """Send ETH, TRX, USDT or USDC (ERC20 & TRC20) to any address."""
    ui.rule("send")
    cfg = Config.load()
    vault = Vault()

    wallet_name = pick_wallet(vault, wallet, "Send from which wallet?")
    info = vault.info(wallet_name)
    asset = pick_asset(asset_key, "Which asset do you want to send?")
    chain = chain_for(cfg, asset)
    sender = wallet_address_on(info, asset.chain)

    book = AddressBook()
    if to is None:
        ui.require_tty("Pass --to <address or alias> in non-interactive mode.")
        contacts = book.for_chain(asset.chain)
        if contacts:
            picked = ui.choose("Send to", [("__manual__", "Type an address")] + [
                (c.alias, f"[bold]{c.alias}[/bold]  [dim]{ui.short_addr(c.address)}[/dim]")
                for c in contacts
            ], default="__manual__")
            to = (ui.ask(f"Recipient [accent]{asset.label}[/accent] address").strip()
                  if picked == "__manual__" else picked)
        else:
            to = ui.ask(f"Recipient [accent]{asset.label}[/accent] address").strip()
    contact = book.get(to)
    if contact:
        if contact.chain != asset.chain:
            raise AddressBookError(
                f"Alias '{contact.alias}' is a {CHAIN_LABEL[contact.chain]} address, but "
                f"{asset.label} is sent on {CHAIN_LABEL[asset.chain]}."
            )
        ui.info(f"Alias [bold]{contact.alias}[/bold] → [addr]{contact.address}[/addr]")
        to = contact.address
    to = chain.validate_address(to)
    if to.lower() == sender.lower():
        ui.warning("Recipient is this wallet's own address.")

    with ui.spinner("Checking balance…"):
        available = fetch_balance(cfg, asset, sender)
    ui.info(f"Available: [amount]{fmt(available)} {asset.symbol}[/amount]")
    if available <= 0:
        raise ChainError(f"This wallet has no {asset.symbol} ({asset.label}) to send.")

    if amount_opt is None:
        ui.require_tty("Pass --amount in non-interactive mode.")
        amount = ui.ask_amount(f"Amount of {asset.symbol} to send", max_value=available)
    else:
        try:
            amount = Decimal(amount_opt)
        except InvalidOperation:
            raise VaultError(f"--amount '{amount_opt}' is not a number.")
        if amount <= 0 or amount > available:
            raise ChainError(f"Amount must be between 0 and {fmt(available)} {asset.symbol}.")

    # Fee estimate + native-balance sanity checks.
    with ui.spinner("Estimating network fee…"):
        if asset.chain == ETHEREUM:
            if asset.is_native:
                fee = chain.estimate_native_fee()
            else:
                from .chains.base import to_base_units
                fee, _ = chain.estimate_token_fee(sender, asset.contract, to,
                                                  to_base_units(amount, asset.decimals))
            gas_balance = _eth(cfg).native_balance(sender)
            gas_symbol = "ETH"
        else:
            fee = chain.estimate_native_fee() if asset.is_native else chain.estimate_token_fee()
            gas_balance = _tron(cfg).native_balance(sender)
            gas_symbol = "TRX"

    if asset.is_native and amount + fee.amount > available and not fee.is_upper_bound:
        maximum = available - fee.amount
        raise ChainError(
            f"{fmt(amount)} {asset.symbol} + {fmt(fee.amount)} fee exceeds the balance. "
            f"Maximum sendable: ~{fmt(max(maximum, Decimal(0)))} {asset.symbol}."
        )
    if not asset.is_native and gas_balance < fee.amount:
        ui.warning(
            f"This wallet holds {fmt(gas_balance)} {gas_symbol}, but the fee may need up to "
            f"{fmt(fee.amount)} {gas_symbol}. The transaction can fail without gas/energy."
        )

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim", justify="right")
    grid.add_column()
    grid.add_row("From", f"{wallet_name}  [addr]{sender}[/addr]")
    grid.add_row("To", f"[addr]{to}[/addr]")
    grid.add_row("Amount", f"[amount]{fmt(amount)} {asset.symbol}[/amount]  [dim]({asset.label})[/dim]")
    grid.add_row("Network fee", fee.detail)
    ui.console.print(Panel(grid, title="[warn]Review before sending[/warn]",
                           border_style="yellow", expand=False))

    if not yes:
        ui.require_tty("Pass --yes in non-interactive mode.")
        if not ui.confirm("Broadcast this transaction?", default=False):
            ui.info("Cancelled — nothing was sent.")
            raise typer.Exit()

    keyring = unlock_keyring(vault, wallet_name)
    key = keyring.eth_private_key if asset.chain == ETHEREUM else keyring.tron_private_key
    with ui.spinner("Signing & broadcasting…"):
        if asset.is_native:
            result = chain.send_native(key, to, amount)
        else:
            result = chain.send_token(key, asset.contract, asset.decimals, to, amount)
    tx_success_panel(asset, amount, to, result)

    if (sys.stdin.isatty() and not yes and book.find_by_address(to) is None
            and ui.confirm("Save this recipient in the address book?", default=False)):
        for _ in range(3):
            try:
                saved = book.add(ui.ask("Alias for this address (e.g. mom, exchange)"), to)
                ui.success(f"[bold]{saved.alias}[/bold] → {saved.address}", title="Contact saved")
                break
            except AddressBookError as exc:
                ui.warning(str(exc))


# --------------------------------------------------------------------- swap

SWAP_POLL_SECONDS = 8
SWAP_POLL_TIMEOUT = 45 * 60


@app.command()
@guard
def swap(
    wallet: str = typer.Option(None, "--wallet", "-w", help="Wallet to swap with."),
    from_key: str = typer.Option(None, "--from", "-f", help="Asset to swap from (e.g. usdt-erc20)."),
    to_key: str = typer.Option(None, "--to", "-t", help="Asset to receive (e.g. usdt-trc20)."),
    amount_opt: str = typer.Option(None, "--amount", "-m", help="Amount to swap."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompts."),
):
    """Swap across chains (ERC20 ⇄ TRC20), e.g. USDT-ERC20 → USDT-TRC20."""
    ui.rule("swap")
    cfg = Config.load()
    client = SwapClient(cfg.changenow_api_key)
    vault = Vault()

    wallet_name = pick_wallet(vault, wallet, "Swap with which wallet?")
    info = vault.info(wallet_name)

    from_asset = pick_asset(from_key, "Swap [bold]from[/bold] which asset?")
    cross = [a for a in asset_choices() if a.chain != from_asset.chain]
    to_asset = pick_asset(to_key, "Receive which asset?", allowed=cross)
    if to_asset.chain == from_asset.chain:
        raise SwapError("Swaps here are cross-chain: pick assets on different networks "
                        "(e.g. usdt-erc20 → usdt-trc20).")

    source = wallet_address_on(info, from_asset.chain)
    destination = wallet_address_on(info, to_asset.chain)

    with ui.spinner("Checking balance…"):
        available = fetch_balance(cfg, from_asset, source)
    ui.info(f"Available: [amount]{fmt(available)} {from_asset.symbol}[/amount] ({from_asset.label})")

    if amount_opt is None:
        ui.require_tty("Pass --amount in non-interactive mode.")
        amount = ui.ask_amount(f"Amount of {from_asset.symbol} to swap")
    else:
        try:
            amount = Decimal(amount_opt)
        except InvalidOperation:
            raise SwapError(f"--amount '{amount_opt}' is not a number.")
    if amount <= 0:
        raise SwapError("Amount must be greater than zero.")
    if amount > available:
        ui.warning(f"Note: that's more than the wallet holds ({fmt(available)} "
                   f"{from_asset.symbol}) — you'd need to deposit from elsewhere.")

    with ui.spinner("Fetching quote…"):
        quote = client.quote(from_asset, to_asset, amount)

    rate = quote.estimated_amount / quote.from_amount if quote.from_amount else Decimal(0)
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim", justify="right")
    grid.add_column()
    grid.add_row("You send", f"[amount]{fmt(quote.from_amount)} {from_asset.symbol}[/amount]  "
                             f"[dim]{from_asset.label}[/dim]")
    grid.add_row("You receive", f"[amount]≈ {fmt(quote.estimated_amount)} {to_asset.symbol}[/amount]  "
                                f"[dim]{to_asset.label}[/dim]")
    grid.add_row("Rate", f"1 {from_asset.symbol} ≈ {fmt(rate, 6)} {to_asset.symbol} "
                         "[dim](network + service fees included)[/dim]")
    grid.add_row("Payout to", f"[addr]{destination}[/addr] [dim](your {wallet_name} wallet)[/dim]")
    grid.add_row("Provider", "ChangeNOW [dim](non-custodial exchange)[/dim]")
    ui.console.print(Panel(grid, title="[accent]Swap quote[/accent]",
                           border_style="cyan", expand=False))

    if not yes:
        ui.require_tty("Pass --yes in non-interactive mode.")
        if not ui.confirm("Create this swap order?", default=False):
            ui.info("Cancelled — no order was created.")
            raise typer.Exit()

    with ui.spinner("Creating swap order…"):
        order = client.create_order(from_asset, to_asset, amount, destination, source)

    ui.success(
        f"Order [bold]{order.order_id}[/bold] created.\n"
        f"Deposit [amount]{fmt(order.from_amount)} {from_asset.symbol}[/amount] "
        f"({from_asset.label}) to:\n[addr]{order.deposit_address}[/addr]",
        title="Swap order",
    )

    auto = False
    if amount <= available:
        if yes:
            auto = True
        else:
            auto = ui.confirm(
                f"Send the {from_asset.symbol} deposit from wallet '{wallet_name}' now?",
                default=True,
            )
    if auto:
        chain = chain_for(cfg, from_asset)
        keyring = unlock_keyring(vault, wallet_name)
        key = keyring.eth_private_key if from_asset.chain == ETHEREUM else keyring.tron_private_key
        with ui.spinner("Sending deposit…"):
            if from_asset.is_native:
                result = chain.send_native(key, order.deposit_address, amount)
            else:
                result = chain.send_token(key, from_asset.contract, from_asset.decimals,
                                          order.deposit_address, amount)
        tx_success_panel(from_asset, amount, order.deposit_address, result)
    else:
        ui.qr(order.deposit_address, f"Deposit · {from_asset.label}")
        ui.info("Send the deposit from any wallet, then track it with "
                f"[accent]wallet swap-status {order.order_id}[/accent]")

    _watch_swap(client, order.order_id)


def _watch_swap(client: SwapClient, order_id: str) -> None:
    ui.info("Watching the order — [dim]Ctrl+C to stop; resume any time with[/dim] "
            f"[accent]wallet swap-status {order_id}[/accent]")
    started = time.monotonic()
    last = ""
    try:
        with ui.spinner("Contacting swap service…") as status_ctx:
            while True:
                state = client.status(order_id)
                if state.status != last:
                    last = state.status
                    ui.console.print(f"  [dim]{time.strftime('%H:%M:%S')}[/dim] "
                                     f"[accent]{state.label}[/accent]")
                if state.done:
                    got = f"{fmt(state.amount_to)} " if state.amount_to else ""
                    message = f"Swap finished — {got}delivered to your wallet."
                    if state.payout_hash:
                        message += f"\nPayout tx: {state.payout_hash}"
                    ui.success(message, title="Swap complete")
                    return
                if state.dead:
                    raise SwapError(
                        f"Swap ended with status '{state.status}'. Deposits are refunded to "
                        "your refund address; contact ChangeNOW support with order id "
                        f"{order_id} if funds don't arrive."
                    )
                if time.monotonic() - started > SWAP_POLL_TIMEOUT:
                    ui.info(f"Still '{state.label}' after 45 min — stopping the watch. "
                            f"Check later with: wallet swap-status {order_id}")
                    return
                status_ctx.update(f"[accent]{state.label} — checking again in "
                                  f"{SWAP_POLL_SECONDS}s[/accent]")
                time.sleep(SWAP_POLL_SECONDS)
    except KeyboardInterrupt:
        ui.console.print()
        ui.info(f"Stopped watching. Resume with: [accent]wallet swap-status {order_id}[/accent]")


@app.command("swap-status")
@guard
def swap_status(
    order_id: str = typer.Argument(..., help="Swap order id from 'wallet swap'."),
    watch: bool = typer.Option(False, "--watch", help="Keep polling until the swap completes."),
):
    """Check (or watch) the status of a swap order."""
    cfg = Config.load()
    client = SwapClient(cfg.changenow_api_key)
    if watch:
        _watch_swap(client, order_id)
        return
    with ui.spinner("Fetching order status…"):
        state = client.status(order_id)
    style = "ok" if state.done else ("err" if state.dead else "accent")
    lines = [f"Status: [{style}]{state.label}[/{style}]"]
    if state.amount_from:
        lines.append(f"Deposited: {fmt(state.amount_from)}")
    if state.amount_to:
        lines.append(f"Payout: {fmt(state.amount_to)}")
    if state.payout_hash:
        lines.append(f"Payout tx: {state.payout_hash}")
    ui.console.print(Panel("\n".join(lines), title=f"[accent]Swap {order_id}[/accent]",
                           border_style="cyan", expand=False))


# ------------------------------------------------------------ export/remove

@app.command()
@guard
def export(name: str = typer.Argument(None, help="Wallet name.")):
    """Reveal a wallet's recovery phrase (requires the passphrase)."""
    vault = Vault()
    wallet_name = pick_wallet(vault, name, "Export which wallet?")
    ui.warning("The recovery phrase is about to be shown on screen.\n"
               "Make sure nobody can see or record your display.")
    if sys.stdin.isatty() and not ui.confirm("Continue?", default=False):
        raise typer.Exit()
    secret = vault.unlock(wallet_name, ui.ask_passphrase())
    ui.mnemonic_panel(secret["mnemonic"])
    if secret.get("bip39_passphrase"):
        ui.warning("This wallet ALSO uses a BIP39 passphrase ('25th word'). "
                   "The words above alone will not restore it.")


@app.command()
@guard
def remove(
    name: str = typer.Argument(None, help="Wallet name."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
):
    """Remove a wallet from the vault (does NOT touch funds on-chain)."""
    vault = Vault()
    wallet_name = pick_wallet(vault, name, "Remove which wallet?")
    info = vault.info(wallet_name)
    ui.warning(
        f"Removing [bold]{wallet_name}[/bold] deletes its encrypted keys from this vault.\n"
        f"ETH  {info.eth_address}\nTRON {info.tron_address}\n\n"
        "Funds stay on-chain, but without the recovery phrase written down "
        "you will lose access to them FOREVER.",
        title="Danger zone",
    )
    if not yes:
        ui.require_tty("Pass --yes in non-interactive mode.")
        typed = ui.ask(f"Type the wallet name ([bold]{wallet_name}[/bold]) to confirm")
        if typed.strip() != wallet_name:
            ui.info("Name did not match — nothing was removed.")
            raise typer.Exit(1)
    vault.remove(wallet_name)
    ui.success(f"Wallet [bold]{wallet_name}[/bold] removed from the vault.")


# ------------------------------------------------------------ address book

@address_app.command("add")
@guard
def address_add(
    alias: str = typer.Argument(None, help="Alias, e.g. mom, exchange, cold-storage."),
    address: str = typer.Argument(None, help="Ethereum (0x…) or Tron (T…) address."),
):
    """Save a recipient address under an alias, usable anywhere an address is asked."""
    book = AddressBook()
    if alias is None:
        ui.require_tty("Pass ALIAS and ADDRESS arguments in non-interactive mode.")
        alias = ui.ask("Alias (e.g. [dim]mom[/dim], [dim]exchange[/dim])").strip()
    if address is None:
        ui.require_tty("Pass the ADDRESS argument in non-interactive mode.")
        address = ui.ask("Address ([dim]0x…[/dim] or [dim]T…[/dim])").strip()
    contact = book.add(alias, address)
    ui.success(
        f"[bold]{contact.alias}[/bold] → [addr]{contact.address}[/addr] "
        f"({CHAIN_LABEL[contact.chain]})\n"
        f"Use it any time: [accent]wallet send --to {contact.alias}[/accent]",
        title="Contact saved",
    )


@address_app.command("list")
@guard
def address_list():
    """List saved contacts."""
    book = AddressBook()
    contacts = book.entries()
    if not contacts:
        ui.warning("No contacts yet.\nAdd one with:  [accent]wallet address add <alias> <address>[/accent]",
                   title="Empty address book")
        return
    table = Table(title=f"📇 {len(contacts)} contact(s)", border_style="grey37",
                  header_style="brand", title_style="accent")
    table.add_column("Alias", style="bold")
    table.add_column("Network", style="dim")
    table.add_column("Address", style="addr")
    table.add_column("Added", style="dim")
    for c in contacts:
        table.add_row(c.alias, CHAIN_LABEL[c.chain], c.address, c.created_at[:10])
    ui.console.print(table)
    ui.console.print(f"[dim]Book: {book.path}[/dim]")


@address_app.command("remove")
@guard
def address_remove(
    alias: str = typer.Argument(..., help="Alias to remove."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
):
    """Remove a contact from the address book."""
    book = AddressBook()
    contact = book.get(alias)
    if contact is None:
        book.remove(alias)  # raises with the list of known aliases
        return
    if not yes:
        ui.require_tty("Pass --yes in non-interactive mode.")
        if not ui.confirm(f"Remove [bold]{alias}[/bold] → {ui.short_addr(contact.address)}?",
                          default=False):
            ui.info("Nothing removed.")
            raise typer.Exit()
    book.remove(alias)
    ui.success(f"Contact [bold]{alias}[/bold] removed.")


# ------------------------------------------------------------------- config

@config_app.command("show")
@guard
def config_show():
    """Show current configuration."""
    cfg = Config.load()
    table = Table(border_style="grey37", header_style="brand")
    table.add_column("Key", style="accent")
    table.add_column("Value")
    secret_keys = {"tron_api_key", "changenow_api_key"}
    for key in Config.keys():
        value = getattr(cfg, key)
        if key in secret_keys and value:
            value = value[:4] + "…" + value[-2:] if len(str(value)) > 8 else "•••"
        table.add_row(key, str(value) if value != "" else "[dim](not set)[/dim]")
    ui.console.print(table)
    ui.console.print(f"[dim]File: {config_path()}[/dim]")


@config_app.command("set")
@guard
def config_set(
    key: str = typer.Argument(..., help=f"One of: {', '.join(Config.keys())}"),
    value: str = typer.Argument(..., help="New value."),
):
    """Set a configuration value, e.g. wallet config set changenow_api_key <key>."""
    cfg = Config.load()
    if key not in Config.keys():
        raise VaultError(f"Unknown key '{key}'. Valid keys: {', '.join(Config.keys())}")
    current = getattr(cfg, key)
    if isinstance(current, int):
        try:
            setattr(cfg, key, int(value))
        except ValueError:
            raise VaultError(f"'{key}' expects a number, got '{value}'.")
    else:
        setattr(cfg, key, value)
    cfg.save()
    ui.success(f"[accent]{key}[/accent] updated.", title="Config saved")


@config_app.command("path")
def config_paths():
    """Show where the vault and config live on disk."""
    ui.console.print(f"home    [accent]{wallet_home()}[/accent]")
    ui.console.print(f"vault   [accent]{vault_path()}[/accent]")
    ui.console.print(f"config  [accent]{config_path()}[/accent]")


# ------------------------------------------------------------------- doctor

@app.command()
@guard
def doctor():
    """Check connectivity and setup health."""
    cfg = Config.load()
    table = Table(border_style="grey37", header_style="brand", show_header=False)
    table.add_column(justify="right", style="dim")
    table.add_column()

    def row(label: str, ok: bool, detail: str):
        icon = "[ok]✔[/ok]" if ok else "[err]✘[/err]"
        table.add_row(label, f"{icon} {detail}")

    row("python", sys.version_info >= (3, 10), sys.version.split()[0])
    try:
        count = len(Vault().names())
        row("vault", True, f"{count} wallet(s) · {vault_path()}")
    except VaultError as exc:
        row("vault", False, str(exc))

    with ui.spinner("Checking Ethereum RPC…"):
        try:
            block = _eth(cfg).w3.eth.block_number
            row("ethereum", True, f"{cfg.eth_rpc_url} · block {block:,}")
        except Exception as exc:
            row("ethereum", False, f"{cfg.eth_rpc_url} · {exc}")
    with ui.spinner("Checking Tron API…"):
        try:
            block = _tron(cfg).client.get_latest_block_number()
            row("tron", True, f"{cfg.tron_api_url} · block {block:,}")
        except Exception as exc:
            row("tron", False, f"{cfg.tron_api_url} · {exc}")
    row("swap api", bool(cfg.changenow_api_key),
        "ChangeNOW key configured" if cfg.changenow_api_key
        else "no key — swaps disabled (wallet config set changenow_api_key <key>)")
    ui.console.print(table)


@app.command()
def version():
    """Print the walletcli version."""
    ui.console.print(f"walletcli [brand]{__version__}[/brand]")


def main() -> None:
    app()


if __name__ == "__main__":
    main()

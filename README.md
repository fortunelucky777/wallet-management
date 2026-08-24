# 🪙 walletcli

A beautiful, secure, **self-custody** wallet for your terminal.
Ethereum ✦ Tron — ETH, TRX, USDT & USDC (ERC20 + TRC20), with encrypted vault
storage and cross-chain swaps.

```
██╗    ██╗ █████╗ ██╗     ██╗     ███████╗████████╗
██║    ██║██╔══██╗██║     ██║     ██╔════╝╚══██╔══╝
██║ █╗ ██║███████║██║     ██║     █████╗     ██║
██║███╗██║██╔══██║██║     ██║     ██╔══╝     ██║
╚███╔███╔╝██║  ██║███████╗███████╗███████╗   ██║
 ╚══╝╚══╝ ╚═╝  ╚═╝╚══════╝╚══════╝╚══════╝   ╚═╝
```

## ✨ Features

- **One mnemonic, two chains** — a single BIP39 recovery phrase derives your
  Ethereum *and* Tron accounts (standard BIP44 paths, compatible with
  MetaMask, TronLink and hardware wallets).
- **Encrypted vault** — secrets are sealed with **Argon2id** (memory-hard KDF,
  128 MiB) + **AES-256-GCM**. The passphrase is never stored. A stolen vault
  file is useless without it.
- **Send** ETH, TRX, USDT & USDC (ERC20 and TRC20) with clear fee estimates
  and a review screen before anything is broadcast.
- **Swap cross-chain** — USDT/USDC between ERC20 ⇄ TRC20 (and ETH ⇄ TRX) via
  ChangeNOW, paid out straight to your own wallet.
- **Address book** — save recipients under aliases (`wallet address add mom T…`)
  and send with `--to mom`; wrong-chain aliases are blocked automatically.
- **Gorgeous UX** — every command is fully interactive: menus, spinners,
  QR codes, colored review panels. Flags exist for scripting.

## 🚀 Install (one command)

```bash
git clone <this-repo> && cd wallet-management && ./install.sh
```

That's it. The installer creates an isolated environment in `~/.walletcli/venv`
and puts a `wallet` command on your PATH. Uninstall any time with
`./install.sh --uninstall` (your vault is never deleted).

<details>
<summary>Manual install (pip / pipx)</summary>

```bash
pipx install .          # or:
pip install --user .
```
</details>

## 🏁 Quickstart

```bash
wallet register        # create (or import) a wallet — guided, ~1 minute
wallet balance         # ETH, TRX, USDT & USDC balances in one table
wallet send            # guided transfer with fee preview
wallet swap            # USDT-ERC20 → USDT-TRC20 and friends
wallet                 # overview of every command
```

Every command is interactive — run it with no flags and follow the prompts.
Every command also takes flags for scripting (`wallet send --help`).

## 📚 Commands

| Command | Description |
|---|---|
| `wallet register` | Create or import a wallet (mnemonic + passphrase), stored encrypted |
| `wallet list` | List registered wallets |
| `wallet show <name> [--qr]` | Receive addresses, optionally as QR codes |
| `wallet balance [name] [-a asset]` | Balances for all six assets |
| `wallet send` | Send `eth`, `trx`, `usdt-erc20`, `usdc-erc20`, `usdt-trc20`, `usdc-trc20` |
| `wallet swap` | Cross-chain swap (ERC20 ⇄ TRC20) with live order tracking |
| `wallet swap-status <id> [--watch]` | Check or watch a swap order |
| `wallet address add/list/remove` | Address book — save recipients as aliases, then `wallet send --to mom` |
| `wallet export <name>` | Reveal the recovery phrase (passphrase required) |
| `wallet remove <name>` | Remove a wallet from the vault |
| `wallet config show/set/path` | Endpoints, API keys, fee limits |
| `wallet doctor` | Connectivity & setup health check |

Asset keys: `eth`, `usdt-erc20`, `usdc-erc20`, `trx`, `usdt-trc20`, `usdc-trc20`.

## ⚙️ Configuration

Stored in `~/.walletcli/config.toml` (see `wallet config show`):

| Key | Default | Notes |
|---|---|---|
| `eth_rpc_url` | publicnode.com | any Ethereum JSON-RPC endpoint |
| `tron_api_url` | api.trongrid.io | Tron full-node HTTP API |
| `tron_api_key` | – | free at [trongrid.io](https://www.trongrid.io) — avoids rate limits |
| `tron_fee_limit_trx` | 100 | max TRX burned per TRC20 transfer |
| `changenow_api_key` | – | free at [changenow.io/api](https://changenow.io/api) — required for swaps |

For automation, `WALLETCLI_PASSPHRASE` supplies the passphrase and
`WALLETCLI_HOME` relocates the vault (defaults to `~/.walletcli`).

## 🔐 Security model — honest edition

**What the vault protects you from:** anyone who obtains the vault *file* —
a stolen laptop, a leaked backup, malware that exfiltrates your disk. Secrets
are encrypted with AES-256-GCM under a key derived from your passphrase with
Argon2id (`t=3`, `m=128 MiB`, `p=4`), which makes brute-forcing even a modest
passphrase brutally expensive. Tampering with the file is detected
(authenticated encryption; entries are cryptographically bound to their wallet
names). The passphrase is never written anywhere. Files are `0600` in a `0700`
directory, written atomically.

**What no software can protect you from:** an *actively* compromised machine.
A keylogger can capture your passphrase as you type it; malware with root can
read process memory while a wallet is unlocked. That is true of every software
wallet. For significant funds, use a hardware wallet; keep your recovery
phrase on paper, never in a file, photo, or password manager.

Practical hygiene the CLI enforces or encourages:

- 24-word phrases by default; backup verification quiz at registration
- imported recovery phrases are typed/pasted with echo disabled (nothing on screen)
- passphrase minimum length, entered twice, hidden input
- screen cleared after showing a new recovery phrase
- mnemonic re-derivation check on unlock (detects vault corruption)
- private keys only ever exist in memory, per operation
- tracebacks are suppressed so secrets can't leak into crash output

## 🔄 How swaps work

Cross-chain swaps use ChangeNOW (a non-custodial instant exchange):

1. `wallet swap` quotes the pair and creates an order → you get a one-time
   **deposit address** on the source chain.
2. walletcli sends the deposit from your wallet (or you send it manually).
3. The exchange pays the destination asset out to **your own address** on the
   destination chain. Track it live with `wallet swap-status <id> --watch`.

## 🧪 Development

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

## ⚠️ Disclaimer

Self-custody means self-responsibility. Transactions on Ethereum and Tron are
irreversible. Always verify addresses and amounts on the review screen before
confirming. This software is provided as-is, without warranty (MIT license).

"""Application configuration and filesystem paths.

Everything lives under the wallet home directory (default ``~/.walletcli``,
overridable with the ``WALLETCLI_HOME`` environment variable):

    ~/.walletcli/
    ├── vault.json    encrypted wallet vault
    └── config.toml   network endpoints / API keys / fee settings
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

import tomli_w

ENV_HOME = "WALLETCLI_HOME"
ENV_PASSPHRASE = "WALLETCLI_PASSPHRASE"


def wallet_home() -> Path:
    home = os.environ.get(ENV_HOME)
    return Path(home).expanduser() if home else Path.home() / ".walletcli"


def vault_path() -> Path:
    return wallet_home() / "vault.json"


def config_path() -> Path:
    return wallet_home() / "config.toml"


@dataclass
class Config:
    # Ethereum JSON-RPC endpoint. Any provider works (Infura, Alchemy, public).
    eth_rpc_url: str = "https://ethereum-rpc.publicnode.com"
    # Tron full-node HTTP API endpoint.
    tron_api_url: str = "https://api.trongrid.io"
    # Optional TronGrid API key (recommended to avoid rate limits).
    tron_api_key: str = ""
    # Hard cap (in TRX) burned for a TRC20 transfer if you hold no Energy.
    tron_fee_limit_trx: int = 100
    # ChangeNOW API key for cross-chain swaps (free at changenow.io/api).
    changenow_api_key: str = ""

    def save(self) -> None:
        path = config_path()
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        data = {f.name: getattr(self, f.name) for f in fields(self)}
        tmp = path.with_suffix(".toml.tmp")
        tmp.write_text(tomli_w.dumps(data), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)

    @classmethod
    def load(cls) -> "Config":
        path = config_path()
        if not path.exists():
            return cls()
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})

    @classmethod
    def keys(cls) -> list[str]:
        return [f.name for f in fields(cls)]

"""Application configuration and filesystem paths.

Everything lives under the wallet home directory (default ``~/.walletcli``,
overridable with the ``WALLETCLI_HOME`` environment variable):

    ~/.walletcli/
    ├── vault.json    encrypted wallet vault
    └── config.toml   network endpoints / API keys / fee settings
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

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


def write_private_text(path: Path, text: str) -> None:
    """Atomically write ``text`` to ``path`` so it is never world-readable.

    The parent directory is (re-)hardened to 0700 and the temp file is created
    with 0600 *before* any data is written — closing the umask race where a
    ``write_text`` + later ``chmod`` briefly leaves secrets at 0644 on a
    predictable ``*.tmp`` path.
    """
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    tmp = path.with_name(path.name + ".tmp")
    # opener forces 0600 at create time; O_TRUNC in case a stale tmp survives.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, path)


def mask_endpoint(url: str) -> str:
    """Hide any API key embedded in a provider URL's path/query.

    Infura/Alchemy endpoints carry the secret in the path
    (``https://mainnet.infura.io/v3/<KEY>``); show only scheme + host so the
    URL can appear in ``config show``/``doctor`` output without leaking it.
    """
    if not url:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.scheme or not parts.netloc:
        return url
    has_secret = (parts.path not in ("", "/")) or bool(parts.query)
    if not has_secret:
        return url
    return urlunsplit((parts.scheme, parts.netloc, "/…", "", ""))


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
        data = {f.name: getattr(self, f.name) for f in fields(self)}
        write_private_text(config_path(), tomli_w.dumps(data))

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

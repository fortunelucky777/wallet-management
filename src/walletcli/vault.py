"""Encrypted wallet vault.

Security design
---------------
* Secrets (mnemonic + optional BIP39 passphrase) are encrypted per wallet with
  **AES-256-GCM** (authenticated encryption — any tampering is detected).
* The 256-bit key is derived from the user's passphrase with **Argon2id**
  (memory-hard: 128 MiB, 3 iterations, 4 lanes), which makes offline
  brute-force attacks against a stolen vault file extremely expensive.
* Every wallet entry uses its own random 16-byte salt and 12-byte nonce.
* The wallet name is bound into the ciphertext as GCM associated data, so an
  attacker cannot swap encrypted blobs between entries unnoticed.
* The passphrase is never stored, in any form. Files are written atomically
  with 0600 permissions inside a 0700 directory.
* Public addresses are stored in the clear so balances can be checked without
  unlocking; private keys and mnemonics exist only inside the ciphertext.

What this protects against: theft of the vault file (laptop stolen, backup
leaked, file exfiltrated by malware) — without the passphrase the contents are
computationally infeasible to recover. No software can protect keys typed into
an *actively* compromised machine (keylogger reading the passphrase as you
type); for large holdings use a hardware wallet.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from argon2.low_level import Type as Argon2Type
from argon2.low_level import hash_secret_raw
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

VAULT_VERSION = 1

KDF_TIME_COST = 3
KDF_MEMORY_COST = 128 * 1024  # KiB → 128 MiB
KDF_PARALLELISM = 4
KDF_SALT_BYTES = 16
GCM_NONCE_BYTES = 12


class VaultError(Exception):
    """Generic vault failure (corrupt file, duplicate name, missing wallet)."""


class WrongPassphrase(VaultError):
    """Decryption failed — wrong passphrase or tampered vault entry."""


def _b64e(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _b64d(data: str) -> bytes:
    return base64.b64decode(data.encode("ascii"))


def _derive_key(passphrase: str, salt: bytes, *, time_cost: int, memory_cost: int,
                parallelism: int) -> bytes:
    return hash_secret_raw(
        secret=passphrase.encode("utf-8"),
        salt=salt,
        time_cost=time_cost,
        memory_cost=memory_cost,
        parallelism=parallelism,
        hash_len=32,
        type=Argon2Type.ID,
    )


def _aad(name: str) -> bytes:
    return f"walletcli:v{VAULT_VERSION}:{name}".encode("utf-8")


def encrypt_secret(name: str, payload: dict, passphrase: str) -> dict:
    salt = secrets.token_bytes(KDF_SALT_BYTES)
    nonce = secrets.token_bytes(GCM_NONCE_BYTES)
    key = _derive_key(
        passphrase, salt,
        time_cost=KDF_TIME_COST, memory_cost=KDF_MEMORY_COST,
        parallelism=KDF_PARALLELISM,
    )
    plaintext = json.dumps(payload).encode("utf-8")
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, _aad(name))
    return {
        "kdf": {
            "name": "argon2id",
            "salt": _b64e(salt),
            "time_cost": KDF_TIME_COST,
            "memory_cost": KDF_MEMORY_COST,
            "parallelism": KDF_PARALLELISM,
        },
        "cipher": {
            "name": "aes-256-gcm",
            "nonce": _b64e(nonce),
            "ciphertext": _b64e(ciphertext),
        },
    }


def decrypt_secret(name: str, crypto: dict, passphrase: str) -> dict:
    kdf, cipher = crypto["kdf"], crypto["cipher"]
    key = _derive_key(
        passphrase,
        _b64d(kdf["salt"]),
        time_cost=kdf["time_cost"],
        memory_cost=kdf["memory_cost"],
        parallelism=kdf["parallelism"],
    )
    try:
        plaintext = AESGCM(key).decrypt(
            _b64d(cipher["nonce"]), _b64d(cipher["ciphertext"]), _aad(name)
        )
    except InvalidTag as exc:
        raise WrongPassphrase(
            "Could not unlock the wallet — wrong passphrase (or the vault file was tampered with)."
        ) from exc
    return json.loads(plaintext.decode("utf-8"))


@dataclass(frozen=True)
class WalletInfo:
    name: str
    eth_address: str
    tron_address: str
    created_at: str


class Vault:
    """The encrypted wallet store backed by a single JSON file."""

    def __init__(self, path: Path | None = None):
        from .config import vault_path

        self.path = path or vault_path()
        self._data: dict = {"version": VAULT_VERSION, "wallets": {}}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise VaultError(f"Vault file {self.path} is unreadable or corrupt: {exc}") from exc
        if data.get("version") != VAULT_VERSION:
            raise VaultError(f"Unsupported vault version: {data.get('version')!r}")
        self._data = data

    def _save(self) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    # ------------------------------------------------------------------ API

    def names(self) -> list[str]:
        return sorted(self._data["wallets"])

    def exists(self, name: str) -> bool:
        return name in self._data["wallets"]

    def info(self, name: str) -> WalletInfo:
        entry = self._entry(name)
        return WalletInfo(
            name=name,
            eth_address=entry["eth_address"],
            tron_address=entry["tron_address"],
            created_at=entry["created_at"],
        )

    def list_info(self) -> list[WalletInfo]:
        return [self.info(n) for n in self.names()]

    def add(self, name: str, *, mnemonic: str, bip39_passphrase: str,
            passphrase: str, eth_address: str, tron_address: str) -> None:
        if self.exists(name):
            raise VaultError(f"A wallet named '{name}' already exists.")
        payload = {"mnemonic": mnemonic, "bip39_passphrase": bip39_passphrase}
        self._data["wallets"][name] = {
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "eth_address": eth_address,
            "tron_address": tron_address,
            "crypto": encrypt_secret(name, payload, passphrase),
        }
        self._save()

    def unlock(self, name: str, passphrase: str) -> dict:
        """Return the decrypted secret payload {mnemonic, bip39_passphrase}."""
        return decrypt_secret(name, self._entry(name)["crypto"], passphrase)

    def remove(self, name: str) -> None:
        self._entry(name)
        del self._data["wallets"][name]
        self._save()

    def _entry(self, name: str) -> dict:
        try:
            return self._data["wallets"][name]
        except KeyError:
            known = ", ".join(self.names()) or "none registered yet"
            raise VaultError(f"No wallet named '{name}'. Wallets: {known}") from None

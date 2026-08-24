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
import binascii
import json
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


# Sanity bounds for KDF parameters read back from the (untrusted) vault file.
# Upper bounds stop a tampered file from forcing a multi-GiB Argon2 allocation;
# lower bounds stop a downgraded file from silently weakening the KDF.
_MAX_MEMORY_COST = 4 * 1024 * 1024  # KiB → 4 GiB ceiling
_MIN_MEMORY_COST = 8 * 1024         # KiB → 8 MiB floor


def _int_param(kdf: dict, key: str, *, minimum: int, maximum: int) -> int:
    try:
        value = kdf[key]
    except (KeyError, TypeError) as exc:
        raise VaultError(f"Vault entry is corrupt: missing KDF parameter '{key}'.") from exc
    if isinstance(value, bool) or not isinstance(value, int):
        raise VaultError(f"Vault entry is corrupt: KDF parameter '{key}' is not an integer.")
    if not minimum <= value <= maximum:
        raise VaultError(
            f"Vault entry is corrupt: KDF parameter '{key}'={value} is outside the "
            f"accepted range [{minimum}, {maximum}]."
        )
    return value


def decrypt_secret(name: str, crypto: dict, passphrase: str) -> dict:
    if not isinstance(crypto, dict):
        raise VaultError("Vault entry is corrupt: missing crypto block.")
    kdf, cipher = crypto.get("kdf"), crypto.get("cipher")
    if not isinstance(kdf, dict) or not isinstance(cipher, dict):
        raise VaultError("Vault entry is corrupt: missing KDF or cipher block.")
    if kdf.get("name") != "argon2id":
        raise VaultError(f"Vault entry uses an unsupported KDF: {kdf.get('name')!r}.")
    if cipher.get("name") != "aes-256-gcm":
        raise VaultError(f"Vault entry uses an unsupported cipher: {cipher.get('name')!r}.")
    try:
        salt = _b64d(kdf["salt"])
        nonce = _b64d(cipher["nonce"])
        ciphertext = _b64d(cipher["ciphertext"])
    except (KeyError, TypeError, ValueError, binascii.Error) as exc:
        raise VaultError("Vault entry is corrupt: salt/nonce/ciphertext is not valid base64.") from exc
    if len(nonce) != GCM_NONCE_BYTES:
        raise VaultError("Vault entry is corrupt: nonce has the wrong length.")

    key = _derive_key(
        passphrase,
        salt,
        time_cost=_int_param(kdf, "time_cost", minimum=1, maximum=64),
        memory_cost=_int_param(kdf, "memory_cost", minimum=_MIN_MEMORY_COST, maximum=_MAX_MEMORY_COST),
        parallelism=_int_param(kdf, "parallelism", minimum=1, maximum=64),
    )
    try:
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, _aad(name))
    except InvalidTag as exc:
        raise WrongPassphrase(
            "Could not unlock the wallet — wrong passphrase (or the vault file was tampered with)."
        ) from exc
    try:
        return json.loads(plaintext.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VaultError("Vault entry decrypted but its contents are corrupt.") from exc


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

    def verify_addresses(self, name: str, keyring) -> None:
        """Raise if the stored plaintext addresses don't match ``keyring``.

        The addresses live outside the AEAD (so balances work without the
        passphrase), so this re-derivation is what actually detects a tampered
        ``eth_address``/``tron_address``. Both chains are checked.
        """
        entry = self._entry(name)
        for label, derived, stored in (
            ("Ethereum", keyring.eth_address, entry["eth_address"]),
            ("Tron", keyring.tron_address, entry["tron_address"]),
        ):
            if derived.lower() != stored.lower():
                raise VaultError(
                    f"The stored {label} address for '{name}' does not match the one derived "
                    "from its recovery phrase — the vault entry may be corrupt or tampered with."
                )

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
        from .config import write_private_text

        write_private_text(self.path, json.dumps(self._data, indent=2))

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

"""Address book: recipient addresses stored under human-friendly aliases.

Lives in ``~/.walletcli/addressbook.json`` (0600). Addresses are public
information — like the wallet addresses in the vault they are not encrypted,
so aliases resolve instantly without a passphrase.

Aliases can never collide with raw addresses: aliases are capped at 32
characters, while Tron addresses are 34 and Ethereum addresses 42.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .assets import ETHEREUM, TRON

BOOK_VERSION = 1
ALIAS_RE = re.compile(r"^[A-Za-z0-9._-]{1,32}$")


class AddressBookError(Exception):
    """User-readable address-book failure."""


def normalize_address(address: str) -> tuple[str, str]:
    """Return ``(chain, normalized_address)`` for a raw address, offline.

    Ethereum addresses are checksum-validated and returned in EIP-55 form, so a
    mixed-case address with a bad checksum is rejected here rather than stored.
    """
    from .chains.ethereum import checksum_address

    try:
        return ETHEREUM, checksum_address(address)
    except Exception:
        pass
    try:
        from tronpy.keys import is_base58check_address

        if is_base58check_address(address):
            return TRON, address
    except Exception:
        pass
    raise AddressBookError(
        f"'{address}' is not a valid Ethereum (0x…) or Tron (T…) address."
    )


def detect_chain(address: str) -> str:
    """Return ETHEREUM or TRON for a raw address, offline (checksum-validated)."""
    return normalize_address(address)[0]


@dataclass(frozen=True)
class Contact:
    alias: str
    address: str
    chain: str  # ETHEREUM | TRON
    created_at: str


class AddressBook:
    def __init__(self, path: Path | None = None):
        from .config import wallet_home

        self.path = path or wallet_home() / "addressbook.json"
        self._data: dict = {"version": BOOK_VERSION, "entries": {}}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AddressBookError(
                f"Address book {self.path} is unreadable or corrupt: {exc}"
            ) from exc
        if data.get("version") != BOOK_VERSION:
            raise AddressBookError(f"Unsupported address book version: {data.get('version')!r}")
        self._data = data

    def _save(self) -> None:
        from .config import write_private_text

        write_private_text(self.path, json.dumps(self._data, indent=2))

    # ------------------------------------------------------------------ API

    def entries(self) -> list[Contact]:
        return [self._contact(a) for a in sorted(self._data["entries"], key=str.lower)]

    def for_chain(self, chain: str) -> list[Contact]:
        return [c for c in self.entries() if c.chain == chain]

    def exists(self, alias: str) -> bool:
        return alias in self._data["entries"]

    def get(self, alias: str) -> Contact | None:
        return self._contact(alias) if self.exists(alias) else None

    def find_by_address(self, address: str) -> Contact | None:
        needle = address.lower()
        for contact in self.entries():
            if contact.address.lower() == needle:
                return contact
        return None

    def add(self, alias: str, address: str) -> Contact:
        alias = alias.strip()
        if not ALIAS_RE.match(alias):
            raise AddressBookError(
                "Aliases can use letters, digits, dot, dash and underscore (max 32 chars)."
            )
        if self.exists(alias):
            existing = self._contact(alias)
            raise AddressBookError(
                f"Alias '{alias}' already points to {existing.address} — "
                f"remove it first with: wallet address remove {alias}"
            )
        chain, normalized = normalize_address(address.strip())
        self._data["entries"][alias] = {
            "address": normalized,
            "chain": chain,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        self._save()
        return self._contact(alias)

    def remove(self, alias: str) -> Contact:
        if not self.exists(alias):
            known = ", ".join(e.alias for e in self.entries()) or "book is empty"
            raise AddressBookError(f"No alias named '{alias}'. Aliases: {known}")
        contact = self._contact(alias)
        del self._data["entries"][alias]
        self._save()
        return contact

    def _contact(self, alias: str) -> Contact:
        entry = self._data["entries"][alias]
        return Contact(alias=alias, address=entry["address"], chain=entry["chain"],
                       created_at=entry["created_at"])

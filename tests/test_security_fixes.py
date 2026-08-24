"""Regression tests for the security-review fixes."""

import copy

import pytest

from walletcli.addressbook import AddressBook, AddressBookError
from walletcli.chains.base import ChainError
from walletcli.chains.ethereum import checksum_address
from walletcli.config import mask_endpoint
from walletcli.derivation import derive_keyring
from walletcli.ui import parse_amount
from walletcli.vault import Vault, VaultError, decrypt_secret, encrypt_secret

TEST_MNEMONIC = "abandon " * 11 + "about"
ETH_ADDR = "0x9858EfFD232B4033E47d90003D41EC34EcaEda94"
TRON_ADDR = "TUEZSdKsoDHQMeZwihtdoBiN46zxhGWYdH"
REAL_USDT = "0xdAC17F958D2ee523a2206206994597C13D831ec7"


# --- EIP-55 checksum enforcement ------------------------------------------

def test_checksum_accepts_valid_and_lowercase():
    assert checksum_address(REAL_USDT) == REAL_USDT
    assert checksum_address(REAL_USDT.lower()) == REAL_USDT


def test_checksum_rejects_corrupted_mixed_case():
    # Real USDT address with its last hex digit flipped, checksum no longer valid.
    corrupted = "0xdAC17F958D2ee523a2206206994597C13D831ec8"
    with pytest.raises(ChainError):
        checksum_address(corrupted)


def test_addressbook_rejects_bad_checksum(tmp_path):
    book = AddressBook(path=tmp_path / "book.json")
    with pytest.raises(AddressBookError):
        book.add("bad", "0xdAC17F958D2ee523a2206206994597C13D831ec8")


def test_addressbook_stores_checksummed(tmp_path):
    book = AddressBook(path=tmp_path / "book.json")
    contact = book.add("t", REAL_USDT.lower())
    assert contact.address == REAL_USDT  # normalized to EIP-55


# --- Amount parsing --------------------------------------------------------

@pytest.mark.parametrize("bad", ["1,5", "2,75", "nan", "Infinity", "-1", "0", "abc"])
def test_parse_amount_rejects_dangerous_input(bad):
    with pytest.raises(ValueError):
        parse_amount(bad)


def test_parse_amount_accepts_plain_decimal():
    assert str(parse_amount("12.5")) == "12.5"


# --- KDF parameter validation ---------------------------------------------

def _record():
    return encrypt_secret("main", {"mnemonic": TEST_MNEMONIC, "bip39_passphrase": ""}, "passphrase-1")


def test_kdf_rejects_oversized_memory_cost():
    bad = _record()
    bad["kdf"]["memory_cost"] = 10 ** 12
    with pytest.raises(VaultError):
        decrypt_secret("main", bad, "passphrase-1")


def test_kdf_rejects_non_integer_param():
    bad = _record()
    bad["kdf"]["time_cost"] = "3"
    with pytest.raises(VaultError):
        decrypt_secret("main", bad, "passphrase-1")


def test_kdf_rejects_missing_field():
    bad = _record()
    del bad["kdf"]["salt"]
    with pytest.raises(VaultError):
        decrypt_secret("main", bad, "passphrase-1")


def test_kdf_rejects_unknown_algorithm():
    bad = _record()
    bad["kdf"]["name"] = "pbkdf2"
    with pytest.raises(VaultError):
        decrypt_secret("main", bad, "passphrase-1")


# --- Vault address tamper detection (both chains) -------------------------

def test_verify_addresses_detects_tron_tampering(tmp_path):
    vault = Vault(path=tmp_path / "vault.json")
    keyring = derive_keyring(TEST_MNEMONIC)
    vault.add("main", mnemonic=TEST_MNEMONIC, bip39_passphrase="",
              passphrase="pw", eth_address=keyring.eth_address,
              tron_address=keyring.tron_address)
    # Tamper the stored Tron address, then re-derive and verify.
    vault._data["wallets"]["main"]["tron_address"] = "TXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
    with pytest.raises(VaultError):
        vault.verify_addresses("main", keyring)


def test_verify_addresses_passes_when_consistent(tmp_path):
    vault = Vault(path=tmp_path / "vault.json")
    keyring = derive_keyring(TEST_MNEMONIC)
    vault.add("main", mnemonic=TEST_MNEMONIC, bip39_passphrase="",
              passphrase="pw", eth_address=keyring.eth_address,
              tron_address=keyring.tron_address)
    vault.verify_addresses("main", keyring)  # must not raise


# --- Secure file permissions ----------------------------------------------

def test_vault_file_and_dir_permissions(tmp_path):
    home = tmp_path / "home"
    vault = Vault(path=home / "vault.json")
    keyring = derive_keyring(TEST_MNEMONIC)
    vault.add("main", mnemonic=TEST_MNEMONIC, bip39_passphrase="",
              passphrase="pw", eth_address=keyring.eth_address,
              tron_address=keyring.tron_address)
    assert (vault.path.stat().st_mode & 0o777) == 0o600
    assert (home.stat().st_mode & 0o777) == 0o700


# --- Endpoint masking ------------------------------------------------------

def test_mask_endpoint_hides_embedded_key():
    assert mask_endpoint("https://mainnet.infura.io/v3/SECRET") == "https://mainnet.infura.io/…"


def test_mask_endpoint_keeps_plain_host():
    assert mask_endpoint("https://api.trongrid.io") == "https://api.trongrid.io"

import base64
import json

import pytest

from walletcli.vault import Vault, VaultError, WrongPassphrase, decrypt_secret, encrypt_secret

SECRET = {"mnemonic": "abandon " * 11 + "about", "bip39_passphrase": ""}


def make_vault(tmp_path):
    return Vault(path=tmp_path / "vault.json")


def test_encrypt_decrypt_roundtrip():
    record = encrypt_secret("main", SECRET, "correct horse battery staple")
    assert decrypt_secret("main", record, "correct horse battery staple") == SECRET


def test_wrong_passphrase_rejected():
    record = encrypt_secret("main", SECRET, "right-passphrase")
    with pytest.raises(WrongPassphrase):
        decrypt_secret("main", record, "wrong-passphrase")


def test_ciphertext_tamper_detected():
    record = encrypt_secret("main", SECRET, "pw")
    raw = bytearray(base64.b64decode(record["cipher"]["ciphertext"]))
    raw[0] ^= 0xFF
    record["cipher"]["ciphertext"] = base64.b64encode(bytes(raw)).decode()
    with pytest.raises(WrongPassphrase):
        decrypt_secret("main", record, "pw")


def test_entry_swap_detected():
    # Moving an encrypted blob to another wallet name must fail (AAD binding).
    record = encrypt_secret("main", SECRET, "pw")
    with pytest.raises(WrongPassphrase):
        decrypt_secret("other", record, "pw")


def test_vault_add_unlock_remove(tmp_path):
    vault = make_vault(tmp_path)
    vault.add("main", mnemonic=SECRET["mnemonic"], bip39_passphrase="",
              passphrase="pw12345678", eth_address="0xabc", tron_address="Tabc")
    assert vault.names() == ["main"]
    assert vault.info("main").eth_address == "0xabc"
    assert vault.unlock("main", "pw12345678")["mnemonic"] == SECRET["mnemonic"]

    # persisted to disk, reload works
    reloaded = make_vault(tmp_path)
    assert reloaded.unlock("main", "pw12345678")["mnemonic"] == SECRET["mnemonic"]

    reloaded.remove("main")
    assert reloaded.names() == []


def test_duplicate_name_rejected(tmp_path):
    vault = make_vault(tmp_path)
    vault.add("main", mnemonic="m", bip39_passphrase="", passphrase="pw",
              eth_address="0x1", tron_address="T1")
    with pytest.raises(VaultError):
        vault.add("main", mnemonic="m", bip39_passphrase="", passphrase="pw",
                  eth_address="0x1", tron_address="T1")


def test_missing_wallet_message(tmp_path):
    vault = make_vault(tmp_path)
    with pytest.raises(VaultError, match="No wallet named"):
        vault.info("ghost")


def test_no_plaintext_secrets_on_disk(tmp_path):
    vault = make_vault(tmp_path)
    vault.add("main", mnemonic=SECRET["mnemonic"], bip39_passphrase="hunter2",
              passphrase="pw12345678", eth_address="0xabc", tron_address="Tabc")
    raw = (tmp_path / "vault.json").read_text()
    assert "abandon" not in raw
    assert "hunter2" not in raw
    assert "pw12345678" not in raw


def test_vault_file_permissions(tmp_path):
    vault = make_vault(tmp_path)
    vault.add("main", mnemonic="m", bip39_passphrase="", passphrase="pw",
              eth_address="0x1", tron_address="T1")
    assert (vault.path.stat().st_mode & 0o777) == 0o600


def test_corrupt_vault_file(tmp_path):
    path = tmp_path / "vault.json"
    path.write_text("{not json")
    with pytest.raises(VaultError, match="corrupt"):
        Vault(path=path)


def test_kdf_params_recorded():
    record = encrypt_secret("main", SECRET, "pw")
    assert record["kdf"]["name"] == "argon2id"
    assert record["kdf"]["memory_cost"] >= 64 * 1024  # at least 64 MiB
    assert record["cipher"]["name"] == "aes-256-gcm"
    assert json.dumps(record)  # JSON-serialisable

import pytest

from walletcli.derivation import (
    derive_keyring,
    generate_mnemonic,
    normalize_mnemonic,
    validate_mnemonic,
)

# Standard BIP39 test vector.
VECTOR = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"


def test_known_eth_vector():
    # m/44'/60'/0'/0/0 for the BIP39 test mnemonic — cross-checked with
    # MetaMask / Ian Coleman's BIP39 tool.
    keyring = derive_keyring(VECTOR)
    assert keyring.eth_address == "0x9858EfFD232B4033E47d90003D41EC34EcaEda94"


def test_address_formats():
    keyring = derive_keyring(VECTOR)
    assert keyring.eth_address.startswith("0x") and len(keyring.eth_address) == 42
    assert keyring.tron_address.startswith("T") and len(keyring.tron_address) == 34
    assert len(bytes.fromhex(keyring.eth_private_key)) == 32
    assert len(bytes.fromhex(keyring.tron_private_key)) == 32


def test_deterministic():
    assert derive_keyring(VECTOR) == derive_keyring(VECTOR)


def test_bip39_passphrase_changes_addresses():
    plain = derive_keyring(VECTOR)
    salted = derive_keyring(VECTOR, bip39_passphrase="TREZOR")
    assert plain.eth_address != salted.eth_address
    assert plain.tron_address != salted.tron_address


def test_generate_lengths():
    for words in (12, 24):
        mnemonic = generate_mnemonic(words)
        assert len(mnemonic.split()) == words
        assert validate_mnemonic(mnemonic)


def test_generated_mnemonics_unique():
    assert generate_mnemonic() != generate_mnemonic()


def test_invalid_word_count():
    with pytest.raises(ValueError):
        generate_mnemonic(13)


def test_validate_rejects_garbage():
    assert not validate_mnemonic("not a real mnemonic at all")
    # bad checksum: valid words, wrong final word
    assert not validate_mnemonic("abandon " * 11 + "abandon")


def test_normalize():
    assert normalize_mnemonic("  Abandon   ABANDON\nabout ") == "abandon abandon about"

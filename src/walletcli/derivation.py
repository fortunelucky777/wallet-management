"""BIP39 mnemonic generation and BIP44 key derivation for Ethereum and Tron.

Derivation paths (standard, compatible with MetaMask / TronLink / Ledger):
    Ethereum  m/44'/60'/0'/0/0
    Tron      m/44'/195'/0'/0/0
"""

from __future__ import annotations

from dataclasses import dataclass

from bip_utils import (
    Bip39MnemonicGenerator,
    Bip39MnemonicValidator,
    Bip39SeedGenerator,
    Bip39WordsNum,
    Bip44,
    Bip44Changes,
    Bip44Coins,
)

_WORDS = {
    12: Bip39WordsNum.WORDS_NUM_12,
    15: Bip39WordsNum.WORDS_NUM_15,
    18: Bip39WordsNum.WORDS_NUM_18,
    21: Bip39WordsNum.WORDS_NUM_21,
    24: Bip39WordsNum.WORDS_NUM_24,
}


@dataclass(frozen=True)
class Keyring:
    """Addresses and private keys derived from one mnemonic."""

    eth_address: str
    eth_private_key: str  # hex, no 0x prefix
    tron_address: str
    tron_private_key: str  # hex


def generate_mnemonic(words: int = 24) -> str:
    if words not in _WORDS:
        raise ValueError(f"Word count must be one of {sorted(_WORDS)}")
    return str(Bip39MnemonicGenerator().FromWordsNumber(_WORDS[words]))


def validate_mnemonic(mnemonic: str) -> bool:
    return Bip39MnemonicValidator().IsValid(normalize_mnemonic(mnemonic))


def normalize_mnemonic(mnemonic: str) -> str:
    return " ".join(mnemonic.lower().split())


def derive_keyring(mnemonic: str, bip39_passphrase: str = "", index: int = 0) -> Keyring:
    seed = Bip39SeedGenerator(normalize_mnemonic(mnemonic)).Generate(bip39_passphrase)

    def leaf(coin: Bip44Coins):
        return (
            Bip44.FromSeed(seed, coin)
            .Purpose()
            .Coin()
            .Account(0)
            .Change(Bip44Changes.CHAIN_EXT)
            .AddressIndex(index)
        )

    eth = leaf(Bip44Coins.ETHEREUM)
    tron = leaf(Bip44Coins.TRON)
    return Keyring(
        eth_address=eth.PublicKey().ToAddress(),
        eth_private_key=eth.PrivateKey().Raw().ToHex(),
        tron_address=tron.PublicKey().ToAddress(),
        tron_private_key=tron.PrivateKey().Raw().ToHex(),
    )

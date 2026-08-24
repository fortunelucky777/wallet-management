"""Registry of supported assets (chains, tokens, contracts, decimals)."""

from __future__ import annotations

from dataclasses import dataclass

ETHEREUM = "ethereum"
TRON = "tron"

CHAIN_LABEL = {ETHEREUM: "Ethereum", TRON: "Tron"}


@dataclass(frozen=True)
class Asset:
    key: str          # stable CLI identifier, e.g. "usdt-erc20"
    symbol: str       # ticker, e.g. "USDT"
    name: str         # human label
    chain: str        # ETHEREUM | TRON
    decimals: int
    contract: str = ""  # empty for native coins

    @property
    def is_native(self) -> bool:
        return not self.contract

    @property
    def label(self) -> str:
        std = "ERC20" if self.chain == ETHEREUM else "TRC20"
        suffix = "native coin" if self.is_native else std
        return f"{self.symbol} · {CHAIN_LABEL[self.chain]} {suffix}"


ASSETS: dict[str, Asset] = {
    a.key: a
    for a in [
        Asset("eth", "ETH", "Ether", ETHEREUM, 18),
        Asset(
            "usdt-erc20", "USDT", "Tether USD", ETHEREUM, 6,
            "0xdAC17F958D2ee523a2206206994597C13D831ec7",
        ),
        Asset(
            "usdc-erc20", "USDC", "USD Coin", ETHEREUM, 6,
            "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        ),
        Asset("trx", "TRX", "Tron", TRON, 6),
        Asset(
            "usdt-trc20", "USDT", "Tether USD", TRON, 6,
            "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",
        ),
        Asset(
            "usdc-trc20", "USDC", "USD Coin", TRON, 6,
            "TEkxiTehnzSmSe2XqrBj4w32RUN966rdz8",
        ),
    ]
}

# ChangeNOW (currency, network) tickers for the swap engine.
SWAP_TICKERS: dict[str, tuple[str, str]] = {
    "eth": ("eth", "eth"),
    "usdt-erc20": ("usdt", "eth"),
    "usdc-erc20": ("usdc", "eth"),
    "trx": ("trx", "trx"),
    "usdt-trc20": ("usdt", "trx"),
    "usdc-trc20": ("usdc", "trx"),
}


def asset_choices() -> list[Asset]:
    return list(ASSETS.values())


def get_asset(key: str) -> Asset:
    k = key.lower().strip()
    if k not in ASSETS:
        valid = ", ".join(ASSETS)
        raise KeyError(f"Unknown asset '{key}'. Valid assets: {valid}")
    return ASSETS[k]

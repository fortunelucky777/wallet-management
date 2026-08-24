"""Shared chain types."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


class ChainError(Exception):
    """Network / RPC / transaction failure with a user-readable message."""


@dataclass(frozen=True)
class TxResult:
    txid: str
    explorer_url: str


@dataclass(frozen=True)
class FeeEstimate:
    asset_symbol: str      # what the fee is paid in (ETH or TRX)
    amount: Decimal        # estimated (ETH) or maximum (TRX fee-limit) cost
    detail: str            # human explanation shown before confirmation
    is_upper_bound: bool = False


def to_base_units(amount: Decimal, decimals: int) -> int:
    scaled = amount.scaleb(decimals)
    if scaled != scaled.to_integral_value():
        raise ChainError(
            f"Amount {amount} has more precision than the asset supports ({decimals} decimals)."
        )
    return int(scaled)


def from_base_units(raw: int, decimals: int) -> Decimal:
    return Decimal(raw).scaleb(-decimals)

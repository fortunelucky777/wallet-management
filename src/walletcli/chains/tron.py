"""Tron client: balances and transfers for TRX and TRC20 tokens."""

from __future__ import annotations

from decimal import Decimal
from functools import cached_property

from .base import ChainError, FeeEstimate, TxResult, from_base_units, to_base_units

EXPLORER = "https://tronscan.org/#/transaction/{txid}"
SUN_PER_TRX = 1_000_000

RATE_LIMIT_HINT = (
    "TronGrid rate-limits anonymous requests. Get a free API key at "
    "https://www.trongrid.io and save it with: wallet config set tron_api_key <key>"
)


def _wrap(prefix: str, exc: Exception) -> ChainError:
    message = f"{prefix}: {exc}"
    if "429" in str(exc):
        message += f"\n{RATE_LIMIT_HINT}"
    return ChainError(message)


class TronChain:
    def __init__(self, api_url: str, api_key: str = "", fee_limit_trx: int = 100):
        self.api_url = api_url
        self.api_key = api_key
        self.fee_limit_sun = fee_limit_trx * SUN_PER_TRX

    @cached_property
    def client(self):
        from tronpy import Tron
        from tronpy.providers import HTTPProvider

        kwargs = {"timeout": 30.0}
        if self.api_key:
            kwargs["api_key"] = self.api_key
        try:
            return Tron(HTTPProvider(self.api_url, **kwargs))
        except Exception as exc:
            raise ChainError(f"Could not initialise Tron client for {self.api_url}: {exc}") from exc

    # -------------------------------------------------------------- helpers

    def validate_address(self, address: str) -> str:
        from tronpy.keys import is_base58check_address

        try:
            valid = is_base58check_address(address)
        except Exception:
            valid = False
        if not valid:
            raise ChainError(f"'{address}' is not a valid Tron address.")
        return address

    def _private_key(self, private_key_hex: str):
        from tronpy.keys import PrivateKey

        return PrivateKey(bytes.fromhex(private_key_hex))

    # ------------------------------------------------------------- balances

    def native_balance(self, address: str) -> Decimal:
        from tronpy.exceptions import AddressNotFound

        try:
            return Decimal(self.client.get_account_balance(self.validate_address(address)))
        except AddressNotFound:
            return Decimal(0)  # account not yet activated on-chain
        except ChainError:
            raise
        except Exception as exc:
            raise _wrap("Could not fetch TRX balance", exc) from exc

    def token_balance(self, address: str, contract_address: str, decimals: int) -> Decimal:
        try:
            contract = self.client.get_contract(contract_address)
            raw = contract.functions.balanceOf(self.validate_address(address))
        except ChainError:
            raise
        except Exception as exc:
            raise _wrap("Could not fetch TRC20 balance", exc) from exc
        return from_base_units(raw, decimals)

    # ------------------------------------------------------------ transfers

    def estimate_native_fee(self) -> FeeEstimate:
        return FeeEstimate(
            "TRX",
            Decimal("1.1"),
            "≈ 1.1 TRX if you have no free bandwidth (often free with staked bandwidth)",
        )

    def estimate_token_fee(self) -> FeeEstimate:
        limit = from_base_units(self.fee_limit_sun, 6)
        return FeeEstimate(
            "TRX",
            limit,
            f"up to {limit} TRX burned for Energy (fee-limit cap; usually ~14 TRX for USDT, "
            "0 if you have staked Energy)",
            is_upper_bound=True,
        )

    def send_native(self, private_key_hex: str, to: str, amount: Decimal) -> TxResult:
        pk = self._private_key(private_key_hex)
        owner = pk.public_key.to_base58check_address()
        try:
            txn = (
                self.client.trx.transfer(
                    owner, self.validate_address(to), to_base_units(amount, 6)
                )
                .build()
                .sign(pk)
            )
            result = txn.broadcast()
        except ChainError:
            raise
        except Exception as exc:
            raise _wrap("Tron node rejected the transaction", exc) from exc
        return self._result(result, txn)

    def send_token(self, private_key_hex: str, contract_address: str, decimals: int,
                   to: str, amount: Decimal) -> TxResult:
        pk = self._private_key(private_key_hex)
        owner = pk.public_key.to_base58check_address()
        try:
            contract = self.client.get_contract(contract_address)
            txn = (
                contract.functions.transfer(
                    self.validate_address(to), to_base_units(amount, decimals)
                )
                .with_owner(owner)
                .fee_limit(self.fee_limit_sun)
                .build()
                .sign(pk)
            )
            result = txn.broadcast()
        except ChainError:
            raise
        except Exception as exc:
            raise _wrap("Tron node rejected the transaction", exc) from exc
        return self._result(result, txn)

    def _result(self, broadcast_result: dict, txn) -> TxResult:
        txid = broadcast_result.get("txid") or txn.txid
        if broadcast_result.get("result") is not True and "txid" not in broadcast_result:
            raise ChainError(f"Tron broadcast failed: {broadcast_result}")
        return TxResult(txid=txid, explorer_url=EXPLORER.format(txid=txid))

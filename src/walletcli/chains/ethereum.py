"""Ethereum client: balances and transfers for ETH and ERC20 tokens (EIP-1559)."""

from __future__ import annotations

from decimal import Decimal
from functools import cached_property

from .base import ChainError, FeeEstimate, TxResult, from_base_units, to_base_units

ERC20_ABI = [
    {
        "name": "balanceOf",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "owner", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "transfer",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "to", "type": "address"},
            {"name": "value", "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
    },
]

NATIVE_GAS = 21_000
TOKEN_GAS_FALLBACK = 120_000
EXPLORER = "https://etherscan.io/tx/{txid}"


def checksum_address(address: str) -> str:
    """Validate an Ethereum address and return its EIP-55 checksummed form.

    ``Web3.is_address`` accepts a hex string with a *wrong* EIP-55 checksum and
    ``to_checksum_address`` would silently rewrite it, so a corrupted mixed-case
    address (typo, truncated paste, clipboard malware) would pass unnoticed.
    Here a mixed-case address must pass the checksum; an all-lower/all-upper
    address carries no checksum information and is accepted, matching MetaMask.
    """
    from web3 import Web3

    if not isinstance(address, str) or not Web3.is_address(address):
        raise ChainError(f"'{address}' is not a valid Ethereum address.")
    body = address[2:] if address[:2].lower() == "0x" else address
    is_mixed_case = any(c.isupper() for c in body) and any(c.islower() for c in body)
    if is_mixed_case and not Web3.is_checksum_address(address):
        raise ChainError(
            f"'{address}' has an invalid EIP-55 checksum — it may be mistyped or corrupted. "
            "Double-check the address."
        )
    return Web3.to_checksum_address(address)


class EthereumChain:
    def __init__(self, rpc_url: str):
        self.rpc_url = rpc_url

    @property
    def _display_url(self) -> str:
        from ..config import mask_endpoint

        return mask_endpoint(self.rpc_url)

    @cached_property
    def w3(self):
        from web3 import HTTPProvider, Web3

        w3 = Web3(HTTPProvider(self.rpc_url, request_kwargs={"timeout": 30}))
        try:
            connected = w3.is_connected()
        except Exception as exc:  # DNS failure, TLS error, ...
            raise ChainError(f"Could not reach Ethereum RPC {self._display_url}: {exc}") from exc
        if not connected:
            raise ChainError(
                f"Could not reach Ethereum RPC {self._display_url}. "
                "Check your connection, or set another endpoint with "
                "'wallet config set eth_rpc_url <url>'."
            )
        return w3

    # -------------------------------------------------------------- helpers

    def validate_address(self, address: str) -> str:
        return checksum_address(address)

    def _contract(self, contract_address: str):
        return self.w3.eth.contract(
            address=self.validate_address(contract_address), abi=ERC20_ABI
        )

    def _fees(self) -> tuple[int, int]:
        block = self.w3.eth.get_block("latest")
        base = block.get("baseFeePerGas")
        if base is None:
            raise ChainError(
                f"The RPC endpoint {self._display_url} did not report an EIP-1559 base fee "
                "(is it an Ethereum mainnet node?)."
            )
        try:
            tip = self.w3.eth.max_priority_fee
        except Exception:
            tip = self.w3.to_wei(1.5, "gwei")
        return base * 2 + tip, tip  # (max_fee, priority_fee)

    # ------------------------------------------------------------- balances

    def native_balance(self, address: str) -> Decimal:
        address = self.validate_address(address)
        try:
            wei = self.w3.eth.get_balance(address)
        except ChainError:
            raise
        except Exception as exc:
            raise ChainError(f"Could not fetch ETH balance: {exc}") from exc
        return from_base_units(wei, 18)

    def token_balance(self, address: str, contract_address: str, decimals: int) -> Decimal:
        address = self.validate_address(address)
        try:
            raw = self._contract(contract_address).functions.balanceOf(address).call()
        except ChainError:
            raise
        except Exception as exc:
            raise ChainError(f"Could not fetch token balance: {exc}") from exc
        return from_base_units(raw, decimals)

    # ------------------------------------------------------------ transfers

    def estimate_native_fee(self) -> FeeEstimate:
        max_fee, _ = self._fees()
        cost = from_base_units(NATIVE_GAS * max_fee, 18)
        return FeeEstimate("ETH", cost, f"≈ {cost:.6f} ETH network fee (21000 gas, EIP-1559)")

    def estimate_token_fee(self, sender: str, contract_address: str,
                           to: str, raw_amount: int) -> tuple[FeeEstimate, int]:
        from web3.exceptions import ContractLogicError

        contract = self._contract(contract_address)
        try:
            gas = contract.functions.transfer(
                self.validate_address(to), raw_amount
            ).estimate_gas({"from": self.validate_address(sender)})
            gas = int(gas * 1.2)
        except ContractLogicError as exc:
            # A revert here means the transfer WILL fail on-chain (recipient
            # blacklisted, contract paused, insufficient token balance). Aborting
            # now saves the user from burning gas on a guaranteed-revert tx.
            raise ChainError(
                f"The token transfer would revert on-chain and was not sent: {exc}"
            ) from exc
        except Exception:
            # Transient/estimation quirks: fall back to a safe gas ceiling.
            gas = TOKEN_GAS_FALLBACK
        max_fee, _ = self._fees()
        cost = from_base_units(gas * max_fee, 18)
        return FeeEstimate("ETH", cost, f"≈ {cost:.6f} ETH network fee ({gas} gas)"), gas

    def send_native(self, private_key_hex: str, to: str, amount: Decimal) -> TxResult:
        w3 = self.w3
        account = w3.eth.account.from_key(bytes.fromhex(private_key_hex))
        max_fee, tip = self._fees()
        tx = {
            "chainId": w3.eth.chain_id,
            "from": account.address,
            "to": self.validate_address(to),
            "value": to_base_units(amount, 18),
            "gas": NATIVE_GAS,
            "maxFeePerGas": max_fee,
            "maxPriorityFeePerGas": tip,
            "nonce": w3.eth.get_transaction_count(account.address, "pending"),
        }
        return self._sign_and_send(account, tx)

    def send_token(self, private_key_hex: str, contract_address: str, decimals: int,
                   to: str, amount: Decimal) -> TxResult:
        w3 = self.w3
        account = w3.eth.account.from_key(bytes.fromhex(private_key_hex))
        raw = to_base_units(amount, decimals)
        _, gas = self.estimate_token_fee(account.address, contract_address, to, raw)
        max_fee, tip = self._fees()
        contract = self._contract(contract_address)
        tx = contract.functions.transfer(
            self.validate_address(to), raw
        ).build_transaction(
            {
                "chainId": w3.eth.chain_id,
                "from": account.address,
                "gas": gas,
                "maxFeePerGas": max_fee,
                "maxPriorityFeePerGas": tip,
                "nonce": w3.eth.get_transaction_count(account.address, "pending"),
            }
        )
        return self._sign_and_send(account, tx)

    def _sign_and_send(self, account, tx: dict) -> TxResult:
        try:
            signed = account.sign_transaction(tx)
            raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")
            tx_hash = self.w3.eth.send_raw_transaction(raw)
        except ValueError as exc:
            raise ChainError(f"Ethereum node rejected the transaction: {exc}") from exc
        txid = tx_hash.hex()
        if not txid.startswith("0x"):
            txid = "0x" + txid
        return TxResult(txid=txid, explorer_url=EXPLORER.format(txid=txid))

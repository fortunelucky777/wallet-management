"""Cross-chain swaps (ERC20 ⇄ TRC20) via the ChangeNOW exchange API.

A swap works like every non-custodial bridge/exchange:

1. Create an exchange order → you get a one-time **deposit address** on the
   source chain and the exchange commits to pay out on the destination chain.
2. Send the source asset to that deposit address (walletcli can do this for
   you from your wallet).
3. The exchange delivers the destination asset to *your own* wallet address.

A free API key is required: https://changenow.io/api — then run
``wallet config set changenow_api_key <key>``.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import httpx

from .assets import SWAP_TICKERS, Asset

API_BASE = "https://api.changenow.io/v2"

# Order states, in rough lifecycle order.
TERMINAL_OK = {"finished"}
TERMINAL_BAD = {"failed", "refunded", "expired"}
STATUS_LABELS = {
    "new": "order created",
    "waiting": "waiting for your deposit",
    "confirming": "deposit confirming on-chain",
    "exchanging": "exchanging",
    "sending": "sending funds to your wallet",
    "finished": "finished — funds delivered",
    "failed": "failed",
    "refunded": "refunded",
    "verifying": "verifying",
    "expired": "expired",
}


class SwapError(Exception):
    """Swap API failure with a user-readable message."""


@dataclass(frozen=True)
class SwapQuote:
    from_asset: Asset
    to_asset: Asset
    from_amount: Decimal
    estimated_amount: Decimal
    min_amount: Decimal


@dataclass(frozen=True)
class SwapOrder:
    order_id: str
    deposit_address: str
    payout_address: str
    from_amount: Decimal
    estimated_amount: Decimal


@dataclass(frozen=True)
class SwapStatus:
    order_id: str
    status: str
    label: str
    amount_from: Decimal | None
    amount_to: Decimal | None
    payout_hash: str | None

    @property
    def done(self) -> bool:
        return self.status in TERMINAL_OK

    @property
    def dead(self) -> bool:
        return self.status in TERMINAL_BAD


class SwapClient:
    def __init__(self, api_key: str):
        if not api_key:
            raise SwapError(
                "Swaps need a (free) ChangeNOW API key.\n"
                "1. Get one at https://changenow.io/api\n"
                "2. Save it:  wallet config set changenow_api_key <your-key>"
            )
        self._client = httpx.Client(
            base_url=API_BASE,
            headers={"x-changenow-api-key": api_key},
            timeout=30,
        )

    def _get(self, path: str, params: dict) -> dict:
        return self._request("GET", path, params=params)

    def _post(self, path: str, payload: dict) -> dict:
        return self._request("POST", path, json=payload)

    def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise SwapError(f"Could not reach the swap service: {exc}") from exc
        if response.status_code >= 400:
            try:
                message = response.json().get("message") or response.text
            except ValueError:
                message = response.text
            raise SwapError(f"Swap service error ({response.status_code}): {message}")
        return response.json()

    @staticmethod
    def _pair(from_asset: Asset, to_asset: Asset) -> dict:
        from_cur, from_net = SWAP_TICKERS[from_asset.key]
        to_cur, to_net = SWAP_TICKERS[to_asset.key]
        return {
            "fromCurrency": from_cur,
            "fromNetwork": from_net,
            "toCurrency": to_cur,
            "toNetwork": to_net,
        }

    # ------------------------------------------------------------------ API

    def quote(self, from_asset: Asset, to_asset: Asset, amount: Decimal) -> SwapQuote:
        pair = self._pair(from_asset, to_asset)
        minimum = self._get("/exchange/min-amount", {**pair, "flow": "standard"})
        raw_min = minimum.get("minAmount") if isinstance(minimum, dict) else None
        if raw_min is None:
            raise SwapError(
                "The swap service did not return a minimum amount for this pair — "
                "it may be unsupported or temporarily unavailable. No order was created."
            )
        min_amount = Decimal(str(raw_min))
        if amount < min_amount:
            raise SwapError(
                f"Amount too small: minimum for this pair is "
                f"{min_amount} {from_asset.symbol}."
            )
        estimate = self._get(
            "/exchange/estimated-amount",
            {**pair, "fromAmount": str(amount), "flow": "standard"},
        )
        estimated = estimate.get("toAmount")
        if estimated is None:
            raise SwapError(f"Swap service returned no estimate: {estimate}")
        return SwapQuote(from_asset, to_asset, amount, Decimal(str(estimated)), min_amount)

    def create_order(self, from_asset: Asset, to_asset: Asset, amount: Decimal,
                     payout_address: str, refund_address: str) -> SwapOrder:
        payload = {
            **self._pair(from_asset, to_asset),
            "fromAmount": str(amount),
            "address": payout_address,
            "refundAddress": refund_address,
            "flow": "standard",
        }
        data = self._post("/exchange", payload)
        if not data.get("payinAddress"):
            raise SwapError(f"Swap service did not return a deposit address: {data}")
        return SwapOrder(
            order_id=data["id"],
            deposit_address=data["payinAddress"],
            payout_address=data.get("payoutAddress", payout_address),
            from_amount=Decimal(str(data.get("fromAmount") or amount)),
            estimated_amount=Decimal(str(data.get("toAmount") or 0)),
        )

    def status(self, order_id: str) -> SwapStatus:
        data = self._get("/exchange/by-id", {"id": order_id})
        raw_status = data.get("status", "unknown")

        def dec(key: str) -> Decimal | None:
            value = data.get(key)
            return Decimal(str(value)) if value is not None else None

        return SwapStatus(
            order_id=order_id,
            status=raw_status,
            label=STATUS_LABELS.get(raw_status, raw_status),
            amount_from=dec("amountFrom"),
            amount_to=dec("amountTo"),
            payout_hash=data.get("payoutHash"),
        )

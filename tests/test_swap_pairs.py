"""Swap pair rules: same-chain and cross-chain allowed, self-swap refused."""

import pytest

from walletcli.assets import get_asset
from walletcli.swap import SwapClient, SwapError, validate_swap_pair


# --- pair validation --------------------------------------------------------

@pytest.mark.parametrize("from_key,to_key", [
    ("eth", "usdt-erc20"),          # same-chain, Ethereum
    ("usdt-erc20", "eth"),          # same-chain, reverse
    ("trx", "usdt-trc20"),          # same-chain, Tron
    ("usdt-erc20", "usdt-trc20"),   # cross-chain (the original case)
    ("eth", "trx"),                 # cross-chain natives
])
def test_valid_pairs_accepted(from_key, to_key):
    validate_swap_pair(get_asset(from_key), get_asset(to_key))  # must not raise


@pytest.mark.parametrize("key", ["eth", "usdt-erc20", "usdt-trc20"])
def test_self_swap_rejected(key):
    with pytest.raises(SwapError):
        validate_swap_pair(get_asset(key), get_asset(key))


# --- ChangeNOW ticker mapping ----------------------------------------------

def test_pair_tickers_same_chain():
    pair = SwapClient._pair(get_asset("eth"), get_asset("usdt-erc20"))
    assert pair == {
        "fromCurrency": "eth", "fromNetwork": "eth",
        "toCurrency": "usdt", "toNetwork": "eth",
    }


def test_pair_tickers_cross_chain():
    pair = SwapClient._pair(get_asset("usdt-erc20"), get_asset("usdt-trc20"))
    assert pair == {
        "fromCurrency": "usdt", "fromNetwork": "eth",
        "toCurrency": "usdt", "toNetwork": "trx",
    }

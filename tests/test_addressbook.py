import pytest

from walletcli.addressbook import AddressBook, AddressBookError, detect_chain
from walletcli.assets import ETHEREUM, TRON

ETH_ADDR = "0x9858EfFD232B4033E47d90003D41EC34EcaEda94"
TRON_ADDR = "TUEZSdKsoDHQMeZwihtdoBiN46zxhGWYdH"


def make_book(tmp_path):
    return AddressBook(path=tmp_path / "addressbook.json")


def test_detect_chain():
    assert detect_chain(ETH_ADDR) == ETHEREUM
    assert detect_chain(TRON_ADDR) == TRON


def test_detect_chain_rejects_garbage():
    for bad in ("hello", "0x1234", "Tshort", ""):
        with pytest.raises(AddressBookError):
            detect_chain(bad)


def test_add_get_remove(tmp_path):
    book = make_book(tmp_path)
    contact = book.add("mom", TRON_ADDR)
    assert contact.chain == TRON
    assert book.get("mom").address == TRON_ADDR
    assert book.find_by_address(TRON_ADDR.lower()).alias == "mom"

    # persisted, reload works
    reloaded = make_book(tmp_path)
    assert reloaded.get("mom").address == TRON_ADDR

    reloaded.remove("mom")
    assert reloaded.entries() == []


def test_duplicate_alias_rejected(tmp_path):
    book = make_book(tmp_path)
    book.add("ex", ETH_ADDR)
    with pytest.raises(AddressBookError, match="already points"):
        book.add("ex", TRON_ADDR)


def test_invalid_alias_rejected(tmp_path):
    book = make_book(tmp_path)
    with pytest.raises(AddressBookError, match="Aliases"):
        book.add("has spaces", ETH_ADDR)
    with pytest.raises(AddressBookError, match="Aliases"):
        book.add("x" * 33, ETH_ADDR)


def test_invalid_address_rejected(tmp_path):
    book = make_book(tmp_path)
    with pytest.raises(AddressBookError, match="not a valid"):
        book.add("bad", "not-an-address")
    assert book.entries() == []


def test_remove_unknown_lists_known(tmp_path):
    book = make_book(tmp_path)
    book.add("mom", TRON_ADDR)
    with pytest.raises(AddressBookError, match="mom"):
        book.remove("ghost")


def test_for_chain(tmp_path):
    book = make_book(tmp_path)
    book.add("eth-guy", ETH_ADDR)
    book.add("tron-guy", TRON_ADDR)
    assert [c.alias for c in book.for_chain(ETHEREUM)] == ["eth-guy"]
    assert [c.alias for c in book.for_chain(TRON)] == ["tron-guy"]


def test_file_permissions(tmp_path):
    book = make_book(tmp_path)
    book.add("mom", TRON_ADDR)
    assert (book.path.stat().st_mode & 0o777) == 0o600

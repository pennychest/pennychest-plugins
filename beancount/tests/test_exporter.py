from datetime import date
from decimal import Decimal

from pennychest.export.base import (
    Ledger,
    LedgerAccount,
    LedgerBudget,
    LedgerPosting,
    LedgerTransaction,
)

from pennychest_beancount.exporter import BeancountExporter, account_names

BANK, GROCERIES, SALARY = 1, 2, 3


def _account(id_, path, kind):
    return LedgerAccount(
        id=id_,
        name=path.split(":")[-1],
        full_path=path,
        type=kind,
        currency="GBP",
        parent_id=None,
        bank_identifier=None,
    )


def _transaction(id_, day, description, account, category, amount, status="confirmed"):
    amount = Decimal(amount)
    return LedgerTransaction(
        id=id_,
        date=day,
        description=description,
        status=status,
        currency="GBP",
        postings=(
            LedgerPosting(id=id_ * 2, account_id=category, amount=amount),
            LedgerPosting(id=id_ * 2 + 1, account_id=account, amount=-amount),
        ),
    )


def _ledger():
    return Ledger(
        today=date(2026, 8, 15),
        accounts=(
            _account(BANK, "Assets:Bank:Current:123456_12345678", "asset"),
            _account(GROCERIES, "Expenses:Food:Groceries", "expense"),
            _account(SALARY, "Income:Salary", "income"),
        ),
        transactions=(
            _transaction(1, date(2026, 7, 1), 'MORRISONS "ST OUEN"', BANK, GROCERIES, "4.15"),
            _transaction(2, date(2026, 7, 31), "SALARY", BANK, SALARY, "-4193.38"),
            _transaction(
                3, date(2026, 8, 2), "COOP\\JERSEY", BANK, GROCERIES, "1.2345", status="pending"
            ),
        ),
        budgets=(
            LedgerBudget(
                account_id=GROCERIES,
                amount=Decimal("250"),
                period="monthly",
                created_on=date(2026, 8, 10),
            ),
        ),
    )


def test_account_names_follow_beancount_rules():
    names = account_names(
        [
            _account(1, "Assets:Bank:Current:123456_12345678", "asset"),
            _account(2, "Liabilities:CreditCard:1234", "liability"),
            _account(3, "Expenses:Giving:LocalCharity", "expense"),
            _account(4, "Expenses:food & drink", "expense"),
            _account(5, "Transfers:Pending", "asset"),
            _account(6, "Expenses:food-drink", "expense"),
            _account(7, "Expenses", "expense"),
        ]
    )
    assert names[1] == "Assets:Bank:Current:123456-12345678"
    assert names[2] == "Liabilities:CreditCard:1234"
    assert names[3] == "Expenses:Giving:LocalCharity"
    assert names[4] == "Expenses:Food-drink"
    assert names[5] == "Assets:Transfers:Pending"
    assert names[6] == "Expenses:Food-drink-2"
    assert names[7] == "Expenses:General"


def test_exports_the_ledger():
    text = BeancountExporter().export(_ledger())

    assert text.startswith("; Exported from PennyChest on 2026-08-15")
    assert 'option "operating_currency" "GBP"' in text
    assert (
        "2026-07-01 open Assets:Bank:Current:123456-12345678  ; Assets:Bank:Current:123456_12345678"
        in text
    )
    assert "2026-07-01 open Expenses:Food:Groceries" in text
    assert "2026-07-31 open Income:Salary" in text
    # Quotes and backslashes escaped; pending is "!"
    assert '2026-07-01 * "MORRISONS \\"ST OUEN\\""' in text
    assert '2026-08-02 ! "COOP\\\\JERSEY"' in text
    assert "  pennychest_id: 1" in text
    assert "  Expenses:Food:Groceries  4.15 GBP" in text
    assert "  Assets:Bank:Current:123456-12345678  -4.15 GBP" in text
    assert "  Income:Salary  -4193.38 GBP" in text
    # Exact amounts, so every transaction still balances
    assert "  Expenses:Food:Groceries  1.2345 GBP" in text
    assert "  Assets:Bank:Current:123456-12345678  -1.2345 GBP" in text
    # Dated from the month the budget was set
    assert '2026-08-01 custom "budget" Expenses:Food:Groceries "monthly" 250.00 GBP' in text


def test_empty_ledger():
    text = BeancountExporter().export(Ledger(date(2026, 8, 15), (), (), ()))
    assert 'option "operating_currency" "GBP"' in text

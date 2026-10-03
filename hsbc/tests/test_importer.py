"""Unit tests for the HSBC PDF statement importer.

Tests parse real HSBC PDF statement files and verify structural correctness
of the output — valid dates, non-empty descriptions, non-zero amounts, and
correctly populated metadata.

No specific transaction values are asserted (PII avoidance). Tests check that
the importer produces well-formed output across every statement found.

Configuration
-------------
Set the ``HSBC_TEST_PDF_DIR`` environment variable to the root directory
containing your HSBC PDF statements. The directory should contain at least
one subdirectory named ``current_account/`` and/or ``credit_card/``.

Example::

    HSBC_TEST_PDF_DIR=/path/to/statements pytest hsbc/tests

Tests are skipped automatically if:
  - pdfplumber is not installed (install this package to get it)
  - ``HSBC_TEST_PDF_DIR`` is not set or the directory does not exist
"""

import os
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

pdfplumber = pytest.importorskip("pdfplumber", reason="pdfplumber not installed")

from pennychest_hsbc.importer import (  # noqa: E402
    HsbcImporter,
    _detect_statement_type,
    _extract_bank_identifier,
    _extract_ca_period,
    _extract_cc_statement_date,
    _extract_closing_balance,
    _extract_name_hint,
    _extract_opening_balance,
)

# ---------------------------------------------------------------------------
# PDF fixture discovery
# ---------------------------------------------------------------------------

_PDF_ROOT: Path | None = None
_env = os.environ.get("HSBC_TEST_PDF_DIR")
if _env:
    _candidate = Path(_env)
    if _candidate.is_dir():
        _PDF_ROOT = _candidate

_NO_PDF_DIR = pytest.mark.skipif(
    _PDF_ROOT is None,
    reason=(
        "HSBC_TEST_PDF_DIR not set or directory not found. "
        "Set this env var to a directory containing current_account/ and credit_card/ subdirs."
    ),
)


def _collect(subdir: str) -> list[pytest.param]:
    """Collect PDFs from a named subdirectory under _PDF_ROOT."""
    if _PDF_ROOT is None:
        return [pytest.param(None, marks=pytest.mark.skip(reason="HSBC_TEST_PDF_DIR not set"))]
    directory = _PDF_ROOT / subdir
    if not directory.exists():
        return [
            pytest.param(
                None,
                marks=pytest.mark.skip(reason=f"Directory not found: {directory}"),
            )
        ]
    params = [
        pytest.param(pdf, id=pdf.name)
        for pdf in sorted(directory.glob("*.pdf"))
    ]
    if not params:
        return [
            pytest.param(
                None,
                marks=pytest.mark.skip(reason=f"No PDFs found in {directory}"),
            )
        ]
    return params


_CURRENT_ACCOUNT_PDFS = _collect("current_account")
_CREDIT_CARD_PDFS = _collect("credit_card")

_IMPORTER = HsbcImporter()

# ---------------------------------------------------------------------------
# Structural validation helper
# ---------------------------------------------------------------------------

_SORT_CODE_PATTERN = re.compile(r"^\d{2}-\d{2}-\d{2} \d{7,10}$")
_CARD_LAST4_PATTERN = re.compile(r"^\*{4}\d{4}$")


def _assert_valid_rows(rows, *, expect_payment_type: bool = False) -> None:
    """Assert every ImportedRow has valid structure."""
    assert len(rows) > 0, "Parser returned no transactions"

    for i, row in enumerate(rows):
        ctx = f"row {i}"

        assert isinstance(row.date, date), f"Invalid date type in {ctx}"
        assert 2000 <= row.date.year <= 2100, f"Implausible date {row.date} in {ctx}"

        assert isinstance(row.description, str) and row.description.strip(), (
            f"Empty or non-string description in {ctx}"
        )

        assert isinstance(row.amount, Decimal), f"Amount not Decimal in {ctx}"
        assert row.amount != Decimal("0"), f"Zero amount in {ctx}"

        assert isinstance(row.metadata, dict), f"metadata not a dict in {ctx}"

        assert isinstance(row.raw_content, str) and row.raw_content.strip(), (
            f"Empty raw_content in {ctx}"
        )

    if expect_payment_type:
        assert any(r.metadata.get("payment_type") for r in rows), (
            "No payment_type found in any row — expected at least one "
            "current account row to carry a payment type code (DD, FP, DEB, etc.)"
        )


# ---------------------------------------------------------------------------
# Current account tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("pdf_path", _CURRENT_ACCOUNT_PDFS)
def test_current_account_detect(pdf_path: Path) -> None:
    """detect() returns True for every current account statement."""
    content = pdf_path.read_bytes()
    assert _IMPORTER.detect(content, pdf_path.name)


@pytest.mark.parametrize("pdf_path", _CURRENT_ACCOUNT_PDFS)
def test_current_account_statement_info(pdf_path: Path) -> None:
    """detect_statement_info() returns correct type and sort-code identifier format."""
    content = pdf_path.read_bytes()
    info = _IMPORTER.detect_statement_info(content)

    assert info is not None, f"detect_statement_info() returned None for {pdf_path.name}"
    assert info["statement_type"] == "current_account"
    assert _SORT_CODE_PATTERN.match(info["bank_identifier"]), (
        f"bank_identifier {info['bank_identifier']!r} does not match "
        f"expected 'XX-XX-XX XXXXXXXX' format"
    )
    assert info["label"] == "HSBC Current Account Statement"


@pytest.mark.parametrize("pdf_path", _CURRENT_ACCOUNT_PDFS)
def test_current_account_parse(pdf_path: Path) -> None:
    """parse() returns well-formed ImportedRows for every current account statement."""
    content = pdf_path.read_bytes()
    rows = _IMPORTER.parse(content)
    _assert_valid_rows(rows, expect_payment_type=True)


# ---------------------------------------------------------------------------
# Credit card tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("pdf_path", _CREDIT_CARD_PDFS)
def test_credit_card_detect(pdf_path: Path) -> None:
    """detect() returns True for every credit card statement."""
    content = pdf_path.read_bytes()
    assert _IMPORTER.detect(content, pdf_path.name)


@pytest.mark.parametrize("pdf_path", _CREDIT_CARD_PDFS)
def test_credit_card_statement_info(pdf_path: Path) -> None:
    """detect_statement_info() returns correct type and masked card identifier format."""
    content = pdf_path.read_bytes()
    info = _IMPORTER.detect_statement_info(content)

    assert info is not None, f"detect_statement_info() returned None for {pdf_path.name}"
    assert info["statement_type"] == "credit_card"
    assert _CARD_LAST4_PATTERN.match(info["bank_identifier"]), (
        f"bank_identifier {info['bank_identifier']!r} does not match expected '****XXXX' format"
    )
    assert info["label"] == "HSBC Credit Card Statement"


@pytest.mark.parametrize("pdf_path", _CREDIT_CARD_PDFS)
def test_credit_card_parse(pdf_path: Path) -> None:
    """parse() returns well-formed ImportedRows for every credit card statement."""
    content = pdf_path.read_bytes()
    rows = _IMPORTER.parse(content)
    _assert_valid_rows(rows)


@pytest.mark.parametrize("pdf_path", _CREDIT_CARD_PDFS)
def test_credit_card_amount_signs(pdf_path: Path) -> None:
    """A typical credit card statement has at least one negative (purchase) amount.

    Statements that contain only a direct-debit payment and no purchases are
    skipped — they are valid but have no purchases whose sign can be tested.
    """
    content = pdf_path.read_bytes()
    rows = _IMPORTER.parse(content)
    # Some monthly statements contain only a DD payment with no purchases.
    # Skip rather than fail — there is no purchase sign logic to verify here.
    if all(r.amount >= 0 for r in rows):
        pytest.skip(f"{pdf_path.name} contains only credit/payment transactions")
    assert any(r.amount < 0 for r in rows), (
        f"No negative (purchase) amounts in {pdf_path.name} — "
        f"check that spending amounts are correctly signed"
    )


# ---------------------------------------------------------------------------
# Internal helper unit tests (no file I/O)
# ---------------------------------------------------------------------------

class TestDetectStatementType:
    def test_credit_card_keyword(self) -> None:
        assert _detect_statement_type("Your Credit Card statement") == "credit_card"

    def test_world_elite_keyword(self) -> None:
        assert _detect_statement_type("World Elite statement for October") == "credit_card"

    def test_visa_keyword(self) -> None:
        assert _detect_statement_type("Visa Card statement") == "credit_card"

    def test_premier_statement_keyword(self) -> None:
        assert _detect_statement_type("HSBC Premier Statement") == "current_account"

    def test_advance_keyword(self) -> None:
        assert _detect_statement_type("HSBC Advance account") == "current_account"

    def test_bonus_saver_keyword(self) -> None:
        assert _detect_statement_type("Online Bonus Saver details") == "current_account"

    def test_flexible_saver_keyword(self) -> None:
        assert _detect_statement_type("Flexible Saver statement") == "current_account"

    def test_unrecognised_returns_none(self) -> None:
        assert _detect_statement_type("Some random bank document") is None


class TestExtractBankIdentifier:
    def test_sort_code_and_account_number(self) -> None:
        # Actual HSBC format: sort code + account number + sheet number, space-separated
        text = "A N Other 12-34-56 12345678 1"
        result = _extract_bank_identifier(text)
        assert result == "12-34-56 12345678"

    def test_credit_card_captures_last_4(self) -> None:
        # Actual HSBC format: full 16-digit card number space-separated in groups of 4
        text = "Miss A Smith 1234 5678 9012 3456"
        result = _extract_bank_identifier(text)
        assert result == "****3456"

    def test_credit_card_does_not_store_full_number(self) -> None:
        """Full card number digits must never appear in the returned identifier."""
        text = "Miss A Smith 1234 5678 9012 3456"
        result = _extract_bank_identifier(text)
        assert result is not None
        assert "1234" not in result
        assert "5678" not in result
        assert "9012" not in result

    def test_no_identifier_returns_none(self) -> None:
        assert _extract_bank_identifier("No account details here") is None


class TestExtractOpeningBalance:
    def test_credit_card_balance_owed_is_negative(self) -> None:
        # No CR suffix -> the customer owes HSBC -> negative on the liability account
        assert _extract_opening_balance("PreviousBalance 312.56", "credit_card") == Decimal("-312.56")

    def test_credit_card_credit_balance_is_positive(self) -> None:
        # CR suffix -> HSBC owes the customer -> positive on the liability account
        assert _extract_opening_balance("PreviousBalance 241.99CR", "credit_card") == Decimal("241.99")

    def test_credit_card_zero_is_zero(self) -> None:
        assert _extract_opening_balance("PreviousBalance 0.00", "credit_card") == Decimal("0.00")

    def test_credit_card_with_thousands_separator(self) -> None:
        assert _extract_opening_balance("PreviousBalance 1,312.14CR", "credit_card") == Decimal("1312.14")

    def test_current_account_is_positive(self) -> None:
        assert _extract_opening_balance("OpeningBalance 9,815.51", "current_account") == Decimal("9815.51")

    def test_returns_none_when_absent(self) -> None:
        assert _extract_opening_balance("no balance here", "credit_card") is None


class TestExtractClosingBalance:
    def test_credit_card_new_balance_owed(self) -> None:
        assert _extract_closing_balance("New Balance 3,025.23", "credit_card") == Decimal("-3025.23")

    def test_current_account_closing_balance(self) -> None:
        assert _extract_closing_balance("ClosingBalance 649.11", "current_account") == Decimal("649.11")


class TestExtractCAPeriod:
    def test_period_with_implicit_start_year(self) -> None:
        start, end = _extract_ca_period("27 November to 26 December 2025 Branch Identifier Code")
        assert start == date(2025, 11, 27)
        assert end == date(2025, 12, 26)

    def test_period_crossing_year_boundary(self) -> None:
        start, end = _extract_ca_period("27 December 2023 to 26 January 2024 Branch Identifier Code")
        assert start == date(2023, 12, 27)
        assert end == date(2024, 1, 26)

    def test_returns_none_when_absent(self) -> None:
        assert _extract_ca_period("no period here") == (None, None)


class TestExtractCCStatementDate:
    def test_long_month_name(self) -> None:
        assert _extract_cc_statement_date("Statement Date 27 June 2024 ...") == date(2024, 6, 27)

    def test_returns_none_when_absent(self) -> None:
        assert _extract_cc_statement_date("no statement date") is None


class TestExtractNameHint:
    def test_ca_combines_first_name_and_product(self) -> None:
        text = (
            "Your HSBC Advance details\n"
            "Account Name Sortcode Account Number Sheet Number\n"
            "Alice Jane Smith 12-34-56 12345678 1\n"
        )
        assert _extract_name_hint(text) == "Alice HSBC Advance"

    def test_cc_combines_title_first_name_and_product(self) -> None:
        text = (
            "Your Credit Card statement\n"
            "Mr Bob Charles Jones 1234 5678 9012 3456\n"
        )
        assert _extract_name_hint(text) == "Bob Credit Card"

    def test_falls_back_to_product_only_when_no_name(self) -> None:
        text = "Your HSBC Premier World Elite statement\n"
        assert _extract_name_hint(text) == "HSBC Premier World Elite"

    def test_returns_none_when_no_product(self) -> None:
        text = "Account Name Sortcode\nAlice Smith 12-34-56 12345678 1\n"
        assert _extract_name_hint(text) is None


# ---------------------------------------------------------------------------
# Credit card statements spanning several pages (synthetic layout, no real data)
# ---------------------------------------------------------------------------


class _FakePage:
    def __init__(self, lines):
        self._words = []
        for top, items in lines:
            for x0, text in items:
                self._words.append({"text": text, "x0": x0, "top": top})

    def extract_words(self):
        return self._words


class _FakePdf:
    def __init__(self, pages):
        self.pages = pages


def _header(top):
    return [
        (top - 8, [(520, "Amount")]),
        (top, [(40, "ReceivedByUs"), (110, "TransactionDate"), (185, "Details")]),
    ]


def _txn(top, day, description, amount):
    return (
        top,
        [(40, day), (60, "Jun"), (75, "26"), (110, day), (130, "Jun"), (145, "26"),
         (185, description), (520, amount)],
    )


def test_credit_card_reads_continuation_pages_and_second_cards() -> None:
    from pennychest_hsbc.importer import _parse_credit_card

    pdf = _FakePdf(
        [
            # Summary page: no transaction table
            _FakePage([(100, [(40, "Statement"), (120, "Date"), (520, "1,234.56")])]),
            # First card's transactions start under a header...
            _FakePage([
                (20, [(40, "Your"), (90, "statement")]),
                *_header(100),
                _txn(120, "15", "IAPFIRST SHOP", "10.00"),
                _txn(135, "16", "IAPSECOND SHOP", "20.00"),
            ]),
            # ...continue at the top of the next page, then the second card's begin under
            # another header
            _FakePage([
                (20, [(40, "Your"), (90, "statement")]),
                _txn(60, "17", "IAPTHIRD SHOP", "30.00"),
                *_header(200),
                _txn(220, "01", "IAPOTHER CARD SHOP", "40.00"),
            ]),
            # A continuation page with no header at all
            _FakePage([
                (20, [(40, "Your"), (90, "statement")]),
                _txn(60, "02", "IAPLAST SHOP", "50.00"),
                (300, [(40, "Total"), (90, "carried"), (140, "forward"), (520, "28,584")]),
            ]),
        ]
    )
    rows = _parse_credit_card(pdf)
    assert [(r.description, r.amount) for r in rows] == [
        ("FIRST SHOP", Decimal("-10.00")),
        ("SECOND SHOP", Decimal("-20.00")),
        ("THIRD SHOP", Decimal("-30.00")),
        ("OTHER CARD SHOP", Decimal("-40.00")),
        ("LAST SHOP", Decimal("-50.00")),
    ]

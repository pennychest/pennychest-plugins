"""HSBC PDF statement importer.

Supports HSBC current account and credit card statements.
Uses pdfplumber for word-level position extraction — each word's (x, y)
coordinates are used to reconstruct the tabular layout without relying on
visual table borders (HSBC PDFs use space-aligned plain text, not bordered
tables, so extract_tables() returns nothing).

No personal data (names, card numbers) is hardcoded. Account identification
uses only the bank identifier extracted from the PDF being imported:
  - Current account: sort code + account number  (e.g. "12-34-56 12345678")
  - Credit card:     last 4 digits of card       (e.g. "****1234")
"""

from __future__ import annotations

import io
import re
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import pdfplumber
from pennychest.imports.base import BaseImporter, ImportedRow, StatementInfo

# Statement type detection keywords
_CREDIT_CARD_KEYWORDS = [
    "World Elite statement",
    "Credit Card",
    "Visa Card statement",
]
_CURRENT_ACCOUNT_KEYWORDS = [
    "Premier Statement",
    "Premier Bank Account",
    "HSBC Advance",
    "Online Bonus Saver",
    "Flexible Saver",
]

# Date format used by HSBC current account statements: "19 Dec 22"
_HSBC_DATE_FMT = "%d %b %y"
# Matches a current account date cell: "DD Mon YY"
_CA_DATE_RE = re.compile(r"^\d{1,2} \w{3} \d{2}$")
# Matches a credit card amount with optional CR suffix: "43.50" or "249.91CR"
_AMOUNT_RE = re.compile(r"^([0-9,]+\.\d{2})(CR)?$")
# Matches the opening / previous balance line on the statement summary page.
# Examples:
#   "OpeningBalance 9,815.51"          (current accounts / savings)
#   "PreviousBalance 312.56"           (credit cards, balance owed)
#   "PreviousBalance 241.99CR"         (credit cards, credit balance)
_OPENING_BALANCE_RE = re.compile(
    r"(?:Opening|Previous)Balance\s+([\d,]+\.\d{2})(CR)?", re.IGNORECASE
)
# Closing / new balance — same shape as opening.
_CLOSING_BALANCE_RE = re.compile(
    r"(?:Closing|New)\s*Balance\s+([\d,]+\.\d{2})(CR)?", re.IGNORECASE
)
# Current-account statement period. Examples:
#   "6 March to 26 March 2023"
#   "27 December 2023 to 26 January 2024"
#   "17 November 2022 to 16 May 2023"
# The start may omit the year; in that case it is taken from the end date.
_CA_PERIOD_RE = re.compile(
    r"(\d{1,2})\s+([A-Z][a-z]+)(?:\s+(\d{4}))?\s+to\s+"
    r"(\d{1,2})\s+([A-Z][a-z]+)\s+(\d{4})"
)
# Credit card statement date. Example: "Statement Date 27 June 2024"
_CC_STATEMENT_DATE_RE = re.compile(
    r"Statement Date\s+(\d{1,2})\s+([A-Z][a-z]+)\s+(\d{4})"
)
# Extracts product name from "Your <product> details/statement"
# Order matters: longer/more-specific strings must come before shorter prefixes.
_NAME_HINT_RE = re.compile(
    r"Your\s+"
    r"(HSBC\s+Premier\s+World\s+Elite"
    r"|HSBC\s+Premier\s+Bank\s+Account"
    r"|HSBC\s+(?:Premier|Advance)"
    r"|Online\s+Bonus\s+Saver"
    r"|Flexible\s+Saver"
    r"|Visa\s+Card"
    r"|Credit\s+Card)"
    r"(?:\s+(?:details|statement))?",
    re.IGNORECASE,
)


_CA_FIRST_NAME_RE = re.compile(
    r"Account Name\s+Sortcode[^\n]*\n\s*([A-Z][a-z]+)",
)
_CC_FIRST_NAME_RE = re.compile(
    r"\b(?:Miss|Mr|Mrs|Ms|Dr)\s+([A-Z][a-z]+)",
)


def _extract_name_hint(text: str) -> str | None:
    """Extract a suggested account name from the statement text.

    Combines the account holder's first name (extracted from the PDF at
    runtime) with the product name to produce hints like "Rebecca HSBC Advance"
    or "Alexander World Elite". The first name is read from the uploaded file —
    nothing is hardcoded in source.
    """
    product_match = _NAME_HINT_RE.search(text)
    if not product_match:
        return None
    product = re.sub(r"\s+", " ", product_match.group(1)).strip()

    # Try CA format first, then CC format
    name_match = _CA_FIRST_NAME_RE.search(text) or _CC_FIRST_NAME_RE.search(text)
    if name_match:
        return f"{name_match.group(1)} {product}"
    return product


def _extract_full_text(pdf: pdfplumber.PDF) -> str:
    """Concatenate text from all pages."""
    parts = []
    for page in pdf.pages:
        text = page.extract_text()
        if text:
            parts.append(text)
    return "\n".join(parts)


def _detect_statement_type(text: str) -> str | None:
    """Detect statement type from PDF text. Returns 'current_account', 'credit_card', or None."""
    for kw in _CREDIT_CARD_KEYWORDS:
        if kw in text:
            return "credit_card"
    for kw in _CURRENT_ACCOUNT_KEYWORDS:
        if kw in text:
            return "current_account"
    return None


def _extract_bank_identifier(text: str) -> str | None:
    """Extract account identifier from PDF text.

    Returns:
      "12-34-56 12345678"  for current accounts (sort code + account number)
      "****1234"           for credit cards (last 4 digits of card number)
    """
    # Current account: sort code + account number followed by sheet number
    m = re.search(r"(\d{2}-\d{2}-\d{2})\s+(\d{7,10})\s+\d+", text)
    if m:
        return f"{m.group(1)} {m.group(2)}"

    # Credit card: 16-digit card number — capture only last 4
    m = re.search(r"\d{4} \d{4} \d{4} (\d{4})", text)
    if m:
        return f"****{m.group(1)}"

    return None


def _parse_hsbc_date(value: str) -> date | None:
    """Parse HSBC current account date string "DD Mon YY" to a date object."""
    try:
        return datetime.strptime(value.strip(), _HSBC_DATE_FMT).date()
    except ValueError:
        return None


def _parse_long_date(day: str, month: str, year: str) -> date | None:
    """Parse a date with a long month name, e.g. "27 June 2024"."""
    try:
        return datetime.strptime(f"{int(day):02d} {month} {year}", "%d %B %Y").date()
    except ValueError:
        return None


def _signed_balance(amount_str: str, cr_suffix: str | None, statement_type: str) -> Decimal | None:
    """Convert a parsed balance string into a signed Decimal for posting use.

    Sign convention:
      - Asset accounts (current_account / savings): positive when in funds.
        The "CR" suffix is rare here but, when present, indicates an additional
        credit, so we keep the value positive.
      - Liability accounts (credit_card): negative when the customer owes
        money (no suffix), positive when there is a credit balance ("CR").
    """
    try:
        value = Decimal(amount_str.replace(",", ""))
    except InvalidOperation:
        return None
    is_credit = cr_suffix is not None
    if statement_type == "credit_card":
        return value if is_credit else -value
    return value


def _extract_opening_balance(text: str, statement_type: str) -> Decimal | None:
    m = _OPENING_BALANCE_RE.search(text)
    if not m:
        return None
    return _signed_balance(m.group(1), m.group(2), statement_type)


def _extract_closing_balance(text: str, statement_type: str) -> Decimal | None:
    m = _CLOSING_BALANCE_RE.search(text)
    if not m:
        return None
    return _signed_balance(m.group(1), m.group(2), statement_type)


def _extract_ca_period(text: str) -> tuple[date | None, date | None]:
    """Extract (period_start, period_end) from a current account / savings statement.

    The end date always carries the year; the start date may omit it (in which
    case it is the same as the end's year).
    """
    m = _CA_PERIOD_RE.search(text)
    if not m:
        return None, None
    s_day, s_mon, s_year, e_day, e_mon, e_year = m.groups()
    end = _parse_long_date(e_day, e_mon, e_year)
    start = _parse_long_date(s_day, s_mon, s_year or e_year)
    return start, end


def _extract_cc_statement_date(text: str) -> date | None:
    m = _CC_STATEMENT_DATE_RE.search(text)
    if not m:
        return None
    return _parse_long_date(*m.groups())


def _clean_amount(value: str) -> str:
    """Strip commas and asterisks from an amount string."""
    return value.replace(",", "").replace("*", "").replace(":", "").strip()


def _clean_description(desc: str) -> tuple[str, str | None]:
    """Strip payment method prefixes and return (clean_desc, payment_method).

    Detected methods: 'contactless', 'in-app', or None (standard card/other).
    """
    desc = desc.strip()

    if desc.startswith(")))"):
        method: str | None = "contactless"
        desc = re.sub(r"^\)\)\)\s*", "", desc)
    elif re.match(r"^(IAP|CIMOBILE)\s*", desc):
        method = "in-app"
        desc = re.sub(r"^(IAP|CIMOBILE)\s*", "", desc)
    else:
        method = None

    desc = re.sub(r"\s{2,}", " ", desc).strip()
    return desc, method


# ---------------------------------------------------------------------------
# Current account parser — word-position based
# ---------------------------------------------------------------------------

def _extract_ca_word_rows(
    page: pdfplumber.page.Page,
) -> list[tuple[str, str, str, str, str]]:
    """Extract (date, type, desc, paid_out, paid_in) string tuples from a current
    account statement page using the x-coordinate of each word.

    HSBC current account statements use a 6-column layout:
      Date | Type | Description | Paid Out | Paid In | Balance

    Column boundaries are derived from the header row ("Paidout"/"Paid out",
    "Paidin"/"Paid in", "Balance"), which is present on every transaction page.
    Older statements use merged "Paidout"/"Paidin"; newer statements use the
    split forms "Paid" + "out" / "Paid" + "in".
    """
    words = page.extract_words()
    if not words:
        return []

    # Find the column header row: look for 'Balance' at x > 450 to anchor
    # the header, then find 'Paidout'/'Paidin' (merged) or two 'Paid' words
    # (split form) on the same row.
    paidout_x = paidin_x = balance_x = header_top = None

    # Group words by row for header detection
    hdr_rows: dict[int, list] = defaultdict(list)
    for w in words:
        hdr_rows[round(w["top"] / 2) * 2].append(w)

    for top_key in sorted(hdr_rows):
        row_words = hdr_rows[top_key]
        balance_words = [w for w in row_words if w["text"] == "Balance" and w["x0"] > 450]
        if not balance_words:
            continue
        balance_x = balance_words[0]["x0"]
        header_top = float(top_key)

        # Try merged form first
        for w in row_words:
            if w["text"] == "Paidout" and w["x0"] > 300:
                paidout_x = w["x0"]
            elif w["text"] == "Paidin" and w["x0"] > 300:
                paidin_x = w["x0"]

        # Fall back to split form: two 'Paid' words at x > 300
        if paidout_x is None or paidin_x is None:
            paid_xs = sorted(
                w["x0"] for w in row_words if w["text"] == "Paid" and w["x0"] > 300
            )
            if len(paid_xs) >= 2:
                paidout_x = paid_xs[0]
                paidin_x = paid_xs[1]
            elif len(paid_xs) == 1 and paidout_x is None:
                paidout_x = paid_xs[0]

        if paidout_x is not None and paidin_x is not None:
            break

    if not all(v is not None for v in [paidout_x, paidin_x, balance_x, header_top]):
        return []

    # Column thresholds derived from the header positions.
    # The "Date" header is at ~53px; the payment type abbreviations (VIS, )))…)
    # sit at ~112px; descriptions start at ~139px.  These are stable ratios
    # relative to the paid-out column across all observed statement versions.
    type_x = paidout_x * 0.31   # ~110 when paidout_x ≈ 357
    desc_x = paidout_x * 0.39   # ~139 when paidout_x ≈ 357

    # Only process words below the header row
    txn_words = [w for w in words if w["top"] > header_top + 3]

    # Group words by visual row: round top to nearest 3px to handle alignment jitter
    rows_by_top: dict[int, list] = defaultdict(list)
    for w in txn_words:
        row_key = round(w["top"] / 3) * 3
        rows_by_top[row_key].append(w)

    result: list[tuple[str, str, str, str, str]] = []
    for top_key in sorted(rows_by_top):
        row_words = sorted(rows_by_top[top_key], key=lambda w: w["x0"])

        date_parts: list[str] = []
        type_parts: list[str] = []
        desc_parts: list[str] = []
        out_parts: list[str] = []
        in_parts: list[str] = []

        for w in row_words:
            x = w["x0"]
            t = w["text"]
            if x < type_x:
                date_parts.append(t)
            elif x < desc_x:
                type_parts.append(t)
            elif x < paidout_x:
                desc_parts.append(t)
            elif x < paidin_x - 5:
                # Subtract tolerance: paid_in amounts may start slightly left of the
                # column header x-position due to right-alignment within the column.
                out_parts.append(t)
            elif x < balance_x:
                in_parts.append(t)
            # Balance column: ignore (we don't store running balance)

        result.append((
            " ".join(date_parts),
            " ".join(type_parts),
            " ".join(desc_parts),
            " ".join(out_parts),
            " ".join(in_parts),
        ))

    return result


def _apply_ca_state_machine(
    rows: list[tuple[str, str, str, str, str]],
) -> list[ImportedRow]:
    """Apply the current account state machine to (date, type, desc, out, in) rows.

    Transactions may span multiple rows (multi-line descriptions).  The state
    machine processes all rows in order, flushing whenever a new transaction
    starts.  All pages are passed in as a single stream so that cross-page
    transactions (where the date is on the previous page) are handled correctly.
    """
    result: list[ImportedRow] = []

    current_date: date | None = None
    current_type: str | None = None
    current_desc_parts: list[str] = []
    current_paid_out: str = ""
    current_paid_in: str = ""
    current_raw_parts: list[str] = []

    def flush() -> None:
        nonlocal current_type, current_desc_parts
        nonlocal current_paid_out, current_paid_in, current_raw_parts

        if current_date is not None and current_type is not None:
            if current_paid_in:
                raw_amount = current_paid_in
                sign = Decimal("1")
            elif current_paid_out:
                raw_amount = current_paid_out
                sign = Decimal("-1")
            else:
                # Transaction with no amount — skip silently
                current_type = None
                current_desc_parts = []
                current_paid_out = ""
                current_paid_in = ""
                current_raw_parts = []
                return

            try:
                amount = Decimal(_clean_amount(raw_amount)) * sign
            except InvalidOperation:
                current_type = None
                current_desc_parts = []
                current_paid_out = ""
                current_paid_in = ""
                current_raw_parts = []
                return

            description, payment_method = _clean_description(
                " ".join(current_desc_parts)
            )

            if description:
                meta: dict[str, str] = {"payment_type": current_type}
                if payment_method:
                    meta["payment_method"] = payment_method

                result.append(
                    ImportedRow(
                        date=current_date,
                        description=description,
                        amount=amount,
                        raw_content=" | ".join(current_raw_parts),
                        metadata=meta,
                    )
                )

        current_type = None
        current_desc_parts = []
        current_paid_out = ""
        current_paid_in = ""
        current_raw_parts = []

    for date_str, type_str, desc_str, paid_out_str, paid_in_str in rows:
        # Skip balance markers
        combined = date_str + " " + desc_str
        if "BALANCEBROUGHTFORWARD" in combined or "BALANCECARRIEDFORWARD" in desc_str:
            flush()
            continue

        # Skip stray single-character marker rows (e.g. "A") and blank rows
        if date_str in ("A",) and not type_str and not desc_str:
            continue
        if not any([date_str, type_str, desc_str, paid_out_str, paid_in_str]):
            continue

        raw_line = " | ".join([date_str, type_str, desc_str, paid_out_str, paid_in_str])
        has_date = bool(_CA_DATE_RE.match(date_str))

        if has_date:
            flush()
            current_date = _parse_hsbc_date(date_str)
            if type_str:
                current_type = type_str
            if desc_str:
                current_desc_parts.append(desc_str)
            if paid_out_str:
                current_paid_out = paid_out_str
            if paid_in_str:
                current_paid_in = paid_in_str
            current_raw_parts.append(raw_line)

        elif type_str and not date_str:
            # New transaction sharing the current date (or cross-page continuation).
            # current_date is intentionally preserved from the previous date line.
            flush()
            current_type = type_str
            if desc_str:
                current_desc_parts.append(desc_str)
            if paid_out_str:
                current_paid_out = paid_out_str
            if paid_in_str:
                current_paid_in = paid_in_str
            current_raw_parts.append(raw_line)

        elif not date_str and not type_str:
            # Continuation row: additional description text and/or amount
            if desc_str and desc_str != ".":
                current_desc_parts.append(desc_str)
            if paid_out_str:
                current_paid_out = paid_out_str
            if paid_in_str:
                current_paid_in = paid_in_str
            if desc_str or paid_out_str or paid_in_str:
                current_raw_parts.append(raw_line)

    flush()
    return result


def _parse_current_account(pdf: pdfplumber.PDF) -> list[ImportedRow]:
    """Parse HSBC current account statement into ImportedRow objects.

    Processes all pages as a single stream so that transactions which start
    on one page and have their amount on the next are handled correctly.
    """
    all_rows: list[tuple[str, str, str, str, str]] = []
    for page in pdf.pages:
        all_rows.extend(_extract_ca_word_rows(page))
    return _apply_ca_state_machine(all_rows)


# ---------------------------------------------------------------------------
# Credit card parser — word-position based
# ---------------------------------------------------------------------------

def _find_cc_header(
    words: list[dict],
) -> tuple[float, float, float, float, float] | None:
    """Find CC transaction column x-positions from the header row.

    Returns (recv_x, txn_x, desc_x, amount_x, header_top) or None if this
    page has no CC transaction table.

    The HSBC credit card header row contains:
      ReceivedByUs | TransactionDate | Details | (Amount on slightly different row)
    """
    recv_x: float | None = None
    txn_x: float | None = None
    desc_x: float | None = None
    amount_x: float | None = None
    header_top: float | None = None

    for w in words:
        if w["text"] == "ReceivedByUs":
            recv_x = w["x0"]
            header_top = w["top"]
        elif w["text"] == "TransactionDate":
            txn_x = w["x0"]
        elif w["text"] == "Amount" and w["x0"] > 400:
            # The 'Amount' column header is on a slightly different row from
            # ReceivedByUs; find it independently.
            amount_x = w["x0"]

    if recv_x is None or txn_x is None or amount_x is None or header_top is None:
        return None

    # 'Details' column header appears on the same row as ReceivedByUs
    for w in words:
        if (
            w["text"] == "Details"
            and abs(w["top"] - header_top) < 2
            and w["x0"] > txn_x + 50
        ):
            desc_x = w["x0"]
            break

    if desc_x is None:
        # Fallback: derive from the consistent relationship txn_x * 1.66 ≈ 195
        desc_x = txn_x * 1.66

    return recv_x, txn_x, desc_x, amount_x, header_top


def _parse_cc_date_tokens(tokens: list[str]) -> date | None:
    """Parse a CC date from 2–3 word tokens.

    HSBC credit card statements use two date formats:
      - Merged:   ["12", "Dec22"]   → day + 3-letter-month + 2-digit-year combined
      - Separate: ["03", "Jan", "23"] → day + month + year as separate words
    Both are normalised to a date by joining tokens, then matching DDMonYY.
    """
    if not tokens:
        return None
    raw = "".join(tokens)  # Remove spaces: "12Dec22" or "03Jan23"
    m = re.match(r"^(\d{1,2})([A-Za-z]{3})(\d{2})$", raw)
    if m:
        d, mon, y = m.groups()
        try:
            return datetime.strptime(f"{int(d):02d} {mon} {y}", "%d %b %y").date()
        except ValueError:
            return None
    return None


def _parse_credit_card_amount(amount_str: str) -> tuple[Decimal, bool] | None:
    """Parse credit card amount string. Returns (amount, is_credit) or None."""
    m = _AMOUNT_RE.match(amount_str.strip())
    if not m:
        return None
    try:
        value = Decimal(m.group(1).replace(",", ""))
        is_credit = bool(m.group(2))  # "CR" suffix means credit (refund/payment)
        return value, is_credit
    except InvalidOperation:
        return None


def _extract_cc_first_received_date(pdf: pdfplumber.PDF) -> date | None:
    """Return the earliest "Received By Us" date in a credit card statement.

    Used to derive period_start for credit card statements (the PDF prints
    only the statement_date / period_end, not the start of the period).
    Transactions are listed in ascending received-date order, so the first
    valid transaction row gives us what we need.
    """
    for page in pdf.pages:
        words = page.extract_words()
        if not words:
            continue
        header = _find_cc_header(words)
        if header is None:
            continue
        recv_x, txn_x, _desc_x, _amount_x, header_top = header

        txn_words = [w for w in words if w["top"] > header_top + 3]
        rows_by_top: dict[int, list] = defaultdict(list)
        for w in txn_words:
            row_key = round(w["top"] / 3) * 3
            rows_by_top[row_key].append(w)

        for top_key in sorted(rows_by_top):
            row_words = sorted(rows_by_top[top_key], key=lambda w: w["x0"])
            recv_tokens = [w["text"] for w in row_words if w["x0"] < txn_x - 10]
            recv_date = _parse_cc_date_tokens(recv_tokens)
            if recv_date:
                return recv_date
    return None


def _parse_credit_card(pdf: pdfplumber.PDF) -> list[ImportedRow]:
    """Parse HSBC credit card statement into ImportedRow objects.

    Each transaction occupies one visual row with two date columns (received
    date, transaction date), a description, and an amount.  The layout is
    derived from the page's column header positions.
    """
    rows: list[ImportedRow] = []
    # Column positions carry over between pages: continuation pages have no header row, and
    # a page can finish one card's transactions above the header of the next card's.
    columns: tuple[float, float, float, float] | None = None

    for page in pdf.pages:
        words = page.extract_words()
        if not words:
            continue

        header = _find_cc_header(words)
        if header is not None:
            columns = header[:4]
        if columns is None:
            continue

        recv_x, txn_x, desc_x, amount_x = columns

        # Every row on the page: headings, totals and notes are skipped below because they
        # don't start with a date and end with an amount.
        rows_by_top: dict[int, list] = defaultdict(list)
        for w in words:
            row_key = round(w["top"] / 3) * 3
            rows_by_top[row_key].append(w)

        for top_key in sorted(rows_by_top):
            row_words = sorted(rows_by_top[top_key], key=lambda w: w["x0"])

            recv_tokens: list[str] = []
            txn_tokens: list[str] = []
            desc_parts: list[str] = []
            amount_parts: list[str] = []

            for w in row_words:
                x = w["x0"]
                t = w["text"]
                # Apply a 10px tolerance to all CC column boundaries.  Right-aligned
                # numbers and description text can start a few pixels left of the
                # column header's x-position due to PDF layout variance.
                if x < txn_x - 10:
                    recv_tokens.append(t)
                elif x < desc_x - 10:
                    txn_tokens.append(t)
                elif x < amount_x - 10:
                    desc_parts.append(t)
                else:
                    amount_parts.append(t)

            # A transaction row must have at least a received date and an amount
            if not recv_tokens or not amount_parts:
                continue

            amount_str = " ".join(amount_parts)
            parsed_amount = _parse_credit_card_amount(amount_str)
            if parsed_amount is None:
                continue

            recv_date = _parse_cc_date_tokens(recv_tokens)
            txn_date = _parse_cc_date_tokens(txn_tokens) if txn_tokens else None

            # Use received date as transaction date if transaction date is missing/invalid
            if txn_date is None:
                txn_date = recv_date
            if txn_date is None:
                continue

            value, is_credit = parsed_amount
            # Credits (refunds, payments to card) are positive; purchases are negative
            amount = value if is_credit else -value

            desc = " ".join(desc_parts)
            description, payment_method = _clean_description(desc)
            if not description:
                continue

            meta: dict[str, str] = {}
            if payment_method:
                meta["payment_method"] = payment_method
            if recv_date and recv_date != txn_date:
                meta["received_date"] = recv_date.isoformat()

            raw = (
                f"{' '.join(recv_tokens)} | {' '.join(txn_tokens)} | {desc} | {amount_str}"
            )

            rows.append(
                ImportedRow(
                    date=txn_date,
                    description=description,
                    amount=amount,
                    raw_content=raw,
                    metadata=meta,
                )
            )

    return rows


# ---------------------------------------------------------------------------
# Main importer class
# ---------------------------------------------------------------------------

class HsbcImporter(BaseImporter):
    """HSBC PDF statement importer.

    Handles both HSBC current account and credit card statements.
    Detects statement type by keyword matching and dispatches to the
    appropriate parser. No personal data is hardcoded.
    """

    name = "hsbc"
    label = "HSBC PDF statements"
    description = "HSBC bank statement importer (current account and credit card)"
    file_types = ["pdf"]

    def detect(self, file_content: bytes, filename: str = "") -> bool:
        """Return True if this looks like an HSBC PDF statement."""
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext != "pdf":
            return False
        try:
            with pdfplumber.open(io.BytesIO(file_content)) as pdf:
                # Only scan first two pages for speed
                for page in pdf.pages[:2]:
                    text = page.extract_text() or ""
                    if "hsbc" in text.lower():
                        return True
        except Exception:
            return False
        return False

    def detect_statement_info(self, file_content: bytes) -> StatementInfo | None:
        """Extract statement type and bank identifier without importing.

        Returns a StatementInfo dict, or None if the PDF is not a recognised
        HSBC statement.
        """
        try:
            with pdfplumber.open(io.BytesIO(file_content)) as pdf:
                text = _extract_full_text(pdf)

                statement_type = _detect_statement_type(text)
                if statement_type is None:
                    return None

                bank_identifier = _extract_bank_identifier(text)
                if bank_identifier is None:
                    return None

                opening_balance = _extract_opening_balance(text, statement_type)
                closing_balance = _extract_closing_balance(text, statement_type)

                if statement_type == "credit_card":
                    period_end = _extract_cc_statement_date(text)
                    period_start = _extract_cc_first_received_date(pdf)
                else:
                    period_start, period_end = _extract_ca_period(text)
        except Exception:
            return None

        label = (
            "HSBC Credit Card Statement"
            if statement_type == "credit_card"
            else "HSBC Current Account Statement"
        )

        return StatementInfo(
            statement_type=statement_type,
            bank_identifier=bank_identifier,
            label=label,
            name_hint=_extract_name_hint(text),
            period_start=period_start,
            period_end=period_end,
            opening_balance=opening_balance,
            closing_balance=closing_balance,
        )

    def parse(self, file_content: bytes) -> list[ImportedRow]:
        """Parse an HSBC PDF statement and return a list of ImportedRow objects."""
        try:
            with pdfplumber.open(io.BytesIO(file_content)) as pdf:
                text = _extract_full_text(pdf)
                statement_type = _detect_statement_type(text)

                if statement_type is None:
                    raise ValueError(
                        "Could not detect HSBC statement type. "
                        "Expected a current account or credit card statement."
                    )

                if statement_type == "credit_card":
                    rows = _parse_credit_card(pdf)
                else:
                    rows = _parse_current_account(pdf)

        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(f"Failed to parse HSBC PDF: {exc}") from exc

        if not rows:
            raise ValueError("No transactions found in HSBC statement.")

        return rows

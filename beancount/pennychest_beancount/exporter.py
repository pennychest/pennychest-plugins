"""The ledger in Beancount's plain-text format (https://beancount.github.io).

PennyChest is already double-entry, so each transaction maps directly: its postings become
Beancount postings with the same amounts. Account paths are adapted to Beancount's naming rules,
confirmed transactions are flagged `*` and pending ones `!`, and budgets are written as Fava
`custom "budget"` entries.
"""

import re
from collections.abc import Iterable
from datetime import date
from decimal import Decimal

from pennychest.export.base import BaseExporter, Ledger, LedgerAccount

ROOTS = {
    "asset": "Assets",
    "liability": "Liabilities",
    "equity": "Equity",
    "income": "Income",
    "expense": "Expenses",
}
# PennyChest budget periods, as Fava names them
PERIODS = {"monthly": "monthly", "annual": "yearly", "yearly": "yearly", "weekly": "weekly"}


def _component(part: str) -> str:
    """One part of an account name: starts with a capital letter or digit, then letters, digits
    and hyphens."""
    part = re.sub(r"[^A-Za-z0-9-]+", "-", part).strip("-") or "X"
    return part[0].upper() + part[1:]


def account_names(accounts: Iterable[LedgerAccount]) -> dict[int, str]:
    """Beancount names for PennyChest accounts, unique and valid. An account under a top level
    Beancount doesn't know is placed under the root for its type."""
    names: dict[int, str] = {}
    used: set[str] = set()
    for account in sorted(accounts, key=lambda a: a.full_path):
        parts = [_component(p) for p in account.full_path.split(":")]
        root = ROOTS[account.type]
        if parts[0] != root:
            parts = [root, *parts] if parts[0] not in ROOTS.values() else [root, *parts[1:]]
        if len(parts) == 1:
            # Beancount accounts need at least two parts; a posting straight to "Expenses"
            # lands in "Expenses:General"
            parts.append("General")
        name = ":".join(parts)
        candidate, n = name, 2
        while candidate in used:
            candidate, n = f"{name}-{n}", n + 1
        used.add(candidate)
        names[account.id] = candidate
    return names


def _quote(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _amount(value: Decimal) -> str:
    """Exact, with at least two decimal places, so every transaction still balances."""
    value = Decimal(value)
    if value == value.quantize(Decimal("0.01")):
        return f"{value.quantize(Decimal('0.01')):f}"
    return f"{value.normalize():f}"


def _currency(code: str | None) -> str:
    code = (code or "GBP").upper()
    return code if re.fullmatch(r"[A-Z][A-Z0-9'._-]{0,22}[A-Z0-9]", code) else "GBP"


def render(ledger: Ledger) -> str:
    today = ledger.today
    accounts = ledger.accounts
    names = account_names(accounts)
    transactions = ledger.transactions

    first_use: dict[int, date] = {}
    for txn in transactions:
        for posting in txn.postings:
            if posting.account_id not in first_use or txn.date < first_use[posting.account_id]:
                first_use[posting.account_id] = txn.date
    earliest = min(first_use.values(), default=today)

    currencies = sorted({_currency(t.currency) for t in transactions}) or ["GBP"]
    lines = [
        f"; Exported from PennyChest on {today.isoformat()}",
        f"; {len(transactions)} transactions. '*' is confirmed, '!' is still to be reviewed.",
        "",
        'option "title" "PennyChest"',
        *[f'option "operating_currency" "{c}"' for c in currencies],
        "",
    ]

    for account in sorted(accounts, key=lambda a: names[a.id]):
        if ":" not in account.full_path and account.id not in first_use:
            continue  # top-level groups ("Expenses") only exist in Beancount through their children
        opened = first_use.get(account.id, earliest)
        comment = f"  ; {account.full_path}" if names[account.id] != account.full_path else ""
        lines.append(f"{opened.isoformat()} open {names[account.id]}{comment}")
    lines.append("")

    if ledger.budgets:
        lines.append("; Budgets, as Fava reads them")
        for budget in sorted(ledger.budgets, key=lambda b: names[b.account_id]):
            created = budget.created_on or today
            start = max(created.replace(day=1), first_use.get(budget.account_id, earliest))
            lines.append(
                f'{start.isoformat()} custom "budget" {names[budget.account_id]} '
                f'"{PERIODS.get(budget.period, budget.period)}" {_amount(budget.amount)} GBP'
            )
        lines.append("")

    for txn in transactions:
        flag = "*" if txn.status == "confirmed" else "!"
        currency = _currency(txn.currency)
        lines.append(f"{txn.date.isoformat()} {flag} {_quote(txn.description)}")
        lines.append(f"  pennychest_id: {txn.id}")
        for posting in txn.postings:
            lines.append(f"  {names[posting.account_id]}  {_amount(posting.amount)} {currency}")
        lines.append("")
    return "\n".join(lines)



class BeancountExporter(BaseExporter):
    name = "beancount"
    label = "Beancount ledger"
    description = (
        "Every transaction in Beancount's plain-text double-entry format, with your accounts, "
        "categories and budgets. Open it in Fava or query it with bean-query. Confirmed "
        "transactions are marked *, ones still to review !."
    )
    file_extension = "beancount"
    media_type = "text/plain; charset=utf-8"

    def export(self, ledger: Ledger) -> str:
        return render(ledger)

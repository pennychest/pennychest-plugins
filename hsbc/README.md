# HSBC statements

Imports HSBC PDF statements into PennyChest: current accounts, savings and credit cards.

- Recognises an HSBC statement from the PDF, and whether it's a current account or a credit
  card from its wording.
- Reads the account from the statement itself: the sort code and account number, or a card's
  last four digits (never the full card number).
- Reads the statement's period and opening and closing balances, so PennyChest can suggest
  the account and its opening balance.
- Reads every transaction, including descriptions over several lines and transactions that
  carry over to the next page. Contactless and in-app payments are noted as such.

It runs in PennyChest's sandbox: it sees only the statement it's sent.

## How it reads statements

HSBC statements are text laid out in columns, with no table borders. The importer reads where
each word is on the page, finds the column headings ("Paid out", "Paid in", "Balance", or
"Received by us", "Transaction date", "Details", "Amount"), and puts each word in its column.

## Testing

```sh
cargo test -p hsbc
```

The tests use made-up statements in HSBC's layout (`tests/fixtures`, made by
`make_statements.py`). To check against real statements as well, put them in
`current_account/` and `credit_card/` folders somewhere outside the repository and run

```sh
HSBC_TEST_PDF_DIR=/path/to/statements cargo test -p hsbc
```

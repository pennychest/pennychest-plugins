# pennychest-hsbc

A [PennyChest](https://github.com/pennychest/pennychest) importer for HSBC CIIOM PDF statements: current accounts
(Advance, Premier, savers) and credit cards. It reads each statement's sort code and account
number, or the card's last four digits, so the import page can suggest the right account, and
picks up the statement period and opening and closing balances.

## Install

See the [repository README](../README.md#installing-a-plugin). The requirement is

```
git+https://github.com/pennychest/pennychest-plugins@hsbc-v0.1.0#subdirectory=hsbc
```

The import page then lists "HSBC PDF statements" among the supported formats.

## Tests

```sh
pip install -e ../pennychest/backend -e "hsbc[dev]"
pytest hsbc/tests
```

Most tests run against real statements, which aren't in the repository. Point
`HSBC_TEST_PDF_DIR` at a directory with `current_account/` and `credit_card/` subdirectories of
your own PDFs to run them; without it they're skipped.

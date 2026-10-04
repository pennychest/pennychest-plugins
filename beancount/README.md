# pennychest-beancount

A [PennyChest](https://github.com/pennychest/pennychest) exporter that writes the whole
ledger in [Beancount](https://beancount.github.io)'s plain-text format, with your accounts,
categories and budgets (as Fava `custom "budget"` entries). Open the file in
[Fava](https://beancount.github.io/fava/) or query it with bean-query.

PennyChest is already double-entry, so each transaction maps directly onto Beancount postings
with the same amounts. Confirmed transactions are flagged `*` and ones still to review `!`.
Account names are adapted to Beancount's rules (e.g. `Assets:Bank:Current:123456_12345678`
becomes `Assets:Bank:Current:123456-12345678`, with the original kept as a comment).

## Install

See the [repository README](../README.md#installing-a-plugin). The requirement is

```
git+https://github.com/pennychest/pennychest-plugins@<tag-or-commit>#subdirectory=beancount
```

Settings → Export then offers "Beancount ledger".

## Tests

```sh
pip install -e ../pennychest/backend -e "beancount[dev]"
pytest beancount/tests
```

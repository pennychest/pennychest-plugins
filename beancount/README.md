# Beancount

Exports the PennyChest ledger in [Beancount](https://beancount.github.io)'s plain-text
double-entry format, to open in [Fava](https://beancount.github.io/fava/) or query with
`bean-query`.

- Every account, opened on the date of its first transaction. Account names are adapted to
  Beancount's rules (each part starts with a capital letter or digit, then letters, digits
  and hyphens); where a name changes, the PennyChest path follows as a comment.
- Every transaction, with its postings and exact amounts, so each still balances. Confirmed
  transactions are flagged `*`, ones still to review `!`, and each carries its
  `pennychest_id`.
- Budgets, as Fava's `custom "budget"` entries, from the month each was set.

It runs in PennyChest's sandbox: it sees the ledger it's sent, and nothing else.

## Testing

```sh
cargo test -p beancount
```

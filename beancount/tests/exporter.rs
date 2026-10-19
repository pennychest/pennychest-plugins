use std::str::FromStr;

use beancount::{Beancount, account_names, render};
use pennychest_plugin::{
    Decimal, Exporter, Ledger, LedgerAccount, LedgerBudget, LedgerPosting, LedgerTransaction,
    NaiveDate,
};

const BANK: i64 = 1;
const GROCERIES: i64 = 2;
const SALARY: i64 = 3;

fn date(y: i32, m: u32, d: u32) -> NaiveDate {
    NaiveDate::from_ymd_opt(y, m, d).unwrap()
}

fn account(id: i64, path: &str, kind: &str) -> LedgerAccount {
    LedgerAccount {
        id,
        name: path.rsplit(':').next().unwrap().to_owned(),
        full_path: path.to_owned(),
        kind: kind.to_owned(),
        currency: "GBP".to_owned(),
        parent_id: None,
        bank_identifier: None,
    }
}

fn transaction(
    id: i64,
    day: NaiveDate,
    description: &str,
    account: i64,
    category: i64,
    amount: &str,
    status: &str,
) -> LedgerTransaction {
    let amount = Decimal::from_str(amount).unwrap();
    LedgerTransaction {
        id,
        date: day,
        description: description.to_owned(),
        status: status.to_owned(),
        currency: "GBP".to_owned(),
        postings: vec![
            LedgerPosting {
                id: id * 2,
                account_id: category,
                amount,
            },
            LedgerPosting {
                id: id * 2 + 1,
                account_id: account,
                amount: -amount,
            },
        ],
    }
}

fn ledger() -> Ledger {
    Ledger {
        today: date(2026, 8, 15),
        accounts: vec![
            account(BANK, "Assets:Bank:Current:123456_12345678", "asset"),
            account(GROCERIES, "Expenses:Food:Groceries", "expense"),
            account(SALARY, "Income:Salary", "income"),
        ],
        transactions: vec![
            transaction(
                1,
                date(2026, 7, 1),
                "MORRISONS \"ST OUEN\"",
                BANK,
                GROCERIES,
                "4.15",
                "confirmed",
            ),
            transaction(
                2,
                date(2026, 7, 31),
                "SALARY",
                BANK,
                SALARY,
                "-4193.38",
                "confirmed",
            ),
            transaction(
                3,
                date(2026, 8, 2),
                "COOP\\JERSEY",
                BANK,
                GROCERIES,
                "1.2345",
                "pending",
            ),
        ],
        budgets: vec![LedgerBudget {
            account_id: GROCERIES,
            amount: Decimal::from_str("250").unwrap(),
            period: "monthly".to_owned(),
            created_on: Some(date(2026, 8, 10)),
        }],
    }
}

#[test]
fn account_names_follow_beancount_rules() {
    let names = account_names(&[
        account(1, "Assets:Bank:Current:123456_12345678", "asset"),
        account(2, "Liabilities:CreditCard:1234", "liability"),
        account(3, "Expenses:Giving:LocalCharity", "expense"),
        account(4, "Expenses:food & drink", "expense"),
        account(5, "Transfers:Pending", "asset"),
        account(6, "Expenses:food-drink", "expense"),
        account(7, "Expenses", "expense"),
    ]);
    assert_eq!(names[&1], "Assets:Bank:Current:123456-12345678");
    assert_eq!(names[&2], "Liabilities:CreditCard:1234");
    assert_eq!(names[&3], "Expenses:Giving:LocalCharity");
    assert_eq!(names[&4], "Expenses:Food-drink");
    assert_eq!(names[&5], "Assets:Transfers:Pending");
    assert_eq!(names[&6], "Expenses:Food-drink-2");
    assert_eq!(names[&7], "Expenses:General");
}

#[test]
fn exports_the_ledger() {
    let text = String::from_utf8(Beancount.export(&ledger()).unwrap()).unwrap();
    assert!(text.starts_with("; Exported from PennyChest on 2026-08-15"));
    let expected = [
        "option \"operating_currency\" \"GBP\"",
        "2026-07-01 open Assets:Bank:Current:123456-12345678  ; Assets:Bank:Current:123456_12345678",
        "2026-07-01 open Expenses:Food:Groceries",
        "2026-07-31 open Income:Salary",
        // Quotes and backslashes escaped; pending is "!".
        "2026-07-01 * \"MORRISONS \\\"ST OUEN\\\"\"",
        "2026-08-02 ! \"COOP\\\\JERSEY\"",
        "  pennychest_id: 1",
        "  Expenses:Food:Groceries  4.15 GBP",
        "  Assets:Bank:Current:123456-12345678  -4.15 GBP",
        "  Income:Salary  -4193.38 GBP",
        // Exact amounts, so every transaction still balances.
        "  Expenses:Food:Groceries  1.2345 GBP",
        "  Assets:Bank:Current:123456-12345678  -1.2345 GBP",
        // Dated from the month the budget was set.
        "2026-08-01 custom \"budget\" Expenses:Food:Groceries \"monthly\" 250.00 GBP",
    ];
    for line in expected {
        assert!(text.contains(line), "missing {line:?} in\n{text}");
    }
}

#[test]
fn the_whole_file() {
    let expected = r#"; Exported from PennyChest on 2026-08-15
; 3 transactions. '*' is confirmed, '!' is still to be reviewed.

option "title" "PennyChest"
option "operating_currency" "GBP"

2026-07-01 open Assets:Bank:Current:123456-12345678  ; Assets:Bank:Current:123456_12345678
2026-07-01 open Expenses:Food:Groceries
2026-07-31 open Income:Salary

; Budgets, as Fava reads them
2026-08-01 custom "budget" Expenses:Food:Groceries "monthly" 250.00 GBP

2026-07-01 * "MORRISONS \"ST OUEN\""
  pennychest_id: 1
  Expenses:Food:Groceries  4.15 GBP
  Assets:Bank:Current:123456-12345678  -4.15 GBP

2026-07-31 * "SALARY"
  pennychest_id: 2
  Income:Salary  -4193.38 GBP
  Assets:Bank:Current:123456-12345678  4193.38 GBP

2026-08-02 ! "COOP\\JERSEY"
  pennychest_id: 3
  Expenses:Food:Groceries  1.2345 GBP
  Assets:Bank:Current:123456-12345678  -1.2345 GBP
"#;
    assert_eq!(render(&ledger()), expected);
}

#[test]
fn an_empty_ledger() {
    let empty = Ledger {
        today: date(2026, 8, 15),
        accounts: vec![],
        transactions: vec![],
        budgets: vec![],
    };
    assert!(render(&empty).contains("option \"operating_currency\" \"GBP\""));
}

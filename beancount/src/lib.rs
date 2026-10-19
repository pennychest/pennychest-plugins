//! The ledger in Beancount's plain-text format (<https://beancount.github.io>).
//!
//! PennyChest is already double-entry, so each transaction maps straight across: its postings
//! become Beancount postings with the same amounts. Account paths are adapted to Beancount's
//! naming rules, confirmed transactions are flagged `*` and pending ones `!`, and budgets are
//! written as Fava `custom "budget"` entries.

use std::collections::{BTreeSet, HashMap, HashSet};
use std::sync::LazyLock;

use chrono::Datelike;
use pennychest_plugin::{Decimal, Exporter, ExporterInfo, Ledger, LedgerAccount, NaiveDate};
use regex::Regex;

/// Beancount's top-level account for each PennyChest account type.
fn root(kind: &str) -> &'static str {
    match kind {
        "asset" => "Assets",
        "liability" => "Liabilities",
        "equity" => "Equity",
        "income" => "Income",
        _ => "Expenses",
    }
}

const ROOTS: [&str; 5] = ["Assets", "Liabilities", "Equity", "Income", "Expenses"];

/// PennyChest's budget periods, as Fava names them.
fn period(period: &str) -> &str {
    match period {
        "monthly" => "monthly",
        "annual" | "yearly" => "yearly",
        "weekly" => "weekly",
        other => other,
    }
}

static NOT_NAME: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[^A-Za-z0-9-]+").unwrap());
static WHITESPACE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\s+").unwrap());
static CURRENCY: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^[A-Z][A-Z0-9'._-]{0,22}[A-Z0-9]$").unwrap());

/// One part of an account name: a capital letter or digit, then letters, digits and hyphens.
fn component(part: &str) -> String {
    let replaced = NOT_NAME.replace_all(part, "-");
    let trimmed = replaced.trim_matches('-');
    let part = if trimmed.is_empty() { "X" } else { trimmed };
    let mut chars = part.chars();
    let first = chars.next().expect("not empty");
    first.to_uppercase().chain(chars).collect()
}

/// Beancount names for PennyChest's accounts, unique and valid. An account under a top level
/// Beancount doesn't know goes under the root for its type.
pub fn account_names(accounts: &[LedgerAccount]) -> HashMap<i64, String> {
    let mut sorted: Vec<&LedgerAccount> = accounts.iter().collect();
    sorted.sort_by(|a, b| a.full_path.cmp(&b.full_path));
    let mut names = HashMap::new();
    let mut used = HashSet::new();
    for account in sorted {
        let mut parts: Vec<String> = account.full_path.split(':').map(component).collect();
        let root = root(&account.kind);
        if parts[0] != root {
            if ROOTS.contains(&parts[0].as_str()) {
                parts[0] = root.to_owned();
            } else {
                parts.insert(0, root.to_owned());
            }
        }
        if parts.len() == 1 {
            // Beancount accounts need two parts: a posting straight to "Expenses" lands in
            // "Expenses:General".
            parts.push("General".to_owned());
        }
        let name = parts.join(":");
        let mut candidate = name.clone();
        let mut n = 2;
        while used.contains(&candidate) {
            candidate = format!("{name}-{n}");
            n += 1;
        }
        used.insert(candidate.clone());
        names.insert(account.id, candidate);
    }
    names
}

fn quote(text: &str) -> String {
    let text = WHITESPACE.replace_all(text, " ");
    format!(
        "\"{}\"",
        text.trim().replace('\\', "\\\\").replace('"', "\\\"")
    )
}

/// Exact, with at least two decimal places, so every transaction still balances.
fn amount(value: Decimal) -> String {
    if value.round_dp(2) == value {
        format!("{:.2}", value)
    } else {
        value.normalize().to_string()
    }
}

fn currency(code: &str) -> String {
    let code = if code.is_empty() {
        "GBP".to_owned()
    } else {
        code.to_uppercase()
    };
    if CURRENCY.is_match(&code) {
        code
    } else {
        "GBP".to_owned()
    }
}

fn first_of_month(date: NaiveDate) -> NaiveDate {
    date.with_day(1).expect("every month has a first")
}

pub fn render(ledger: &Ledger) -> String {
    let today = ledger.today;
    let names = account_names(&ledger.accounts);
    let transactions = &ledger.transactions;

    let mut first_use: HashMap<i64, NaiveDate> = HashMap::new();
    for txn in transactions {
        for posting in &txn.postings {
            let first = first_use.entry(posting.account_id).or_insert(txn.date);
            if txn.date < *first {
                *first = txn.date;
            }
        }
    }
    let earliest = first_use.values().min().copied().unwrap_or(today);

    let mut currencies: Vec<String> = transactions
        .iter()
        .map(|t| currency(&t.currency))
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect();
    if currencies.is_empty() {
        currencies.push("GBP".into());
    }
    let mut lines = vec![
        format!("; Exported from PennyChest on {today}"),
        format!(
            "; {} transactions. '*' is confirmed, '!' is still to be reviewed.",
            transactions.len()
        ),
        String::new(),
        "option \"title\" \"PennyChest\"".to_owned(),
    ];
    lines.extend(
        currencies
            .iter()
            .map(|c| format!("option \"operating_currency\" \"{c}\"")),
    );
    lines.push(String::new());

    let mut accounts: Vec<&LedgerAccount> = ledger.accounts.iter().collect();
    accounts.sort_by(|a, b| names[&a.id].cmp(&names[&b.id]));
    for account in accounts {
        // Top-level groups ("Expenses") only exist in Beancount through their children.
        if !account.full_path.contains(':') && !first_use.contains_key(&account.id) {
            continue;
        }
        let opened = first_use.get(&account.id).copied().unwrap_or(earliest);
        let name = &names[&account.id];
        let comment = if *name == account.full_path {
            String::new()
        } else {
            format!("  ; {}", account.full_path)
        };
        lines.push(format!("{opened} open {name}{comment}"));
    }
    lines.push(String::new());

    if !ledger.budgets.is_empty() {
        lines.push("; Budgets, as Fava reads them".to_owned());
        let mut budgets: Vec<_> = ledger.budgets.iter().collect();
        budgets.sort_by(|a, b| names[&a.account_id].cmp(&names[&b.account_id]));
        for budget in budgets {
            let created = budget.created_on.unwrap_or(today);
            let used = first_use
                .get(&budget.account_id)
                .copied()
                .unwrap_or(earliest);
            let start = first_of_month(created).max(used);
            lines.push(format!(
                "{start} custom \"budget\" {} \"{}\" {} GBP",
                names[&budget.account_id],
                period(&budget.period),
                amount(budget.amount)
            ));
        }
        lines.push(String::new());
    }

    for txn in transactions {
        let flag = if txn.status == "confirmed" { "*" } else { "!" };
        let currency = currency(&txn.currency);
        lines.push(format!("{} {flag} {}", txn.date, quote(&txn.description)));
        lines.push(format!("  pennychest_id: {}", txn.id));
        for posting in &txn.postings {
            lines.push(format!(
                "  {}  {} {currency}",
                names[&posting.account_id],
                amount(posting.amount)
            ));
        }
        lines.push(String::new());
    }
    lines.join("\n")
}

pub struct Beancount;

impl Exporter for Beancount {
    fn info(&self) -> ExporterInfo {
        ExporterInfo::new(
            "beancount",
            "Beancount ledger",
            "beancount",
            "text/plain; charset=utf-8",
        )
        .describe(
            "Every transaction in Beancount's plain-text double-entry format, with your \
             accounts, categories and budgets. Open it in Fava or query it with bean-query. \
             Confirmed transactions are marked *, ones still to review !.",
        )
    }

    fn export(&self, ledger: &Ledger) -> Result<Vec<u8>, String> {
        Ok(render(ledger).into_bytes())
    }
}

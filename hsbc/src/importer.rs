//! HSBC PDF statements: current accounts (and savings) and credit cards.
//!
//! Each word's position rebuilds the table, because HSBC statements are space-aligned text
//! with no borders (see [`crate::pdf`]).
//!
//! No personal data is hardcoded. The account is identified only from the statement itself:
//! a current account by sort code and account number ("12-34-56 12345678"), a credit card by
//! its last four digits ("****1234"). The full card number is never kept.

use std::collections::BTreeMap;
use std::str::FromStr;
use std::sync::LazyLock;

use pennychest_plugin::{Decimal, Importer, ImporterInfo, NaiveDate, Row, StatementInfo};
use regex::Regex;
use serde_json::{Map, Value};

use crate::pdf::{self, Page, Word};

const CREDIT_CARD_KEYWORDS: [&str; 3] = [
    "World Elite statement",
    "Credit Card",
    "Visa Card statement",
];
const CURRENT_ACCOUNT_KEYWORDS: [&str; 5] = [
    "Premier Statement",
    "Premier Bank Account",
    "HSBC Advance",
    "Online Bonus Saver",
    "Flexible Saver",
];

fn regex(pattern: &str) -> Regex {
    Regex::new(pattern).expect("the pattern is valid")
}

/// A current account date cell: "19 Dec 22".
static CA_DATE: LazyLock<Regex> = LazyLock::new(|| regex(r"^\d{1,2} \w{3} \d{2}$"));
/// A credit card amount, "CR" for a credit: "43.50" or "249.91CR".
static AMOUNT: LazyLock<Regex> = LazyLock::new(|| regex(r"^([0-9,]+\.\d{2})(CR)?$"));
/// "OpeningBalance 9,815.51", "PreviousBalance 312.56" or "PreviousBalance 241.99CR".
static OPENING_BALANCE: LazyLock<Regex> =
    LazyLock::new(|| regex(r"(?i)(?:Opening|Previous)Balance\s+([\d,]+\.\d{2})(CR)?"));
static CLOSING_BALANCE: LazyLock<Regex> =
    LazyLock::new(|| regex(r"(?i)(?:Closing|New)\s*Balance\s+([\d,]+\.\d{2})(CR)?"));
/// "6 March to 26 March 2023" or "27 December 2023 to 26 January 2024": the start may leave
/// out the year, which is then the end's.
static CA_PERIOD: LazyLock<Regex> = LazyLock::new(|| {
    regex(r"(\d{1,2})\s+([A-Z][a-z]+)(?:\s+(\d{4}))?\s+to\s+(\d{1,2})\s+([A-Z][a-z]+)\s+(\d{4})")
});
/// "Statement Date 27 June 2024".
static CC_STATEMENT_DATE: LazyLock<Regex> =
    LazyLock::new(|| regex(r"Statement Date\s+(\d{1,2})\s+([A-Z][a-z]+)\s+(\d{4})"));
/// The product in "Your <product> details/statement"; longer names first.
static NAME_HINT: LazyLock<Regex> = LazyLock::new(|| {
    regex(
        r"(?i)Your\s+(HSBC\s+Premier\s+World\s+Elite|HSBC\s+Premier\s+Bank\s+Account|HSBC\s+(?:Premier|Advance)|Online\s+Bonus\s+Saver|Flexible\s+Saver|Visa\s+Card|Credit\s+Card)(?:\s+(?:details|statement))?",
    )
});
static CA_FIRST_NAME: LazyLock<Regex> =
    LazyLock::new(|| regex(r"Account Name\s+Sortcode[^\n]*\n\s*([A-Z][a-z]+)"));
static CC_FIRST_NAME: LazyLock<Regex> =
    LazyLock::new(|| regex(r"\b(?:Miss|Mr|Mrs|Ms|Dr)\s+([A-Z][a-z]+)"));
/// Sort code, account number, then the sheet number.
static SORT_CODE: LazyLock<Regex> =
    LazyLock::new(|| regex(r"(\d{2}-\d{2}-\d{2})\s+(\d{7,10})\s+\d+"));
static CARD_NUMBER: LazyLock<Regex> = LazyLock::new(|| regex(r"\d{4} \d{4} \d{4} (\d{4})"));
static WHITESPACE_RUN: LazyLock<Regex> = LazyLock::new(|| regex(r"\s{2,}"));
static IN_APP: LazyLock<Regex> = LazyLock::new(|| regex(r"^(IAP|CIMOBILE)\s*"));
static CONTACTLESS: LazyLock<Regex> = LazyLock::new(|| regex(r"^\)\)\)\s*"));
static CC_DATE: LazyLock<Regex> = LazyLock::new(|| regex(r"^(\d{1,2})([A-Za-z]{3})(\d{2})$"));

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StatementType {
    CurrentAccount,
    CreditCard,
}

impl StatementType {
    fn as_str(self) -> &'static str {
        match self {
            Self::CurrentAccount => "current_account",
            Self::CreditCard => "credit_card",
        }
    }
}

pub fn detect_statement_type(text: &str) -> Option<StatementType> {
    if CREDIT_CARD_KEYWORDS.iter().any(|k| text.contains(k)) {
        return Some(StatementType::CreditCard);
    }
    if CURRENT_ACCOUNT_KEYWORDS.iter().any(|k| text.contains(k)) {
        return Some(StatementType::CurrentAccount);
    }
    None
}

pub fn extract_bank_identifier(text: &str) -> Option<String> {
    if let Some(m) = SORT_CODE.captures(text) {
        return Some(format!("{} {}", &m[1], &m[2]));
    }
    CARD_NUMBER.captures(text).map(|m| format!("****{}", &m[1]))
}

/// A suggested account name: the holder's first name and the product, e.g. "Alice HSBC
/// Advance", read from the statement.
pub fn extract_name_hint(text: &str) -> Option<String> {
    let product = NAME_HINT.captures(text)?;
    let product = product[1].split_whitespace().collect::<Vec<_>>().join(" ");
    let name = CA_FIRST_NAME
        .captures(text)
        .or_else(|| CC_FIRST_NAME.captures(text));
    Some(match name {
        Some(name) => format!("{} {product}", &name[1]),
        None => product,
    })
}

/// "19 Dec 22", as current account statements write dates. Years 69–99 are the 1900s.
fn parse_short_date(day: &str, month: &str, year: &str) -> Option<NaiveDate> {
    let day: u32 = day.parse().ok()?;
    let month = month_number(month, true)?;
    let year: i32 = year.parse().ok()?;
    let year = if year >= 69 { 1900 + year } else { 2000 + year };
    NaiveDate::from_ymd_opt(year, month, day)
}

fn parse_hsbc_date(value: &str) -> Option<NaiveDate> {
    let parts: Vec<&str> = value.split_whitespace().collect();
    match parts[..] {
        [day, month, year] if year.len() == 2 => parse_short_date(day, month, year),
        _ => None,
    }
}

/// "27 June 2024", with the month written out.
fn parse_long_date(day: &str, month: &str, year: &str) -> Option<NaiveDate> {
    NaiveDate::from_ymd_opt(
        year.parse().ok()?,
        month_number(month, false)?,
        day.parse().ok()?,
    )
}

const MONTHS: [&str; 12] = [
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
];

/// A month by name, ignoring case: its first three letters if `short`, else written out.
fn month_number(name: &str, short: bool) -> Option<u32> {
    let name = name.to_lowercase();
    let i = MONTHS.iter().position(|m| {
        if short {
            name.len() == 3 && m.starts_with(&name)
        } else {
            *m == name
        }
    })?;
    u32::try_from(i + 1).ok()
}

fn decimal(text: &str) -> Option<Decimal> {
    Decimal::from_str(&text.replace(',', "")).ok()
}

/// A balance, signed for posting: an asset account's is positive in funds; a credit card's is
/// negative when money is owed, positive with "CR".
fn signed_balance(amount: &str, credit: bool, kind: StatementType) -> Option<Decimal> {
    let value = decimal(amount)?;
    Some(match kind {
        StatementType::CreditCard if !credit => -value,
        _ => value,
    })
}

fn balance(pattern: &Regex, text: &str, kind: StatementType) -> Option<Decimal> {
    let m = pattern.captures(text)?;
    signed_balance(&m[1], m.get(2).is_some(), kind)
}

pub fn extract_opening_balance(text: &str, kind: StatementType) -> Option<Decimal> {
    balance(&OPENING_BALANCE, text, kind)
}

pub fn extract_closing_balance(text: &str, kind: StatementType) -> Option<Decimal> {
    balance(&CLOSING_BALANCE, text, kind)
}

pub fn extract_ca_period(text: &str) -> (Option<NaiveDate>, Option<NaiveDate>) {
    let Some(m) = CA_PERIOD.captures(text) else {
        return (None, None);
    };
    let end = parse_long_date(&m[4], &m[5], &m[6]);
    let start_year = m.get(3).map_or(&m[6], |y| y.as_str());
    (parse_long_date(&m[1], &m[2], start_year), end)
}

pub fn extract_cc_statement_date(text: &str) -> Option<NaiveDate> {
    let m = CC_STATEMENT_DATE.captures(text)?;
    parse_long_date(&m[1], &m[2], &m[3])
}

fn clean_amount(value: &str) -> String {
    value.replace([',', '*', ':'], "").trim().to_owned()
}

/// The description without its payment method prefix, and the method: "contactless" for
/// ")))", "in-app" for "IAP" or "CIMOBILE".
fn clean_description(description: &str) -> (String, Option<&'static str>) {
    let description = description.trim();
    let (rest, method) = if description.starts_with(")))") {
        (
            CONTACTLESS.replace(description, "").into_owned(),
            Some("contactless"),
        )
    } else if IN_APP.is_match(description) {
        (IN_APP.replace(description, "").into_owned(), Some("in-app"))
    } else {
        (description.to_owned(), None)
    };
    (
        WHITESPACE_RUN.replace_all(&rest, " ").trim().to_owned(),
        method,
    )
}

/// Words grouped into visual rows: by `top` rounded to the nearest multiple of `step`, in order
/// down the page, each row left to right.
fn rows_by_top<'a>(words: impl IntoIterator<Item = &'a Word>, step: f64) -> Vec<Vec<&'a Word>> {
    let mut rows: BTreeMap<i64, Vec<&Word>> = BTreeMap::new();
    for word in words {
        rows.entry(round_half_even(word.top / step) * step as i64)
            .or_default()
            .push(word);
    }
    rows.into_values()
        .map(|mut row| {
            row.sort_by(|a, b| a.x0.total_cmp(&b.x0));
            row
        })
        .collect()
}

/// Rounds to the nearest whole number, and halves to even.
fn round_half_even(value: f64) -> i64 {
    let rounded = value.round();
    let result = if (value - value.trunc()).abs() == 0.5 && rounded % 2.0 != 0.0 {
        rounded - value.signum()
    } else {
        rounded
    };
    result as i64
}

fn join(words: &[&str]) -> String {
    words.join(" ")
}

// Current accounts ---------------------------------------------------------------------------

/// (date, type, description, paid out, paid in) for each row of a current account page's
/// table, the columns placed by its header row ("Paidout"/"Paid out", "Paidin"/"Paid in",
/// "Balance").
type CaRow = (String, String, String, String, String);

fn ca_word_rows(page: &Page) -> Vec<CaRow> {
    let words = &page.words;
    if words.is_empty() {
        return Vec::new();
    }
    let (mut paidout_x, mut paidin_x, mut balance_x, mut header_top) = (None, None, None, None);
    for row in rows_by_top(words, 2.0) {
        let Some(balance) = row.iter().find(|w| w.text == "Balance" && w.x0 > 450.0) else {
            continue;
        };
        balance_x = Some(balance.x0);
        header_top = Some((round_half_even(row[0].top / 2.0) * 2) as f64);
        for w in &row {
            if w.text == "Paidout" && w.x0 > 300.0 {
                paidout_x = Some(w.x0);
            } else if w.text == "Paidin" && w.x0 > 300.0 {
                paidin_x = Some(w.x0);
            }
        }
        if paidout_x.is_none() || paidin_x.is_none() {
            let mut paid: Vec<f64> = row
                .iter()
                .filter(|w| w.text == "Paid" && w.x0 > 300.0)
                .map(|w| w.x0)
                .collect();
            paid.sort_by(f64::total_cmp);
            if paid.len() >= 2 {
                paidout_x = Some(paid[0]);
                paidin_x = Some(paid[1]);
            } else if paid.len() == 1 && paidout_x.is_none() {
                paidout_x = Some(paid[0]);
            }
        }
        if paidout_x.is_some() && paidin_x.is_some() {
            break;
        }
    }
    let (Some(paidout_x), Some(paidin_x), Some(balance_x), Some(header_top)) =
        (paidout_x, paidin_x, balance_x, header_top)
    else {
        return Vec::new();
    };
    // The date, type and description columns, in proportion to the paid-out column, which
    // holds across every statement version seen.
    let type_x = paidout_x * 0.31;
    let desc_x = paidout_x * 0.39;

    let below = words.iter().filter(|w| w.top > header_top + 3.0);
    rows_by_top(below, 3.0)
        .into_iter()
        .map(|row| {
            let (mut date, mut kind, mut desc, mut out, mut paid_in) =
                (vec![], vec![], vec![], vec![], vec![]);
            for w in row {
                let x = w.x0;
                let t = w.text.as_str();
                if x < type_x {
                    date.push(t);
                } else if x < desc_x {
                    kind.push(t);
                } else if x < paidout_x {
                    desc.push(t);
                } else if x < paidin_x - 5.0 {
                    // Paid-in amounts can start a little left of their header.
                    out.push(t);
                } else if x < balance_x {
                    paid_in.push(t);
                }
            }
            (
                join(&date),
                join(&kind),
                join(&desc),
                join(&out),
                join(&paid_in),
            )
        })
        .collect()
}

/// Turns current account rows into transactions. A transaction can run over several rows,
/// and onto the next page, so every page's rows come through together.
fn ca_transactions(rows: &[CaRow]) -> Vec<Row> {
    #[derive(Default)]
    struct Current {
        kind: Option<String>,
        desc: Vec<String>,
        paid_out: String,
        paid_in: String,
        raw: Vec<String>,
    }

    let mut result = Vec::new();
    let mut date: Option<NaiveDate> = None;
    let mut current = Current::default();

    let flush = |current: &mut Current, date: Option<NaiveDate>, result: &mut Vec<Row>| {
        let taken = std::mem::take(current);
        let (Some(date), Some(kind)) = (date, taken.kind) else {
            return;
        };
        let (raw_amount, sign) = if !taken.paid_in.is_empty() {
            (&taken.paid_in, Decimal::ONE)
        } else if !taken.paid_out.is_empty() {
            (&taken.paid_out, Decimal::NEGATIVE_ONE)
        } else {
            return;
        };
        let Ok(amount) = Decimal::from_str(&clean_amount(raw_amount)) else {
            return;
        };
        let (description, method) = clean_description(&taken.desc.join(" "));
        if description.is_empty() {
            return;
        }
        let mut metadata = Map::new();
        metadata.insert("payment_type".into(), Value::String(kind));
        if let Some(method) = method {
            metadata.insert("payment_method".into(), Value::String(method.into()));
        }
        result.push(Row {
            date,
            description,
            amount: amount * sign,
            raw_content: taken.raw.join(" | "),
            line_number: None,
            metadata,
        });
    };

    for (date_str, type_str, desc_str, out_str, in_str) in rows {
        let combined = format!("{date_str} {desc_str}");
        if combined.contains("BALANCEBROUGHTFORWARD") || desc_str.contains("BALANCECARRIEDFORWARD")
        {
            flush(&mut current, date, &mut result);
            continue;
        }
        // A stray marker ("A") on its own, or an empty row.
        if date_str == "A" && type_str.is_empty() && desc_str.is_empty() {
            continue;
        }
        if [date_str, type_str, desc_str, out_str, in_str]
            .iter()
            .all(|s| s.is_empty())
        {
            continue;
        }
        let raw_line = [date_str, type_str, desc_str, out_str, in_str]
            .map(String::as_str)
            .join(" | ");

        if CA_DATE.is_match(date_str) {
            flush(&mut current, date, &mut result);
            date = parse_hsbc_date(date_str);
            if !type_str.is_empty() {
                current.kind = Some(type_str.clone());
            }
        } else if !type_str.is_empty() && date_str.is_empty() {
            // Another transaction on the same date, or carried over from the last page.
            flush(&mut current, date, &mut result);
            current.kind = Some(type_str.clone());
        } else if date_str.is_empty() && type_str.is_empty() {
            // More of the description, or the amount.
            if !desc_str.is_empty() && desc_str != "." {
                current.desc.push(desc_str.clone());
            }
            if !out_str.is_empty() {
                current.paid_out.clone_from(out_str);
            }
            if !in_str.is_empty() {
                current.paid_in.clone_from(in_str);
            }
            if !desc_str.is_empty() || !out_str.is_empty() || !in_str.is_empty() {
                current.raw.push(raw_line);
            }
            continue;
        } else {
            continue;
        }
        if !desc_str.is_empty() {
            current.desc.push(desc_str.clone());
        }
        if !out_str.is_empty() {
            current.paid_out.clone_from(out_str);
        }
        if !in_str.is_empty() {
            current.paid_in.clone_from(in_str);
        }
        current.raw.push(raw_line);
    }
    flush(&mut current, date, &mut result);
    result
}

fn parse_current_account(pages: &[Page]) -> Vec<Row> {
    let rows: Vec<CaRow> = pages.iter().flat_map(ca_word_rows).collect();
    ca_transactions(&rows)
}

// Credit cards -------------------------------------------------------------------------------

/// Where the columns of a credit card page's table start, from its header: "ReceivedByUs"
/// (whose top is the header's), "TransactionDate", "Details" and "Amount" (a little above the
/// others).
struct CcHeader {
    txn_x: f64,
    desc_x: f64,
    amount_x: f64,
    top: f64,
}

fn find_cc_header(words: &[Word]) -> Option<CcHeader> {
    let (mut recv, mut txn, mut amount) = (None, None, None);
    for w in words {
        match w.text.as_str() {
            "ReceivedByUs" => recv = Some((w.x0, w.top)),
            "TransactionDate" => txn = Some(w.x0),
            "Amount" if w.x0 > 400.0 => amount = Some(w.x0),
            _ => {}
        }
    }
    let ((_, top), txn_x, amount_x) = (recv?, txn?, amount?);
    let desc_x = words
        .iter()
        .find(|w| w.text == "Details" && (w.top - top).abs() < 2.0 && w.x0 > txn_x + 50.0)
        .map_or(txn_x * 1.66, |w| w.x0);
    Some(CcHeader {
        txn_x,
        desc_x,
        amount_x,
        top,
    })
}

/// "12", "Dec22" or "03", "Jan", "23": joined up, then read as day, month, year.
fn parse_cc_date(tokens: &[&str]) -> Option<NaiveDate> {
    let joined = tokens.concat();
    let m = CC_DATE.captures(&joined)?;
    parse_short_date(&m[1], &m[2], &m[3])
}

fn parse_cc_amount(text: &str) -> Option<(Decimal, bool)> {
    let m = AMOUNT.captures(text.trim())?;
    Some((decimal(&m[1])?, m.get(2).is_some()))
}

/// The first "received by us" date, the start of a credit card statement's period, which
/// the statement only gives the end of.
fn first_received_date(pages: &[Page]) -> Option<NaiveDate> {
    for page in pages {
        let Some(header) = find_cc_header(&page.words) else {
            continue;
        };
        let below = page.words.iter().filter(|w| w.top > header.top + 3.0);
        for row in rows_by_top(below, 3.0) {
            let recv: Vec<&str> = row
                .iter()
                .filter(|w| w.x0 < header.txn_x - 10.0)
                .map(|w| w.text.as_str())
                .collect();
            if let Some(date) = parse_cc_date(&recv) {
                return Some(date);
            }
        }
    }
    None
}

fn parse_credit_card(pages: &[Page]) -> Vec<Row> {
    let mut rows = Vec::new();
    // Columns carry over between pages: continuation pages have no header, and a page can
    // end one card's transactions above the next card's header.
    let mut columns: Option<(f64, f64, f64)> = None;
    for page in pages {
        if page.words.is_empty() {
            continue;
        }
        if let Some(header) = find_cc_header(&page.words) {
            columns = Some((header.txn_x, header.desc_x, header.amount_x));
        }
        let Some((txn_x, desc_x, amount_x)) = columns else {
            continue;
        };
        // Every row on the page: headings, totals and notes are skipped because they don't
        // start with a date and end with an amount.
        for row in rows_by_top(&page.words, 3.0) {
            let (mut recv, mut txn, mut desc, mut amount) = (vec![], vec![], vec![], vec![]);
            for w in row {
                let (x, t) = (w.x0, w.text.as_str());
                // 10pt of tolerance: right-aligned numbers and text can start a little left
                // of their header.
                if x < txn_x - 10.0 {
                    recv.push(t);
                } else if x < desc_x - 10.0 {
                    txn.push(t);
                } else if x < amount_x - 10.0 {
                    desc.push(t);
                } else {
                    amount.push(t);
                }
            }
            if recv.is_empty() || amount.is_empty() {
                continue;
            }
            let amount_str = join(&amount);
            let Some((value, credit)) = parse_cc_amount(&amount_str) else {
                continue;
            };
            let recv_date = parse_cc_date(&recv);
            let Some(date) = (if txn.is_empty() {
                None
            } else {
                parse_cc_date(&txn)
            })
            .or(recv_date) else {
                continue;
            };
            let desc_str = join(&desc);
            let (description, method) = clean_description(&desc_str);
            if description.is_empty() {
                continue;
            }
            let mut metadata = Map::new();
            if let Some(method) = method {
                metadata.insert("payment_method".into(), Value::String(method.into()));
            }
            if let Some(received) = recv_date.filter(|r| *r != date) {
                metadata.insert(
                    "received_date".into(),
                    Value::String(received.format("%Y-%m-%d").to_string()),
                );
            }
            rows.push(Row {
                date,
                description,
                // Credits (refunds, payments) are money in; purchases money out.
                amount: if credit { value } else { -value },
                raw_content: format!(
                    "{} | {} | {desc_str} | {amount_str}",
                    join(&recv),
                    join(&txn)
                ),
                line_number: None,
                metadata,
            });
        }
    }
    rows
}

// The importer -------------------------------------------------------------------------------

fn full_text(pages: &[Page]) -> String {
    pages
        .iter()
        .map(Page::text)
        .filter(|t| !t.is_empty())
        .collect::<Vec<_>>()
        .join("\n")
}

pub struct Hsbc;

impl Importer for Hsbc {
    fn info(&self) -> ImporterInfo {
        ImporterInfo::new("hsbc", "HSBC PDF statements", &["pdf"])
            .describe("HSBC bank statement importer (current account and credit card)")
            .detects()
            .with_statement_info()
    }

    fn detect(&self, file: &[u8], filename: &str) -> bool {
        let is_pdf = filename
            .rsplit_once('.')
            .is_some_and(|(_, ext)| ext.eq_ignore_ascii_case("pdf"));
        if !is_pdf {
            return false;
        }
        pdf::pages(file).is_ok_and(|pages| {
            pages
                .iter()
                .take(2)
                .any(|page| page.text().to_lowercase().contains("hsbc"))
        })
    }

    fn parse(&self, file: &[u8]) -> Result<Vec<Row>, String> {
        let pages = pdf::pages(file).map_err(|e| format!("Failed to parse HSBC PDF: {e}"))?;
        let text = full_text(&pages);
        let rows = match detect_statement_type(&text) {
            Some(StatementType::CreditCard) => parse_credit_card(&pages),
            Some(StatementType::CurrentAccount) => parse_current_account(&pages),
            None => {
                return Err(
                    "Could not detect HSBC statement type. Expected a current account \
                            or credit card statement."
                        .into(),
                );
            }
        };
        if rows.is_empty() {
            return Err("No transactions found in HSBC statement.".into());
        }
        Ok(rows)
    }

    fn statement_info(&self, file: &[u8]) -> Result<Option<StatementInfo>, String> {
        let Ok(pages) = pdf::pages(file) else {
            return Ok(None);
        };
        let text = full_text(&pages);
        let Some(kind) = detect_statement_type(&text) else {
            return Ok(None);
        };
        let Some(bank_identifier) = extract_bank_identifier(&text) else {
            return Ok(None);
        };
        let (period_start, period_end) = match kind {
            StatementType::CreditCard => (
                first_received_date(&pages),
                extract_cc_statement_date(&text),
            ),
            StatementType::CurrentAccount => extract_ca_period(&text),
        };
        Ok(Some(StatementInfo {
            statement_type: kind.as_str().into(),
            bank_identifier,
            label: match kind {
                StatementType::CreditCard => "HSBC Credit Card Statement",
                StatementType::CurrentAccount => "HSBC Current Account Statement",
            }
            .into(),
            name_hint: extract_name_hint(&text),
            period_start,
            period_end,
            opening_balance: extract_opening_balance(&text, kind),
            closing_balance: extract_closing_balance(&text, kind),
        }))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn date(y: i32, m: u32, d: u32) -> NaiveDate {
        NaiveDate::from_ymd_opt(y, m, d).unwrap()
    }

    fn dec(text: &str) -> Decimal {
        Decimal::from_str(text).unwrap()
    }

    #[test]
    fn detects_the_statement_type() {
        use StatementType::*;
        let cases = [
            ("Your Credit Card statement", Some(CreditCard)),
            ("World Elite statement for October", Some(CreditCard)),
            ("Visa Card statement", Some(CreditCard)),
            ("HSBC Premier Statement", Some(CurrentAccount)),
            ("HSBC Advance account", Some(CurrentAccount)),
            ("Online Bonus Saver details", Some(CurrentAccount)),
            ("Flexible Saver statement", Some(CurrentAccount)),
            ("Some random bank document", None),
        ];
        for (text, expected) in cases {
            assert_eq!(detect_statement_type(text), expected, "{text}");
        }
    }

    #[test]
    fn identifies_the_account() {
        assert_eq!(
            extract_bank_identifier("A N Other 12-34-56 12345678 1").as_deref(),
            Some("12-34-56 12345678")
        );
        let card = extract_bank_identifier("Miss A Smith 1234 5678 9012 3456").unwrap();
        assert_eq!(card, "****3456");
        for part in ["1234", "5678", "9012"] {
            assert!(!card.contains(part), "the full card number is never kept");
        }
        assert_eq!(extract_bank_identifier("No account details here"), None);
    }

    #[test]
    fn opening_balances_are_signed_for_posting() {
        use StatementType::*;
        let cases = [
            ("PreviousBalance 312.56", CreditCard, Some("-312.56")),
            ("PreviousBalance 241.99CR", CreditCard, Some("241.99")),
            ("PreviousBalance 0.00", CreditCard, Some("0.00")),
            ("PreviousBalance 1,312.14CR", CreditCard, Some("1312.14")),
            ("OpeningBalance 9,815.51", CurrentAccount, Some("9815.51")),
            ("no balance here", CreditCard, None),
        ];
        for (text, kind, expected) in cases {
            assert_eq!(
                extract_opening_balance(text, kind),
                expected.map(dec),
                "{text}"
            );
        }
    }

    #[test]
    fn closing_balances_are_signed_for_posting() {
        assert_eq!(
            extract_closing_balance("New Balance 3,025.23", StatementType::CreditCard),
            Some(dec("-3025.23"))
        );
        assert_eq!(
            extract_closing_balance("ClosingBalance 649.11", StatementType::CurrentAccount),
            Some(dec("649.11"))
        );
    }

    #[test]
    fn reads_a_current_account_period() {
        assert_eq!(
            extract_ca_period("27 November to 26 December 2025 Branch Identifier Code"),
            (Some(date(2025, 11, 27)), Some(date(2025, 12, 26)))
        );
        assert_eq!(
            extract_ca_period("27 December 2023 to 26 January 2024 Branch Identifier Code"),
            (Some(date(2023, 12, 27)), Some(date(2024, 1, 26)))
        );
        assert_eq!(extract_ca_period("no period here"), (None, None));
    }

    #[test]
    fn reads_a_credit_card_statement_date() {
        assert_eq!(
            extract_cc_statement_date("Statement Date 27 June 2024 ..."),
            Some(date(2024, 6, 27))
        );
        assert_eq!(extract_cc_statement_date("no statement date"), None);
    }

    #[test]
    fn suggests_an_account_name() {
        let ca = "Your HSBC Advance details\nAccount Name Sortcode Account Number Sheet Number\n\
                  Alice Jane Smith 12-34-56 12345678 1\n";
        assert_eq!(extract_name_hint(ca).as_deref(), Some("Alice HSBC Advance"));
        let cc = "Your Credit Card statement\nMr Bob Charles Jones 1234 5678 9012 3456\n";
        assert_eq!(extract_name_hint(cc).as_deref(), Some("Bob Credit Card"));
        assert_eq!(
            extract_name_hint("Your HSBC Premier World Elite statement\n").as_deref(),
            Some("HSBC Premier World Elite")
        );
        assert_eq!(
            extract_name_hint("Account Name Sortcode\nAlice Smith 12-34-56 12345678 1\n"),
            None
        );
    }

    #[test]
    fn dates_and_descriptions() {
        assert_eq!(parse_hsbc_date("19 Dec 22"), Some(date(2022, 12, 19)));
        assert_eq!(parse_cc_date(&["12", "Dec22"]), Some(date(2022, 12, 12)));
        assert_eq!(parse_cc_date(&["03", "Jan", "23"]), Some(date(2023, 1, 3)));
        assert_eq!(parse_cc_date(&["Total"]), None);
        assert_eq!(
            clean_description(")))  TESCO   STORES"),
            ("TESCO STORES".into(), Some("contactless"))
        );
        assert_eq!(
            clean_description("IAPAMAZON"),
            ("AMAZON".into(), Some("in-app"))
        );
        assert_eq!(clean_description("DD SKY"), ("DD SKY".into(), None));
        assert_eq!(round_half_even(2.5), 2);
        assert_eq!(round_half_even(3.5), 4);
        assert_eq!(round_half_even(-0.4), 0);
    }

    // Credit card statements over several pages, on a made-up layout with no real data.

    fn page(lines: &[(f64, Vec<(f64, &str)>)]) -> Page {
        Page::from_words(
            lines
                .iter()
                .flat_map(|(top, items)| {
                    items.iter().map(move |(x0, text)| Word {
                        text: (*text).to_owned(),
                        x0: *x0,
                        x1: x0 + 10.0,
                        top: *top,
                        bottom: top + 8.0,
                    })
                })
                .collect(),
        )
    }

    fn header(top: f64) -> Vec<(f64, Vec<(f64, &'static str)>)> {
        vec![
            (top - 8.0, vec![(520.0, "Amount")]),
            (
                top,
                vec![
                    (40.0, "ReceivedByUs"),
                    (110.0, "TransactionDate"),
                    (185.0, "Details"),
                ],
            ),
        ]
    }

    fn txn<'a>(
        top: f64,
        day: &'a str,
        description: &'a str,
        amount: &'a str,
    ) -> (f64, Vec<(f64, &'a str)>) {
        (
            top,
            vec![
                (40.0, day),
                (60.0, "Jun"),
                (75.0, "26"),
                (110.0, day),
                (130.0, "Jun"),
                (145.0, "26"),
                (185.0, description),
                (520.0, amount),
            ],
        )
    }

    #[test]
    fn credit_cards_read_continuation_pages_and_second_cards() {
        let statement = (20.0, vec![(40.0, "Your"), (90.0, "statement")]);
        let pages = [
            // The summary page has no transactions.
            page(&[(
                100.0,
                vec![(40.0, "Statement"), (120.0, "Date"), (520.0, "1,234.56")],
            )]),
            // The first card's transactions start under a header…
            page(
                &[
                    vec![statement.clone()],
                    header(100.0),
                    vec![
                        txn(120.0, "15", "IAPFIRST SHOP", "10.00"),
                        txn(135.0, "16", "IAPSECOND SHOP", "20.00"),
                    ],
                ]
                .concat(),
            ),
            // …carry on at the top of the next page, and the second card's start under
            // another header.
            page(
                &[
                    vec![statement.clone(), txn(60.0, "17", "IAPTHIRD SHOP", "30.00")],
                    header(200.0),
                    vec![txn(220.0, "01", "IAPOTHER CARD SHOP", "40.00")],
                ]
                .concat(),
            ),
            // A continuation page with no header at all.
            page(&[
                statement.clone(),
                txn(60.0, "02", "IAPLAST SHOP", "50.00"),
                (
                    300.0,
                    vec![
                        (40.0, "Total"),
                        (90.0, "carried"),
                        (140.0, "forward"),
                        (520.0, "28,584"),
                    ],
                ),
            ]),
        ];
        let rows: Vec<(String, Decimal)> = parse_credit_card(&pages)
            .into_iter()
            .map(|r| (r.description, r.amount))
            .collect();
        let expected = [
            ("FIRST SHOP", "-10.00"),
            ("SECOND SHOP", "-20.00"),
            ("THIRD SHOP", "-30.00"),
            ("OTHER CARD SHOP", "-40.00"),
            ("LAST SHOP", "-50.00"),
        ]
        .map(|(d, a)| (d.to_owned(), dec(a)));
        assert_eq!(rows, expected);
    }
}

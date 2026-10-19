//! The importer on whole PDFs: made-up statements laid out as HSBC's are
//! (`fixtures/make_statements.py`), and real ones when there are some to hand.
//!
//! Real statements are never committed. To check against yours, put them in
//! `current_account/` and `credit_card/` folders and run
//!
//!     HSBC_TEST_PDF_DIR=/path/to/statements cargo test -p hsbc
//!
//! Without `HSBC_TEST_PDF_DIR` those tests pass without checking anything, and say so.

use std::path::{Path, PathBuf};
use std::str::FromStr;

use hsbc::Hsbc;
use pennychest_plugin::{Decimal, Importer, NaiveDate, Row};
use serde_json::json;

fn fixture(name: &str) -> Vec<u8> {
    std::fs::read(
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures")
            .join(name),
    )
    .unwrap()
}

fn date(text: &str) -> NaiveDate {
    NaiveDate::from_str(text).unwrap()
}

fn dec(text: &str) -> Decimal {
    Decimal::from_str(text).unwrap()
}

fn summary(rows: &[Row]) -> Vec<(String, String, Decimal)> {
    rows.iter()
        .map(|r| (r.date.to_string(), r.description.clone(), r.amount))
        .collect()
}

#[test]
fn a_current_account_statement() {
    let pdf = fixture("current_account.pdf");
    assert!(Hsbc.detect(&pdf, "statement.pdf"));
    assert!(!Hsbc.detect(&pdf, "statement.csv"), "PDFs only");

    let info = Hsbc.statement_info(&pdf).unwrap().unwrap();
    assert_eq!(info.statement_type, "current_account");
    assert_eq!(info.bank_identifier, "40-11-22 31926819");
    assert_eq!(info.label, "HSBC Current Account Statement");
    assert_eq!(info.name_hint.as_deref(), Some("Alice HSBC Advance"));
    assert_eq!(info.period_start, Some(date("2025-11-27")));
    assert_eq!(info.period_end, Some(date("2025-12-26")));
    assert_eq!(info.opening_balance, Some(dec("1000.00")));
    assert_eq!(info.closing_balance, Some(dec("2234.56")));

    let rows = Hsbc.parse(&pdf).unwrap();
    let expected = [
        ("2025-11-28", "TESCO STORES 2041", "-12.50"),
        // A second payment on the same date, contactless.
        ("2025-11-28", "COSTA COFFEE", "-3.20"),
        // A description over two lines.
        ("2025-12-01", "EXAMPLE EMPLOYER LTD SALARY DEC", "2500.00"),
        ("2025-12-02", "COUNCIL TAX", "-150.00"),
        ("2025-12-05", "APP STORE", "-0.99"),
        // Its amount is on the next page.
        ("2025-12-08", "RENT TO LANDLORD REF FLAT 2", "-1198.75"),
        ("2025-12-20", "J SMITH", "100.00"),
    ]
    .map(|(d, desc, a)| (d.to_owned(), desc.to_owned(), dec(a)));
    assert_eq!(summary(&rows), expected);
    assert_eq!(
        rows[0].metadata,
        json!({"payment_type": "VIS"}).as_object().unwrap().clone()
    );
    assert_eq!(rows[4].metadata["payment_method"], "in-app");
    assert_eq!(
        rows[0].raw_content,
        "28 Nov 25 | VIS | TESCO STORES 2041 | 12.50 | "
    );
}

#[test]
fn a_credit_card_statement() {
    let pdf = fixture("credit_card.pdf");
    assert!(Hsbc.detect(&pdf, "Statement.PDF"));

    let info = Hsbc.statement_info(&pdf).unwrap().unwrap();
    assert_eq!(info.statement_type, "credit_card");
    assert_eq!(info.bank_identifier, "****9010");
    assert_eq!(info.name_hint.as_deref(), Some("Bob Credit Card"));
    // The first date a transaction was received, as the statement only gives its end.
    assert_eq!(info.period_start, Some(date("2026-05-29")));
    assert_eq!(info.period_end, Some(date("2026-06-27")));
    // Owed, so negative on the card's liability account.
    assert_eq!(info.opening_balance, Some(dec("-312.56")));
    assert_eq!(info.closing_balance, Some(dec("-1025.23")));

    let rows = Hsbc.parse(&pdf).unwrap();
    let expected = [
        ("2026-05-28", "TESCO STORES 2041", "-43.50"),
        ("2026-06-02", "PRET A MANGER", "-6.95"),
        ("2026-06-03", "AMAZON REFUND", "19.99"),
        ("2026-06-10", "PAYMENT RECEIVED - THANK YOU", "312.56"),
        // From a continuation page with no header.
        ("2026-06-18", "TRAINLINE.COM", "-1007.83"),
    ]
    .map(|(d, desc, a)| (d.to_owned(), desc.to_owned(), dec(a)));
    assert_eq!(summary(&rows), expected);
    assert_eq!(rows[0].metadata["received_date"], "2026-05-29");
    assert_eq!(rows[1].metadata["payment_method"], "contactless");
}

#[test]
fn files_it_cant_read_say_so() {
    assert!(!Hsbc.detect(b"not a pdf", "x.pdf"));
    assert_eq!(Hsbc.statement_info(b"not a pdf").unwrap(), None);
    let error = Hsbc.parse(b"not a pdf").unwrap_err();
    assert!(error.starts_with("Failed to parse HSBC PDF"), "{error}");
}

// Real statements -----------------------------------------------------------------------------

/// The PDFs in `$HSBC_TEST_PDF_DIR/<kind>`, or none (and a note) without it.
fn real_statements(kind: &str) -> Vec<PathBuf> {
    let Some(root) = std::env::var_os("HSBC_TEST_PDF_DIR") else {
        eprintln!("HSBC_TEST_PDF_DIR isn't set: no real {kind} statements checked");
        return Vec::new();
    };
    let dir = Path::new(&root).join(kind);
    let mut pdfs: Vec<PathBuf> = std::fs::read_dir(&dir)
        .unwrap_or_else(|e| panic!("{}: {e}", dir.display()))
        .filter_map(Result::ok)
        .map(|e| e.path())
        .filter(|p| p.extension().is_some_and(|e| e.eq_ignore_ascii_case("pdf")))
        .collect();
    pdfs.sort();
    assert!(!pdfs.is_empty(), "no PDFs in {}", dir.display());
    pdfs
}

fn assert_valid_rows(rows: &[Row], name: &str) {
    assert!(!rows.is_empty(), "{name}: no transactions");
    for (i, row) in rows.iter().enumerate() {
        let year = row.date.format("%Y").to_string().parse::<i32>().unwrap();
        assert!(
            (2000..=2100).contains(&year),
            "{name} row {i}: {}",
            row.date
        );
        assert!(
            !row.description.trim().is_empty(),
            "{name} row {i}: no description"
        );
        assert!(!row.amount.is_zero(), "{name} row {i}: zero amount");
        assert!(
            !row.raw_content.trim().is_empty(),
            "{name} row {i}: no raw content"
        );
    }
}

#[test]
fn real_current_account_statements_are_detected() {
    for pdf in real_statements("current_account") {
        assert!(
            Hsbc.detect(&std::fs::read(&pdf).unwrap(), "s.pdf"),
            "{}",
            pdf.display()
        );
    }
}

#[test]
fn real_current_account_statements_are_identified() {
    let sort_code = regex::Regex::new(r"^\d{2}-\d{2}-\d{2} \d{7,10}$").unwrap();
    for pdf in real_statements("current_account") {
        let info = Hsbc.statement_info(&std::fs::read(&pdf).unwrap()).unwrap();
        let info = info.unwrap_or_else(|| panic!("{}: no statement info", pdf.display()));
        assert_eq!(info.statement_type, "current_account", "{}", pdf.display());
        assert!(
            sort_code.is_match(&info.bank_identifier),
            "{}",
            pdf.display()
        );
    }
}

#[test]
fn real_current_account_statements_parse() {
    for pdf in real_statements("current_account") {
        let rows = Hsbc.parse(&std::fs::read(&pdf).unwrap()).unwrap();
        assert_valid_rows(&rows, &pdf.display().to_string());
        assert!(
            rows.iter().any(|r| r.metadata.contains_key("payment_type")),
            "{}: no payment types",
            pdf.display()
        );
    }
}

#[test]
fn real_credit_card_statements_are_detected() {
    for pdf in real_statements("credit_card") {
        assert!(
            Hsbc.detect(&std::fs::read(&pdf).unwrap(), "s.pdf"),
            "{}",
            pdf.display()
        );
    }
}

#[test]
fn real_credit_card_statements_are_identified() {
    let last_four = regex::Regex::new(r"^\*{4}\d{4}$").unwrap();
    for pdf in real_statements("credit_card") {
        let info = Hsbc.statement_info(&std::fs::read(&pdf).unwrap()).unwrap();
        let info = info.unwrap_or_else(|| panic!("{}: no statement info", pdf.display()));
        assert_eq!(info.statement_type, "credit_card", "{}", pdf.display());
        assert!(
            last_four.is_match(&info.bank_identifier),
            "{}",
            pdf.display()
        );
    }
}

#[test]
fn real_credit_card_statements_parse() {
    for pdf in real_statements("credit_card") {
        let rows = Hsbc.parse(&std::fs::read(&pdf).unwrap()).unwrap();
        assert_valid_rows(&rows, &pdf.display().to_string());
    }
}

#[test]
fn real_credit_card_purchases_are_money_out() {
    for pdf in real_statements("credit_card") {
        let rows = Hsbc.parse(&std::fs::read(&pdf).unwrap()).unwrap();
        // A statement of only payments and refunds has nothing to check.
        let purchases: Vec<&Row> = rows
            .iter()
            .filter(|r| !r.raw_content.trim_end().ends_with("CR"))
            .collect();
        for row in purchases {
            assert!(
                row.amount.is_sign_negative(),
                "{}: {:?}",
                pdf.display(),
                row
            );
        }
    }
}

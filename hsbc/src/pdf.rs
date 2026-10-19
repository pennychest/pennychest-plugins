//! The words on each page of a PDF, with their positions, as pdfplumber's `extract_words`
//! finds them, and each page's text as its `extract_text` gives it.
//!
//! HSBC statements are space-aligned text with no table borders, so the importer rebuilds
//! their tables from where each word is. Words are found by pdfplumber's rules, with its
//! default settings:
//!
//! - characters are grouped into lines by their `top`, clustering values within 3pt;
//! - within a line, characters are sorted left to right, and a new word starts at a space,
//!   when the next character is more than 3pt after the end of the previous one, or before
//!   its start, or more than 3pt above or below it;
//! - text is each line's words joined by a space, and the lines joined by newlines.
//!
//! Positions are in points from the page's top-left corner, as pdfplumber gives them.

use std::panic::{AssertUnwindSafe, catch_unwind};

use pdf_extract::{Document, MediaBox, OutputDev, OutputError, Transform};

const X_TOLERANCE: f64 = 3.0;
const Y_TOLERANCE: f64 = 3.0;
/// How far below the baseline a character's box goes, as a fraction of its size. pdfplumber
/// reads it from the font; this is Helvetica's, which is close for the sans-serif fonts
/// statements use. It only moves every `top` on a line by the same amount.
const DESCENT: f64 = -0.207;

#[derive(Debug, Clone, PartialEq)]
pub struct Word {
    pub text: String,
    pub x0: f64,
    pub x1: f64,
    pub top: f64,
    pub bottom: f64,
}

#[derive(Debug, Clone)]
struct Char {
    text: String,
    x0: f64,
    x1: f64,
    top: f64,
    bottom: f64,
}

#[derive(Debug, Default)]
pub struct Page {
    pub words: Vec<Word>,
}

impl Page {
    /// A page made of these words, as tests build them.
    pub fn from_words(words: Vec<Word>) -> Self {
        Self { words }
    }

    /// The page's text: words on a line joined by a space, lines by newlines.
    pub fn text(&self) -> String {
        let mut words: Vec<&Word> = self.words.iter().collect();
        words.sort_by(|a, b| a.top.total_cmp(&b.top));
        cluster(&words, |w| w.top, Y_TOLERANCE)
            .into_iter()
            .map(|mut line| {
                line.sort_by(|a, b| a.x0.total_cmp(&b.x0));
                line.iter()
                    .map(|w| w.text.as_str())
                    .collect::<Vec<_>>()
                    .join(" ")
            })
            .collect::<Vec<_>>()
            .join("\n")
    }
}

/// Groups items whose key is within `tolerance` of the next smaller one, as pdfplumber's
/// `cluster_objects` does: in order of key, keeping the items' order within each group.
fn cluster<T: Clone>(items: &[T], key: impl Fn(&T) -> f64, tolerance: f64) -> Vec<Vec<T>> {
    let mut values: Vec<f64> = items.iter().map(&key).collect();
    values.sort_by(f64::total_cmp);
    values.dedup();
    // Which group each distinct value is in.
    let mut groups = Vec::with_capacity(values.len());
    let mut group = 0;
    for (i, value) in values.iter().enumerate() {
        if i > 0 && *value > values[i - 1] + tolerance {
            group += 1;
        }
        groups.push(group);
    }
    let group_of = |value: f64| {
        let i = values
            .binary_search_by(|v| v.total_cmp(&value))
            .expect("every value is listed");
        groups[i]
    };
    let mut clustered: Vec<Vec<T>> = vec![Vec::new(); group + usize::from(!items.is_empty())];
    for item in items {
        clustered[group_of(key(item))].push(item.clone());
    }
    clustered.retain(|g| !g.is_empty());
    clustered
}

/// pdfplumber writes ligatures out as their letters.
fn expand(text: &str) -> String {
    text.chars()
        .map(|c| match c {
            'ﬀ' => "ff".to_owned(),
            'ﬃ' => "ffi".to_owned(),
            'ﬄ' => "ffl".to_owned(),
            'ﬁ' => "fi".to_owned(),
            'ﬂ' => "fl".to_owned(),
            'ﬆ' | 'ﬅ' => "st".to_owned(),
            other => other.to_string(),
        })
        .collect()
}

fn words_of(chars: &[Char]) -> Vec<Word> {
    let mut words = Vec::new();
    for mut line in cluster(chars, |c| c.top, Y_TOLERANCE) {
        line.sort_by(|a, b| a.x0.total_cmp(&b.x0));
        let mut current: Vec<Char> = Vec::new();
        for char in line {
            let begins_word = current.last().is_some_and(|prev| {
                char.x0 < prev.x0
                    || char.x0 > prev.x1 + X_TOLERANCE
                    || (char.top - prev.top).abs() > Y_TOLERANCE
            });
            if char.text.trim().is_empty() {
                words.extend(word(&current));
                current.clear();
            } else if begins_word {
                words.extend(word(&current));
                current = vec![char];
            } else {
                current.push(char);
            }
        }
        words.extend(word(&current));
    }
    words
}

fn word(chars: &[Char]) -> Option<Word> {
    let first = chars.first()?;
    let mut word = Word {
        text: String::new(),
        x0: first.x0,
        x1: first.x1,
        top: first.top,
        bottom: first.bottom,
    };
    for c in chars {
        word.text.push_str(&expand(&c.text));
        word.x0 = word.x0.min(c.x0);
        word.x1 = word.x1.max(c.x1);
        word.top = word.top.min(c.top);
        word.bottom = word.bottom.max(c.bottom);
    }
    Some(word)
}

/// Collects each page's upright characters from pdf-extract.
#[derive(Default)]
struct Collector {
    pages: Vec<Vec<Char>>,
    page_top: f64,
}

impl OutputDev for Collector {
    fn begin_page(
        &mut self,
        _page_num: u32,
        media_box: &MediaBox,
        _art_box: Option<(f64, f64, f64, f64)>,
    ) -> Result<(), OutputError> {
        self.page_top = media_box.ury;
        self.pages.push(Vec::new());
        Ok(())
    }

    fn end_page(&mut self) -> Result<(), OutputError> {
        Ok(())
    }

    fn output_character(
        &mut self,
        trm: &Transform,
        width: f64,
        _spacing: f64,
        font_size: f64,
        char: &str,
    ) -> Result<(), OutputError> {
        // Rotated text, such as a reference up the margin, isn't part of any table.
        if trm.m12.abs() > f64::EPSILON || trm.m21.abs() > f64::EPSILON {
            return Ok(());
        }
        let size = (font_size * trm.m22).abs();
        let (x, baseline) = (trm.m31, trm.m32);
        let advance = width * font_size * trm.m11;
        if let Some(page) = self.pages.last_mut() {
            page.push(Char {
                text: char.to_owned(),
                x0: x,
                x1: x + advance,
                top: self.page_top - (baseline + (1.0 + DESCENT) * size),
                bottom: self.page_top - (baseline + DESCENT * size),
            });
        }
        Ok(())
    }

    fn begin_word(&mut self) -> Result<(), OutputError> {
        Ok(())
    }

    fn end_word(&mut self) -> Result<(), OutputError> {
        Ok(())
    }

    fn end_line(&mut self) -> Result<(), OutputError> {
        Ok(())
    }
}

/// Every page's words, or why the file can't be read as a PDF.
pub fn pages(pdf: &[u8]) -> Result<Vec<Page>, String> {
    // pdf-extract panics on some malformed files, rather than returning an error.
    let read = catch_unwind(AssertUnwindSafe(|| -> Result<Vec<Vec<Char>>, String> {
        let document = Document::load_mem(pdf).map_err(|e| e.to_string())?;
        if document.is_encrypted() {
            return Err("it's password-protected".into());
        }
        let mut collector = Collector::default();
        pdf_extract::output_doc(&document, &mut collector).map_err(|e| format!("{e:?}"))?;
        Ok(collector.pages)
    }));
    let pages = match read {
        Ok(result) => result?,
        Err(_) => return Err("it couldn't be read".into()),
    };
    Ok(pages
        .iter()
        .map(|chars| Page {
            words: words_of(chars),
        })
        .collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn char(text: &str, x0: f64, top: f64) -> Char {
        Char {
            text: text.into(),
            x0,
            x1: x0 + 5.0,
            top,
            bottom: top + 10.0,
        }
    }

    #[test]
    fn groups_characters_into_words_as_pdfplumber_does() {
        let chars = [
            // "Paid" then a gap of 2pt (same word), then "out" 10pt on.
            char("P", 10.0, 100.0),
            char("a", 17.0, 100.5),
            char("i", 24.0, 100.0),
            char("d", 31.0, 100.0),
            char("o", 46.0, 101.0),
            char("u", 51.0, 101.0),
            char("t", 56.0, 101.0),
            // A space splits words however close.
            char(" ", 61.0, 100.0),
            char("x", 62.0, 100.0),
            // Another line.
            char("1", 10.0, 120.0),
        ];
        let texts: Vec<String> = words_of(&chars).into_iter().map(|w| w.text).collect();
        assert_eq!(texts, ["Paid", "out", "x", "1"]);
    }

    #[test]
    fn clusters_chain_like_pdfplumbers() {
        let values = [1.0, 3.5, 6.0, 9.5, 20.0];
        let groups = cluster(&values, |v| *v, 3.0);
        assert_eq!(groups, vec![vec![1.0, 3.5, 6.0], vec![9.5], vec![20.0]]);
    }

    #[test]
    fn page_text_joins_words_and_lines() {
        let word = |text: &str, x0: f64, top: f64| Word {
            text: text.into(),
            x0,
            x1: x0 + 10.0,
            top,
            bottom: top + 10.0,
        };
        let page = Page::from_words(vec![
            word("Balance", 60.0, 10.0),
            word("Opening", 10.0, 11.0),
            word("Next", 10.0, 30.0),
        ]);
        assert_eq!(page.text(), "Opening Balance\nNext");
    }

    #[test]
    fn says_what_is_wrong_with_a_file_that_isnt_a_pdf() {
        assert!(pages(b"not a pdf").is_err());
    }
}

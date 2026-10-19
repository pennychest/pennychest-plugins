//! PennyChest's importer for HSBC PDF statements. `main.rs` serves it as a plugin.

pub mod importer;
pub mod pdf;

pub use importer::Hsbc;

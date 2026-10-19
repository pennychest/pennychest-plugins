//! The HSBC statement importer, as a PennyChest plugin.

use pennychest_plugin::Plugin;

fn main() {
    Plugin::new("hsbc", env!("CARGO_PKG_VERSION"))
        .importer(hsbc::Hsbc)
        .serve();
}

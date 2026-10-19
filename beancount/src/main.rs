//! The Beancount exporter, as a PennyChest plugin.

use pennychest_plugin::Plugin;

fn main() {
    Plugin::new("beancount", env!("CARGO_PKG_VERSION"))
        .exporter(beancount::Beancount)
        .serve();
}

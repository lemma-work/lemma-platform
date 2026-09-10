//! The Agent Host's command line.
//!
//! Was one 635-line file; the parser, the dispatch, the console callbacks
//! and the printing helpers are modules of their own now.

mod cli;
mod console;
mod dispatch;
mod output;

#[cfg(test)]
mod tests;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    dispatch::run().await
}

//! The packaged layout, against the contract that names it.

use super::*;

/// Every path this file probes for is one the contract names.
///
/// The producer of a host pack is a Python script run by a release job; the
/// consumer is this file, and it hard-codes a dozen paths. Nothing checked
/// that the two agreed, and nothing could: a PR runs this file's tests
/// against a fixture the same PR wrote, while the pack is built somewhere
/// else on a different trigger. A rename lands green on both sides and is
/// found by whoever installs the release, as `NotFound` and the name of a
/// file they have never heard of.
///
/// So both sides assert against one committed artifact -- the rule
/// docs/testing.md states for when a stub is allowed to exist at all. The
/// Python half of this pair is
/// `tests/test_build_local_host_pack.py::test_the_builder_writes_every_path_the_app_looks_for`.
///
/// Asserted as a set equality rather than containment, in both directions.
/// A candidate here that the contract does not name is a path the producer
/// has never been told to write; a candidate there that this file does not
/// probe is a fallback nobody would ever reach.
#[test]
fn the_packaged_layout_matches_the_committed_contract() {
    let contract: Value =
        serde_json::from_str(include_str!("../../../../contracts/host-pack-layout.json")).unwrap();

    // What this file actually probes for, read out of its own source so the
    // list cannot be maintained twice.
    // The module that owns it, so this fails loudly if it moves rather than
    // quietly finding nothing.
    let source = include_str!("../bindings.rs").replace("\r\n", "\n");
    let packaged = {
        let start = source
            .find("fn packaged_bindings(")
            .expect("packaged_bindings exists");
        let end = source[start..]
            .find("fn source_bindings(")
            .expect("source_bindings follows it");
        &source[start..start + end]
    };

    for entry in contract["required"].as_array().unwrap() {
        let what = entry["what"].as_str().unwrap();
        for candidate in entry["candidates"].as_array().unwrap() {
            let candidate = candidate.as_str().unwrap();
            assert!(
                packaged.contains(&format!("\"{candidate}\"")),
                "the contract promises {what} at {candidate}, which this file never looks for",
            );
        }
    }

    // And nothing probed for is absent from the contract. `required_file`
    // takes its candidates as string literals, so they are exactly the
    // quoted paths in this function.
    let promised: std::collections::BTreeSet<&str> = contract["required"]
        .as_array()
        .unwrap()
        .iter()
        .flat_map(|entry| entry["candidates"].as_array().unwrap())
        .map(|candidate| candidate.as_str().unwrap())
        .collect();
    for line in packaged.lines() {
        let trimmed = line.trim();
        let Some(rest) = trimmed.strip_prefix('"') else {
            continue;
        };
        let Some(path) = rest.strip_suffix("\",") else {
            continue;
        };
        if !path.contains('/') {
            continue;
        }
        assert!(
            promised.contains(path),
            "this file probes for {path}, which the contract does not promise",
        );
    }

    // The derived paths are joins rather than probes, so they are checked as
    // substrings -- across the whole pack, since `build` names most of them,
    // and never across this file, which would let a path be "found" in the
    // guards' own fixtures.
    let shipping = pack_source();
    let shipping = shipping.as_str();
    for entry in contract["derived"].as_array().unwrap() {
        if !entry["named_by_consumer"].as_bool().unwrap_or(true) {
            continue;
        }
        let path = entry["path"].as_str().unwrap();
        let (parent, leaf) = path.rsplit_once('/').unwrap_or(("", path));
        assert!(
            shipping.contains(leaf),
            "the contract names {path}, which nothing in this file joins ({parent})",
        );
    }
}

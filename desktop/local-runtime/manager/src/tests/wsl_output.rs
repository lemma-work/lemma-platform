//! Reading what `wsl.exe` prints, whatever it encodes it in.

use super::*;

/// wsl.exe writes UTF-16LE. Decoding it as UTF-8 succeeds -- the NUL halves
/// are valid code points -- so the result was text with a NUL between every
/// letter, and `trim()` does not remove NUL because it is not whitespace.
/// Every WSL error reached the user that way, and the substring matching
/// that turns a message into an actionable error code never fired.
#[test]
fn wsl_errors_are_decoded_from_utf16_before_anyone_reads_them() {
    let utf16: Vec<u8> = "There is no distribution with the supplied name.\r\n"
        .encode_utf16()
        .flat_map(u16::to_le_bytes)
        .collect();
    assert_eq!(
        wsl_message(&utf16),
        "There is no distribution with the supplied name."
    );
    assert!(!wsl_message(&utf16).contains('\0'));
}

#[test]
fn a_byte_order_mark_does_not_survive_into_a_distribution_name() {
    // With the BOM left on, the first distribution listed could never match
    // its own name -- so an existing guest looked absent and Lemma tried to
    // import over it.
    let mut bytes = vec![0xFF, 0xFE];
    bytes.extend("LemmaRuntime\r\n".encode_utf16().flat_map(u16::to_le_bytes));
    let listed = decode_wsl_output(&bytes);
    assert!(
        listed.lines().any(|line| line.trim() == "LemmaRuntime"),
        "decoded as {listed:?}"
    );
}

#[test]
fn plain_utf8_output_is_left_alone() {
    assert_eq!(wsl_message(b"docker: not found\n"), "docker: not found");
}

#[cfg(windows)]
#[test]
fn decodes_legacy_utf16_wsl_distribution_output() {
    let encoded: Vec<u8> = "LemmaRuntime\r\nUbuntu\r\n"
        .encode_utf16()
        .flat_map(u16::to_le_bytes)
        .collect();
    assert!(decode_wsl_output(&encoded).contains("LemmaRuntime"));
}

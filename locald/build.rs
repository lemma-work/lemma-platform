use std::path::Path;

/// Embed `Info.plist` so the daemon's code-signing identifier survives a rebuild.
///
/// macOS keys a keychain item's access control to the code identity of whoever
/// created it. `codesign` will happily invent an identifier for a bare Mach-O
/// that does not supply one, but it derives that identifier from the binary
/// itself — link the same source twice and the two results claim to be different
/// programs. locald reads the operator's secrets on every launch, so the user
/// would be asked to re-authorise after each build.
///
/// A `__TEXT,__info_plist` section fixes that at the binary level: `codesign`
/// prefers its `CFBundleIdentifier` over anything it would derive, whoever runs
/// it and with whatever flags. That matters because the desktop bundler re-signs
/// this daemon as a sidecar and passes no identifier of its own.
fn main() {
    println!("cargo:rerun-if-changed=build.rs");
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() != Ok("macos") {
        return;
    }
    let manifest = std::env::var("CARGO_MANIFEST_DIR").expect("cargo sets CARGO_MANIFEST_DIR");
    let plist = Path::new(&manifest).join("Info.plist");
    println!("cargo:rerun-if-changed={}", plist.display());
    // Only the shipped binaries: tests and build scripts link their own
    // executables, and a duplicated section would fail the link.
    println!(
        "cargo:rustc-link-arg-bins=-Wl,-sectcreate,__TEXT,__info_plist,{}",
        plist.display()
    );
}

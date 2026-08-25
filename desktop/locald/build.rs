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
    // `telemetry.rs` reads this with `option_env!`, which is resolved at
    // compile time -- and cargo will not rebuild a crate just because an
    // environment variable changed. Both release workflows restore a warm
    // cache, so without this a build that sets the key for the first time
    // would happily reuse an object file compiled without it, and ship
    // telemetry that can never fire while appearing to be configured.
    println!("cargo:rerun-if-env-changed=LEMMA_TELEMETRY_KEY");
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

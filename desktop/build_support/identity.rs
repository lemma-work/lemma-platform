use std::path::Path;

pub fn embed_info_plist() {
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() != Ok("macos") {
        return;
    }
    let manifest = std::env::var("CARGO_MANIFEST_DIR").expect("cargo sets CARGO_MANIFEST_DIR");
    let plist = Path::new(&manifest).join("Info.plist");
    println!("cargo:rerun-if-changed={}", plist.display());
    // Bundlers re-sign sidecars without supplying an identifier. Embed it so
    // code identity survives code changes and subsequent signing passes.
    println!(
        "cargo:rustc-link-arg-bins=-Wl,-sectcreate,__TEXT,__info_plist,{}",
        plist.display()
    );
}

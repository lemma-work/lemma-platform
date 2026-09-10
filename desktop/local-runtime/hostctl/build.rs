#[path = "../../build_support/identity.rs"]
mod identity;

fn main() {
    identity::embed_info_plist();
}

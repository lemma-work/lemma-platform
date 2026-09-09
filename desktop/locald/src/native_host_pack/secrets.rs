//! This installation's own secrets, and the private files holding them.

pub(crate) use lemma_private_file::{
    make_private as ensure_private_file, write_atomic as write_private_atomic,
};

use super::*;

/// Read the installation secret, replacing it only if unreadable.
///
/// `installation_secret` derives the Fernet key for encrypted columns and the
/// workspace runtime credential key. Its invariant is that it is gone when the
/// data directory is -- so reminting it while the data directory survives makes
/// every encrypted row permanently undecryptable, quietly. Healing it therefore
/// also records that the data must be reset.
pub(crate) fn load_or_create_host_secrets(
    path: &Path,
    healed: &mut Vec<String>,
) -> io::Result<HostSecrets> {
    if path.is_file() {
        match read_existing_host_secrets(path) {
            Ok(secrets) => return Ok(secrets),
            Err(reason) => {
                let aside = crate::paths::quarantine_aside(path)?;
                if let Some(root) = path.parent() {
                    crate::paths::require_data_reset(
                        root,
                        "this installation's secret was replaced, and anything encrypted with \
                         the previous one can no longer be read",
                    )?;
                }
                healed.push(format!(
                    "the installation secret was unreadable ({reason}); kept as {} and replaced. \
                     Encrypted local data cannot be decrypted with the new one",
                    aside.display()
                ));
            }
        }
    }
    // Absent, not unreadable -- and that is not automatically a first run. A
    // restore that skipped an owner-only file, or a half-finished manual
    // cleanup, leaves the data behind and takes the key. Minting silently there
    // is the same permanent loss the corrupt path is careful about, arrived at
    // more quietly.
    else if path
        .parent()
        .is_some_and(crate::paths::installation_has_data)
    {
        let root = path.parent().expect("checked just above");
        crate::paths::require_data_reset(
            root,
            "this installation's secret is missing, and anything encrypted with it can no \
             longer be read",
        )?;
        healed.push(
            "the installation secret was missing while local data was still present; a new \
             one was created and the existing encrypted data cannot be decrypted with it"
                .to_owned(),
        );
    }
    let mut bytes = [0_u8; 32];
    getrandom::fill(&mut bytes)
        .map_err(|error| io::Error::other(format!("secure randomness failed: {error}")))?;
    let secrets = HostSecrets {
        installation_secret: bytes.iter().map(|byte| format!("{byte:02x}")).collect(),
    };
    write_private_atomic(path, &serde_json::to_vec(&secrets)?)?;
    Ok(secrets)
}

pub(crate) fn read_existing_host_secrets(path: &Path) -> Result<HostSecrets, String> {
    ensure_private_file(path).map_err(|error| error.to_string())?;
    let raw = fs::read(path).map_err(|error| error.to_string())?;
    let secrets: HostSecrets =
        serde_json::from_slice(&raw).map_err(|error| format!("invalid JSON: {error}"))?;
    validate_hex_secret("installation secret", &secrets.installation_secret)
        .map_err(|error| error.to_string())?;
    Ok(secrets)
}

pub(crate) fn random_hex(byte_count: usize) -> io::Result<String> {
    let mut bytes = vec![0_u8; byte_count];
    getrandom::fill(&mut bytes)
        .map_err(|error| io::Error::other(format!("secure randomness failed: {error}")))?;
    Ok(bytes.iter().map(|byte| format!("{byte:02x}")).collect())
}

pub(crate) fn validate_hex_secret(label: &str, value: &str) -> io::Result<()> {
    if value.len() != 64
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err(invalid(format!(
            "{label} is not a 32-byte lowercase hex secret"
        )));
    }
    Ok(())
}

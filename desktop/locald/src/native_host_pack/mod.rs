//! The host pack: the app code locald runs on this machine, and the manifest
//! that says how to start it.
//!
//! Was one 1,968-line file.

//! Native renderer for the packaged two-process managed-local runtime.
//!
//! A signed Desktop release must work on a machine without a source checkout,
//! Python package manager, or `lemma-stack` executable. Compatibility providers
//! may still use the legacy renderer, but the app-owned VZ/WSL runtime is
//! rendered entirely by the durable daemon.

use std::collections::BTreeMap;
use std::fs::{self, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use base64::engine::general_purpose::URL_SAFE;
use base64::Engine;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use crate::local_domain::LocalDomain;
use crate::network::{load_or_allocate, NetworkPorts};
use crate::paths::LocalPaths;

mod bindings;
mod build;
mod secrets;
mod stamps;

pub(crate) use bindings::*;
pub(crate) use build::*;
pub(crate) use secrets::*;
pub(crate) use stamps::*;

#[cfg(test)]
mod tests;

pub(crate) const POSTGRES_PORT: u16 = 55432;
pub(crate) const REDIS_PORT: u16 = 56379;
pub(crate) const SUPERTOKENS_PORT: u16 = 53567;
// The workspace and the API share one browser hostname on two ports.
//
// Safari/WKWebView blocks a response from a different hostname from setting the
// SuperTokens session cookies the top-level frontend uses, so both are exposed
// through one name. The backend still binds only to loopback, and sandboxes use
// the explicit host.lemma.internal callbacks below.
//
// Which name that is comes from `local_domain`, not from a constant here -- see
// that module for why it is configurable and what it costs.

#[derive(Clone, Debug)]
pub(crate) struct ManagedManifestMaterial {
    pub postgres_password: String,
    pub redis_password: String,
    pub bridge_executable: PathBuf,
}

/// The per-installation seed every derived local key hangs off.
///
/// Never regenerate this for an existing installation: it also derives the key
/// that encrypts stored secrets, so a fresh one leaves every encrypted row
/// unreadable. `deny_unknown_fields` is deliberate — a field this does not
/// recognise means the file was written by something else.
#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct HostSecrets {
    installation_secret: String,
}

/// Where the code a manifest points at actually lives.
///
/// A released pack ships its own interpreter, its own Node, and a built Next
/// server. A developer's checkout has none of those and uses `uv` and
/// `next dev`. Those are the only differences — ports, environment, the
/// managed-runtime block, health checks and restart policy are identical — so
/// they are rendered once from here instead of forking the renderer. A dev run
/// that exercised a different supervisor would prove nothing about this one.
pub(crate) struct Bindings {
    /// Argv prefix that runs the backend's Python.
    python: Vec<String>,
    backend_dir: PathBuf,
    frontend_command: Vec<String>,
    frontend_dir: PathBuf,
    /// Assets that are baked into a pack but live in sibling projects in a
    /// checkout.
    browser_sdk: PathBuf,
    browser_ui: PathBuf,
    skills: PathBuf,
    /// `next dev` must not be told it is a production build.
    node_env: &'static str,
    /// Where the backend keeps the key that encrypts stored secrets.
    ///
    /// A packaged install holds real provider credentials and belongs in the
    /// OS keychain. A checkout cannot use it: the backend is a `uv run` child
    /// of locald with no GUI session, so macOS answers with a "keychain cannot
    /// be found" dialog and the run stalls before anything works. Source mode
    /// uses an in-config key derived from this installation's own secret, which
    /// is throwaway anyway — the whole dev root is under /tmp.
    secret_key_provider: &'static str,
}

/// A checkout to run instead of a released pack, and the release whose pinned
/// infrastructure images it should run against.
///
/// Selected by `LEMMA_LOCALD_SOURCE_ROOT`; set only by
/// `desktop/scripts/dev-local.sh --source`. A packaged app never sets it, and
/// the renderer behaves exactly as before when it is absent.
pub(crate) struct SourceLayout {
    root: PathBuf,
    release_manifest: PathBuf,
}

pub(crate) fn prepare(
    paths: &LocalPaths,
    pack_root: &Path,
    material: ManagedManifestMaterial,
    healed: &mut Vec<String>,
) -> io::Result<PathBuf> {
    crate::host_process::reclaim_persisted_installation_processes(&paths.root)?;
    let ports = load_or_allocate(paths)?;
    let source = source_layout()?;
    let manifest = build(
        paths,
        pack_root,
        &material,
        ports,
        source.as_ref(),
        healed,
        &LocalDomain::from_env(),
    )?;
    let destination = paths.root.join("host-pack.json");
    write_private_atomic(&destination, &serde_json::to_vec_pretty(&manifest)?)?;
    Ok(destination)
}

pub(crate) fn invalid(message: impl Into<String>) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message.into())
}

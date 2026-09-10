//! Reading a release manifest, and refusing one that does not describe
//! artifacts this build may fetch.

use super::*;

pub(crate) fn load_manifest(path: &Path) -> io::Result<ReleaseManifest> {
    load_manifest_with_policy(path, local_artifacts_enabled(path))
}

pub(crate) fn load_manifest_with_policy(
    path: &Path,
    allow_local_artifacts: bool,
) -> io::Result<ReleaseManifest> {
    let raw = fs::read(path)?;
    if raw.len() > 1024 * 1024 {
        return Err(invalid("release manifest exceeds 1 MiB"));
    }
    let manifest: ReleaseManifest = serde_json::from_slice(&raw).map_err(|error| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            format!("invalid local release manifest: {error}"),
        )
    })?;
    // Checked before anything is downloaded, and on the marker rather than on
    // the placeholder hostname: the unresolvable URL is an implementation
    // detail of the stub, the marker is the statement of intent.
    if manifest.artifact_source.as_deref() == Some(BUILD_CHECK_SOURCE) {
        return Err(invalid(
            "this app came from a CI build-check DMG, which carries no runtime to \
             install. Build an installable one with the Release Local Images \
             workflow (publish: false) - see desktop/README.md",
        ));
    }
    if manifest.schema_version != MANIFEST_SCHEMA_VERSION
        || manifest.version.is_empty()
        || manifest.version.len() > 128
        || !manifest
            .version
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'-' | b'+'))
    {
        return Err(invalid("unsupported release manifest identity or schema"));
    }
    for artifact in manifest
        .host_packs
        .values()
        .chain(manifest.guest_runtimes.values())
    {
        validate_artifact(artifact, allow_local_artifacts)?;
        if artifact.runtime_version != manifest.version {
            return Err(invalid(
                "artifact runtime version does not match the release manifest",
            ));
        }
    }
    Ok(manifest)
}

pub(crate) fn artifact_for<'a>(
    artifacts: &'a HashMap<String, ArtifactRef>,
    target: &str,
    label: &str,
) -> io::Result<&'a ArtifactRef> {
    artifacts.get(target).ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::NotFound,
            format!("release manifest has no {label} for {target}"),
        )
    })
}

pub(crate) fn validate_artifact(
    artifact: &ArtifactRef,
    allow_local_artifacts: bool,
) -> io::Result<()> {
    let safe_source = match (&artifact.url, &artifact.resource) {
        (Some(value), None) => {
            let url = reqwest::Url::parse(value)
                .map_err(|error| invalid(format!("invalid artifact URL: {error}")))?;
            let safe_https = url.scheme() == "https";
            let safe_local = allow_local_artifacts
                && url.scheme() == "file"
                && url.host_str().is_none()
                && url.query().is_none()
                && url.to_file_path().is_ok();
            (safe_https || safe_local)
                && url.username().is_empty()
                && url.password().is_none()
                && url.fragment().is_none()
        }
        (None, Some(resource)) => {
            !resource.is_empty()
                && resource.len() <= 128
                && !resource.contains(['/', '\\'])
                && resource
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'-' | b'_'))
        }
        _ => false,
    };
    if !safe_source
        || artifact.format != "zip"
        || artifact.size == 0
        || artifact.size > MAX_ARCHIVE_BYTES
        || artifact.expanded_size == 0
        || u128::from(artifact.expanded_size) > MAX_EXTRACTED_BYTES
        || !valid_metadata_name(&artifact.platform)
        || !valid_metadata_name(&artifact.architecture)
        || artifact.runtime_version.is_empty()
        || artifact.runtime_version.len() > 128
        || artifact.sha256.len() != 64
        || !artifact
            .sha256
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err(invalid("invalid or unsafe release artifact metadata"));
    }
    Ok(())
}

pub(crate) fn valid_metadata_name(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
}

pub(crate) fn validate_artifact_target(
    artifact: &ArtifactRef,
    platform: &str,
    architecture: &str,
    label: &str,
) -> io::Result<()> {
    if artifact.platform != platform || artifact.architecture != architecture {
        return Err(invalid(format!(
            "{label} metadata targets {}-{}, expected {platform}-{architecture}",
            artifact.platform, artifact.architecture
        )));
    }
    Ok(())
}

pub(crate) fn local_artifacts_enabled(manifest_path: &Path) -> bool {
    if std::env::var("LEMMA_DESKTOP_ALLOW_LOCAL_ARTIFACTS").as_deref() != Ok("1") {
        return false;
    }
    let Some(configured) = std::env::var_os("LEMMA_DESKTOP_RELEASE_MANIFEST") else {
        return false;
    };
    let configured = PathBuf::from(configured);
    configured.canonicalize().ok() == manifest_path.canonicalize().ok()
}

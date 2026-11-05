from importlib.metadata import PackageNotFoundError, version

# The installed distribution's version, so `lemma-stack self version` reports
# what pyproject.toml declares (and scripts/check_version_consistency.py holds
# equal to every other component). A literal here was set once at 0.1.0 and
# never moved again. "unknown" when running from an uninstalled tree: it is not
# a valid version, so the manifest's min_admin_version gate skips rather than
# refusing every release.
try:
    __version__ = version("lemma-stack")
except PackageNotFoundError:
    __version__ = "unknown"

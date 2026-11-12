"""Shared pod bundle format vocabulary for the Lemma CLI and backend.

Pure, stdlib-only helpers for the pod bundle directory format: layout
constants, JSONC parsing, table diffing, portable-variable handling,
per-resource payload normalization, and archive pack/extract.
"""

from __future__ import annotations

from .apply_fields import (
    GRANT_HOLDER_DIRS,
    POD_CONNECTOR_FIELDS,
    SCHEDULE_APPLY_FIELDS,
    SURFACE_APPLY_FIELDS,
    BundleConnector,
    read_bundle_connector,
    MACHINE_APPLY_FIELDS,
)
from .archive import extract_bundle, pack_bundle
from .diff import TableDiff, diff_table_columns
from .jsonc import loads_jsonc, strip_jsonc
from .layout import (
    APP_MANIFEST_ALIAS,
    EXPORTABLE_RESOURCE_DIRS,
    FILES_MANIFEST,
    FORMAT_VERSION,
    FORMAT_VERSION_WITHOUT_CONNECTORS,
    JSON_FILE_REF_KEY,
    POD_MANIFEST_FILE,
    POD_MEMBER_TOKEN,
    RAW_FILE_REF_KEY,
    RESOURCE_DIR_ALIASES,
    RESOURCE_DIRS,
    SYSTEM_TABLE_COLUMNS,
    TABLE_DATA_FILE,
    bundle_format_version,
    check_format_version,
    load_resource_payload,
    normalize_resource_dir_name,
)
from .normalize import (
    BundleValidationIssue,
    load_bundle_connectors,
    referenced_connector_slugs,
)

__all__ = [
    "APP_MANIFEST_ALIAS",
    "EXPORTABLE_RESOURCE_DIRS",
    "FILES_MANIFEST",
    "FORMAT_VERSION",
    "FORMAT_VERSION_WITHOUT_CONNECTORS",
    "GRANT_HOLDER_DIRS",
    "JSON_FILE_REF_KEY",
    "POD_CONNECTOR_FIELDS",
    "MACHINE_APPLY_FIELDS",
    "POD_MANIFEST_FILE",
    "POD_MEMBER_TOKEN",
    "RAW_FILE_REF_KEY",
    "RESOURCE_DIR_ALIASES",
    "RESOURCE_DIRS",
    "SCHEDULE_APPLY_FIELDS",
    "SURFACE_APPLY_FIELDS",
    "SYSTEM_TABLE_COLUMNS",
    "TABLE_DATA_FILE",
    "BundleConnector",
    "BundleValidationIssue",
    "TableDiff",
    "bundle_format_version",
    "check_format_version",
    "diff_table_columns",
    "extract_bundle",
    "load_bundle_connectors",
    "load_resource_payload",
    "loads_jsonc",
    "normalize_resource_dir_name",
    "pack_bundle",
    "read_bundle_connector",
    "referenced_connector_slugs",
    "strip_jsonc",
]

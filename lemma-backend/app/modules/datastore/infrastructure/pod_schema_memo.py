"""Which pod schemas this process knows to be at the latest search DDL version.

Not a data cache: it holds nothing another replica could disagree about. A
schema at the latest version stays there until this process is replaced by one
whose code knows a newer version, or until the schema is dropped -- and both
places that drop one call ``forget``. Only the latest version is remembered,
because a lower one can be migrated by another process at any moment.
"""

from __future__ import annotations

from app.core.bounded import BoundedDict

#: ``pod_schema_migrations.CHANGE_LOG_VERSION``, which imports this module.
_CHANGE_LOG_VERSION = 3

# Bounded like every process-lifetime memo keyed by something that grows: an
# evicted schema costs one primary-key read, never a wrong answer. There is one
# pod-data database per process, so the schema name is the key.
_at_latest: BoundedDict[str, int] = BoundedDict(
    8192, name="datastore.pod_schema_versions"
)


#: Schemas below the latest version that already carry the change log -- what
#: a record write needs -- so the write path asks the database once, not per
#: write, while the background backfill has not reached them yet.
_change_log_ready: BoundedDict[str, bool] = BoundedDict(
    8192, name="datastore.pod_schema_change_log"
)


def change_log_ready(schema_name: str) -> bool:
    return bool(_change_log_ready.get(schema_name)) or (
        known_version(schema_name) >= _CHANGE_LOG_VERSION
    )


def remember_change_log(schema_name: str) -> None:
    _change_log_ready[schema_name] = True


def known_version(schema_name: str) -> int:
    return _at_latest.get(schema_name, 0) or 0


def remember(schema_name: str, version: int) -> None:
    _at_latest[schema_name] = version


def forget(schema_name: str) -> None:
    _at_latest.pop(schema_name, None)
    _change_log_ready.pop(schema_name, None)


def forget_all() -> None:
    _at_latest.clear()
    _change_log_ready.clear()

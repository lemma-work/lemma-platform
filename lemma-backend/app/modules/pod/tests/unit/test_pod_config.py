"""Unit tests for PodConfig default-runtime resolution and back-compat."""

from app.modules.agent.domain.value_objects import AgentRuntimeConfig
from app.modules.pod.domain.pod_entities import PodConfig


def test_resolved_default_runtime_prefers_full_runtime():
    config = PodConfig.model_validate(
        {"default_runtime": {"profile_id": "p1", "model_name": "m1"}}
    )
    assert config.resolved_default_runtime() == AgentRuntimeConfig(
        profile_id="p1", model_name="m1"
    )


def test_resolved_default_runtime_falls_back_to_legacy_profile_id():
    config = PodConfig.model_validate({"default_profile_id": "legacy"})
    runtime = config.resolved_default_runtime()
    assert runtime is not None
    assert runtime.profile_id == "legacy"
    # legacy default never pinned a model — null means "use the profile default"
    assert runtime.model_name is None


def test_resolved_default_runtime_none_when_unset():
    assert PodConfig().resolved_default_runtime() is None


def test_full_runtime_with_null_model_behaves_like_legacy():
    config = PodConfig.model_validate({"default_runtime": {"profile_id": "p4"}})
    runtime = config.resolved_default_runtime()
    assert runtime is not None
    assert runtime.profile_id == "p4"
    assert runtime.model_name is None


def test_serializer_drops_unset_defaults():
    dumped = PodConfig(default_profile_id="legacy").model_dump()
    assert "default_runtime" not in dumped
    assert dumped["default_profile_id"] == "legacy"

    dumped2 = PodConfig().model_dump()
    assert "default_runtime" not in dumped2
    assert "default_profile_id" not in dumped2


def test_full_runtime_serializes_without_null_model():
    config = PodConfig(default_runtime=AgentRuntimeConfig(profile_id="p1"))
    dumped = config.model_dump()
    assert dumped["default_runtime"] == {"profile_id": "p1"}


def test_from_raw_tolerates_non_mapping_and_malformed():
    assert PodConfig.from_raw(None).resolved_default_runtime() is None
    assert PodConfig.from_raw("garbage").resolved_default_runtime() is None
    # an empty default_profile_id is invalid; from_raw degrades to empty config
    assert PodConfig.from_raw({"default_profile_id": ""}).resolved_default_runtime() is None


def test_from_raw_ignores_unknown_keys():
    config = PodConfig.from_raw(
        {"default_runtime": {"profile_id": "p1"}, "unknown_future_key": 123}
    )
    runtime = config.resolved_default_runtime()
    assert runtime is not None and runtime.profile_id == "p1"

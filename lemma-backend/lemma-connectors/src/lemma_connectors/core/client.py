from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

from lemma_connectors.core.auth import CredentialTypes
from lemma_connectors.core.descriptors import OperationDescriptor, ToolDescriptor
from lemma_connectors.core.errors import OperationNotFoundError
from lemma_connectors.core.openapi import (
    GeneratedTool,
    LazyToolMap,
    build_generated_client,
    build_tool_map,
)
from lemma_connectors.core.operations import Operation


class _LazyResourceNamespace:
    """Attribute-access namespace that builds a resource on first access."""

    __slots__ = ("_resource_module", "_client", "_cache")

    def __init__(self, resource_module: Any, client: Any):
        self._resource_module = resource_module
        self._client = client
        self._cache: dict[str, Any] = {}

    def __getattr__(self, slug: str) -> Any:
        registry = self._resource_module.RESOURCE_REGISTRY
        if slug in registry:
            resource = self._cache.get(slug)
            if resource is None:
                resource = self._resource_module.build_resource(self._client, slug)
                self._cache[slug] = resource
            return resource
        raise AttributeError(f"No resource named {slug!r}")


class BaseInfoClient:
    def __init__(
        self,
        *,
        metadata_path: Path,
        base_url: str,
        client_module_path: str,
    ):
        self._metadata_path = metadata_path
        self._base_url = base_url
        self._client_module_path = client_module_path
        self._generated_client: Any | None = None
        self._tools: LazyToolMap | None = None
        self._resource_module: Any | None = None
        self._operations_by_resource: dict[str, dict[str, Operation[Any, Any]]] = {}
        self._operation_index: dict[str, Operation[Any, Any]] = {}
        self.resources: Any = SimpleNamespace()

    def _ensure_generated_client(self) -> Any:
        if self._generated_client is None:
            self._generated_client = build_generated_client(
                client_module_path=self._client_module_path,
                base_url=self._base_url,
                credentials=None,
            )
        return self._generated_client

    def _ensure_tools(self) -> LazyToolMap:
        if self._tools is None:
            self._tools = build_tool_map(
                metadata_path=self._metadata_path,
                generated_client=self._ensure_generated_client(),
            )
        return self._tools

    def list_tools(self) -> list[ToolDescriptor]:
        return [tool.descriptor for tool in self._ensure_tools().list_tools()]

    def get_tool(self, name: str) -> GeneratedTool:
        return self._ensure_tools().get_tool(name)

    def list_operation_names(self) -> list[str]:
        if self._resource_module is None:
            return []
        return list(self._resource_module.OPERATION_TO_RESOURCE.keys())

    def list_operations(self) -> list[OperationDescriptor]:
        self._ensure_all_resource_operations()
        return [op.descriptor for op in self._operation_index.values()]

    def get_operation(self, name: str) -> Operation[Any, Any]:
        operation = self._operation_index.get(name)
        if operation is not None:
            return operation
        if self._resource_module is None:
            raise OperationNotFoundError(name)
        slug = self._resource_module.OPERATION_TO_RESOURCE.get(name)
        if slug is None:
            raise OperationNotFoundError(name)
        ops = self._ensure_resource_operations(slug)
        operation = ops.get(name)
        if operation is None:
            raise OperationNotFoundError(name)
        self._operation_index[name] = operation
        return operation

    def _ensure_resource_operations(
        self, slug: str
    ) -> dict[str, Operation[Any, Any]]:
        ops = self._operations_by_resource.get(slug)
        if ops is None:
            resource = self._resource_module.build_resource(self, slug)
            ops = resource.build_operations()
            self._operations_by_resource[slug] = ops
        return ops

    def _ensure_all_resource_operations(self) -> None:
        if self._resource_module is None:
            return
        for slug in self._resource_module.RESOURCE_REGISTRY:
            ops = self._ensure_resource_operations(slug)
            for operation_name, operation in ops.items():
                self._operation_index.setdefault(operation_name, operation)

    def register_resource_registry(self, resource_module: Any) -> None:
        self._resource_module = resource_module
        self.resources = _LazyResourceNamespace(resource_module, self)

    def register_resources(self, resources: dict[str, Any]) -> None:
        self.resources = SimpleNamespace(**resources)
        operations: dict[str, Operation[Any, Any]] = {}
        for resource in resources.values():
            operations.update(resource.build_operations())
        self._operation_index = operations
        self._operations_by_resource = {}


class BaseIntegrationClient(BaseInfoClient):
    def __init__(
        self,
        *,
        metadata_path: Path,
        base_url: str,
        client_module_path: str,
        credentials: CredentialTypes | None,
    ):
        super().__init__(
            metadata_path=metadata_path,
            base_url=base_url,
            client_module_path=client_module_path,
        )
        self.credentials = credentials

    def _ensure_generated_client(self) -> Any:
        if self._generated_client is None:
            self._generated_client = build_generated_client(
                client_module_path=self._client_module_path,
                base_url=self._base_url,
                credentials=self.credentials,
            )
        return self._generated_client

    async def execute_tool(self, name: str, payload: Mapping[str, Any] | None = None) -> Any:
        tool = self.get_tool(name)
        return await tool.execute(payload)

    async def execute_operation(
        self,
        name: str,
        payload: Mapping[str, Any] | Any,
    ) -> Any:
        operation = self.get_operation(name)
        return await operation.execute(payload)

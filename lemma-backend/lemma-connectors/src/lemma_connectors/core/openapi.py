from __future__ import annotations

import importlib
import inspect
import json
import warnings
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, get_origin

from json_schema_to_pydantic import PydanticModelBuilder
from pydantic import BaseModel, ConfigDict, create_model
from pydantic.json_schema import PydanticJsonSchemaWarning

from lemma_connectors.core.auth import (
    ApiKeyCredentials,
    CredentialTypes,
    NoAuthCredentials,
    OAuth2Credentials,
)
from lemma_connectors.core.descriptors import ToolDescriptor
from lemma_connectors.core.errors import (
    IntegrationExecutionError,
    ToolNotFoundError,
    ToolValidationError,
)
from lemma_connectors.core.metadata import ToolMetadata
from lemma_connectors.core.results import BinaryContentResult


class PydanticModelRegistry(BaseModel):
    module_path: str
    class_names: list[str]
    enum_names: list[str] = []
    schema_refs: dict[str, str]
    operations: dict[str, dict[str, str | None]]


class GeneratedTool:
    def __init__(
        self,
        *,
        metadata: ToolMetadata,
        generated_client: Any,
        model_registry: PydanticModelRegistry,
    ):
        self.metadata = metadata
        self.generated_client = generated_client
        self.model_registry = model_registry
        self._module = importlib.import_module(metadata.module_path)
        if "Unset" not in self._module.__dict__:
            try:
                types_module = importlib.import_module(
                    metadata.module_path.split(".api.", 1)[0] + ".types"
                )
                unset_type = getattr(types_module, "Unset", None)
                if unset_type is not None:
                    self._module.Unset = unset_type
            except Exception:
                pass
        self._pydantic_models = importlib.import_module(model_registry.module_path)
        self._tool_types = importlib.import_module(
            model_registry.module_path.rsplit(".", 1)[0] + ".tool_types"
        )
        self._async_function = getattr(self._module, "asyncio", None)
        self._async_detailed_function = getattr(self._module, "asyncio_detailed", None)
        self._signature_function = self._async_function or self._async_detailed_function
        if self._signature_function is None:
            raise AttributeError(f"{metadata.module_path} has no asyncio entrypoint")
        self._signature = inspect.signature(self._signature_function)
        self._schema_builder = PydanticModelBuilder(base_model_type=BaseModel)
        self.input_type = self._tool_types.INPUT_MODELS.get(metadata.name) or self._build_input_type()
        self.output_type = self._tool_types.OUTPUT_MODELS.get(metadata.name) or self._build_output_type()

    def _resolve_signature_parameter_name(self, field_name: str) -> str:
        if field_name in self._signature.parameters:
            return field_name
        suffixed_name = f"{field_name}_"
        if suffixed_name in self._signature.parameters:
            return suffixed_name
        return field_name

    @property
    def descriptor(self) -> ToolDescriptor:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=r"Cannot update undefined schema.*skipped-discriminator",
                category=PydanticJsonSchemaWarning,
            )
            input_schema = self.input_type.model_json_schema()
            output_schema = self.output_type.model_json_schema()
        return ToolDescriptor(
            name=self.metadata.name,
            title=self.metadata.title,
            description=self.metadata.description,
            input_schema=input_schema,
            output_schema=output_schema,
            method=self.metadata.method,
            path=self.metadata.path,
            input_type=self.input_type,
            output_type=self.output_type,
            tags=tuple(self.metadata.tags),
            deprecated=self.metadata.deprecated,
        )

    def _lookup_named_model(self, *, schema_ref: str | None, operation_key: str) -> type[BaseModel] | None:
        class_name: str | None = None
        if schema_ref is not None:
            class_name = self.model_registry.schema_refs.get(schema_ref)
        if class_name is None:
            class_name = (self.model_registry.operations.get(self.metadata.name) or {}).get(operation_key)
        if class_name is None:
            return None
        candidate = getattr(self._pydantic_models, class_name, None)
        if inspect.isclass(candidate) and issubclass(candidate, BaseModel):
            return candidate
        return None

    def _build_inline_model(self, *, name: str, schema: dict[str, Any]) -> type[BaseModel]:
        titled_schema = dict(schema)
        titled_schema.setdefault("title", name)
        model = self._schema_builder.create_pydantic_model(
            titled_schema,
            root_schema=titled_schema,
            allow_undefined_array_items=True,
            allow_undefined_type=True,
        )
        if inspect.isclass(model) and issubclass(model, BaseModel):
            return model
        raise TypeError(f"Could not build a Pydantic model for {name}")

    def _build_body_type(self) -> type[BaseModel] | None:
        if self.metadata.request_body is None:
            return None
        named = self._lookup_named_model(
            schema_ref=self.metadata.request_body.schema_ref,
            operation_key="request",
        )
        if named is not None:
            return named
        return self._build_inline_model(
            name=f"{self.metadata.name.title().replace('_', '')}Body",
            schema=self.metadata.request_body.schema_definition,
        )

    def _build_output_type(self) -> type[BaseModel]:
        if self._expects_binary_response():
            return BinaryContentResult
        named = self._lookup_named_model(
            schema_ref=self.metadata.response_schema_ref,
            operation_key="response",
        )
        if named is not None:
            return named
        return self._build_inline_model(
            name=f"{self.metadata.name.title().replace('_', '')}Response",
            schema=self.metadata.response_schema,
        )

    def _build_input_type(self) -> type[BaseModel]:
        field_definitions: dict[str, tuple[Any, Any]] = {}
        for name, parameter in self._signature.parameters.items():
            if name == "client":
                continue
            annotation = parameter.annotation
            if name == "body":
                annotation = self._build_body_type() or annotation
            if annotation is inspect.Signature.empty:
                annotation = Any
            if parameter.default is inspect.Signature.empty:
                default = ...
            else:
                default = parameter.default
            field_definitions[name] = (annotation, default)

        model_name = f"{self.metadata.name.title().replace('_', '')}ToolInput"
        return create_model(
            model_name,
            __config__=ConfigDict(extra="forbid"),
            **field_definitions,
        )

    def _is_json_media_type(self, media_type: str | None) -> bool:
        if not media_type:
            return False
        normalized = media_type.split(";", 1)[0].strip().lower()
        return (
            normalized == "application/json"
            or normalized.endswith("+json")
        )

    def _expects_binary_response(self) -> bool:
        return (
            self.metadata.binary_response_hint
            or not self._is_json_media_type(self.metadata.response_content_type)
        )

    async def execute(self, data: Any) -> BaseModel:
        normalized_data = self._normalize_input_data(data)
        if isinstance(normalized_data, BaseModel):
            validated = self.input_type.model_validate(normalized_data.model_dump())
        elif isinstance(normalized_data, Mapping):
            validated = self.input_type.model_validate(normalized_data)
        elif data is None:
            validated = self.input_type.model_validate({})
        else:
            raise ToolValidationError(
                f"{self.metadata.name} expects a mapping or {self.input_type.__name__} input."
            )

        raw_payload = validated.model_dump(
            exclude_none=True,
            exclude_unset=True,
            by_alias=False,
        )
        kwargs: dict[str, Any] = {}
        for name, value in raw_payload.items():
            if self._should_omit_wire_parameter(name):
                continue
            parameter_name = self._resolve_signature_parameter_name(name)
            annotation = self._signature.parameters[parameter_name].annotation
            kwargs[parameter_name] = self._coerce_value(
                annotation,
                self._to_wire_data(value),
            )

        try:
            # Prefer the *_detailed executor so we can see the HTTP status. The
            # plain `asyncio` variant returns only `.parsed`, which is None on any
            # non-2xx — validating that None later surfaces a confusing
            # "Input should be a valid dictionary ... NoneType" error that masks
            # the real upstream failure (e.g. a 400 from the provider).
            if self._async_detailed_function is not None:
                response = await self._async_detailed_function(
                    client=self.generated_client,
                    **kwargs,
                )
                self._raise_for_status(response)
                if self._expects_binary_response():
                    result = BinaryContentResult.from_http_response(
                        response,
                        fallback_media_type=self.metadata.response_content_type,
                    )
                else:
                    result = getattr(response, "parsed", None)
                    if result is None and getattr(response, "content", None):
                        result = BinaryContentResult.from_http_response(
                            response,
                            fallback_media_type=self.metadata.response_content_type,
                        )
            elif self._async_function is not None:
                result = await self._async_function(client=self.generated_client, **kwargs)
            else:
                raise IntegrationExecutionError(
                    f"{self.metadata.name} has no async executor."
                )
        except IntegrationExecutionError:
            raise
        except Exception as exc:
            if self._is_slack_tool() and not self._expects_binary_response():
                raw_result = await self._try_execute_raw_json(kwargs)
                if raw_result is not None:
                    result = raw_result
                else:
                    raise IntegrationExecutionError(
                        f"{self.metadata.name} execution failed: {exc}"
                    ) from exc
            else:
                raise IntegrationExecutionError(
                    f"{self.metadata.name} execution failed: {exc}"
                ) from exc

        if isinstance(result, (bytes, bytearray)):
            result = BinaryContentResult.from_bytes(
                bytes(result),
                media_type=self.metadata.response_content_type,
            )
        if result is None:
            # 2xx with an empty body (e.g. 204 No Content): return a well-formed
            # empty result instead of letting model_validate(None) raise.
            return self.output_type.model_validate({})
        return self.output_type.model_validate(coerce_tool_result(result))

    def _raise_for_status(self, response: Any) -> None:
        """Surface upstream HTTP failures as a clear, actionable error."""
        status_code = int(getattr(response, "status_code", 0) or 0)
        if status_code < 400:
            return
        body = self._summarize_error_body(getattr(response, "content", None))
        message = (
            f"{self.metadata.name} failed: the provider returned HTTP {status_code}."
        )
        if body:
            message = f"{message} {body}"
        raise IntegrationExecutionError(message)

    @staticmethod
    def _summarize_error_body(content: Any, *, limit: int = 600) -> str:
        if not content:
            return ""
        try:
            text = (
                content.decode("utf-8", errors="replace")
                if isinstance(content, (bytes, bytearray))
                else str(content)
            ).strip()
        except Exception:
            return ""
        # Prefer the provider's error message when the body is JSON.
        try:
            data = json.loads(text)
        except Exception:
            return text[:limit]
        if isinstance(data, dict):
            err = data.get("error", data.get("message", data))
            if isinstance(err, dict):
                err = err.get("message") or err.get("status") or json.dumps(err)
            text = str(err)
        return text[:limit]

    async def _try_execute_raw_json(self, kwargs: dict[str, Any]) -> Any | None:
        get_kwargs = getattr(self._module, "_get_kwargs", None)
        get_async_client = getattr(self.generated_client, "get_async_httpx_client", None)
        if get_kwargs is None or get_async_client is None:
            return None
        try:
            request_kwargs = get_kwargs(**kwargs)
            response = await get_async_client().request(**request_kwargs)
            return response.json()
        except Exception:
            return None

    def _normalize_input_data(self, data: Any) -> Any:
        if not (
            self._uses_authenticated_header()
            and self._is_slack_tool()
            and isinstance(data, Mapping)
        ):
            return data
        fields = getattr(self.input_type, "model_fields", {})
        if "token" not in fields or data.get("token") is not None:
            return data
        normalized = dict(data)
        normalized["token"] = getattr(self.generated_client, "token", "")
        return normalized

    def _uses_authenticated_header(self) -> bool:
        return bool(getattr(self.generated_client, "token", None))

    def _is_slack_tool(self) -> bool:
        return ".slack." in self.metadata.module_path

    def _should_omit_wire_parameter(self, name: str) -> bool:
        return (
            name == "token"
            and self._is_slack_tool()
            and self._uses_authenticated_header()
            and any(
                parameter.python_name == name
                and parameter.location == "query"
                for parameter in self.metadata.parameters
            )
        )

    def _to_wire_data(self, value: Any) -> Any:
        if isinstance(value, BaseModel):
            return value.model_dump(
                by_alias=True,
                exclude_none=True,
                exclude_unset=True,
            )
        if isinstance(value, list):
            return [self._to_wire_data(item) for item in value]
        if isinstance(value, dict):
            return {
                key: self._to_wire_data(item)
                for key, item in value.items()
                if item is not None
            }
        return value

    def _coerce_value(self, annotation: Any, value: Any) -> Any:
        if value is None:
            return None

        origin = get_origin(annotation)
        if origin is not None:
            union_args = tuple(
                arg for arg in getattr(annotation, "__args__", ()) if arg is not type(None)
            )
            for arg in union_args:
                coerced = self._coerce_value(arg, value)
                if coerced is not value or arg is Any:
                    return coerced
        if origin in (list, tuple) and isinstance(value, list):
            item_type = getattr(annotation, "__args__", (Any,))[0]
            return [self._coerce_value(item_type, item) for item in value]

        if inspect.isclass(annotation) and issubclass(annotation, Enum):
            if isinstance(value, annotation):
                return value
            try:
                return annotation(value)
            except Exception:
                return value

        if hasattr(annotation, "from_dict") and isinstance(value, Mapping):
            return annotation.from_dict(dict(value))
        return value


def load_tool_metadata(path: Path) -> list[ToolMetadata]:
    content = json.loads(path.read_text())
    return [ToolMetadata.model_validate(item) for item in content["tools"]]


def load_pydantic_model_registry(path: Path) -> PydanticModelRegistry:
    return PydanticModelRegistry.model_validate(json.loads(path.read_text()))


def build_generated_client(
    *,
    client_module_path: str,
    base_url: str,
    credentials: CredentialTypes | None,
) -> Any:
    client_module = importlib.import_module(client_module_path)
    resolved_base_url = (
        credentials.base_url
        if isinstance(credentials, OAuth2Credentials) and credentials.base_url
        else base_url
    )
    if credentials is None or isinstance(credentials, NoAuthCredentials):
        return client_module.Client(base_url=resolved_base_url)
    if isinstance(credentials, OAuth2Credentials):
        return client_module.AuthenticatedClient(
            base_url=resolved_base_url,
            token=credentials.access_token,
            prefix=credentials.token_type,
        )
    if isinstance(credentials, ApiKeyCredentials):
        return client_module.Client(
            base_url=resolved_base_url,
            httpx_args={"params": {credentials.name: credentials.api_key}},
        )
    raise TypeError(f"Unsupported credentials type: {type(credentials)!r}")


class LazyToolMap:
    """Builds ``GeneratedTool`` instances on first access instead of up front.

    Constructing a ``GeneratedTool`` imports its generated API module and the
    large pydantic model module. For a connector with hundreds of tools (jira),
    building all of them eagerly at client-construction time is the dominant
    import cost. This map defers that work to ``get_tool``/``list_tools``.
    """

    def __init__(self, *, metadata_path: Path, generated_client: Any):
        self._metadata = load_tool_metadata(metadata_path)
        self._registry = load_pydantic_model_registry(
            metadata_path.parent / "pydantic_model_registry.json"
        )
        self._generated_client = generated_client
        self._by_name = {item.name: item for item in self._metadata}
        self._cache: dict[str, GeneratedTool] = {}

    def _build(self, item: ToolMetadata) -> GeneratedTool:
        tool = GeneratedTool(
            metadata=item,
            generated_client=self._generated_client,
            model_registry=self._registry,
        )
        self._cache[item.name] = tool
        return tool

    def get_tool(self, name: str) -> GeneratedTool:
        tool = self._cache.get(name)
        if tool is not None:
            return tool
        item = self._by_name.get(name)
        if item is None:
            raise ToolNotFoundError(name)
        return self._build(item)

    def list_tools(self) -> list[GeneratedTool]:
        tools: list[GeneratedTool] = []
        for item in self._metadata:
            tool = self._cache.get(item.name)
            if tool is None:
                tool = self._build(item)
            tools.append(tool)
        return tools


def build_tool_map(
    *,
    metadata_path: Path,
    generated_client: Any,
) -> LazyToolMap:
    return LazyToolMap(metadata_path=metadata_path, generated_client=generated_client)


def list_tool_descriptors(tools: Iterable[GeneratedTool]) -> list[ToolDescriptor]:
    return [tool.descriptor for tool in tools]


def coerce_tool_result(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return coerce_tool_result(value.to_dict())
    if isinstance(value, list):
        return [coerce_tool_result(item) for item in value]
    if isinstance(value, dict):
        return {key: coerce_tool_result(item) for key, item in value.items()}
    return value

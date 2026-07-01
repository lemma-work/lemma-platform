from __future__ import annotations

from typing import Any
import structlog
from pydantic import BaseModel

from app.modules.connectors.domain.ports import SchemaCompilerPort


class PydanticCodeSchemaCompiler(SchemaCompilerPort):
    """Compile generated pydantic-model snippets into JSON schema."""

    logger = structlog.get_logger()

    def to_json_schema(self, code: str) -> dict[str, Any]:
        if not code:
            return {}

        model_name = None
        try:
            # 1. Try to find model name from comment
            for line in code.split("\n"):
                if line.strip().startswith("#target_variable:"):
                    model_name = line.split(":", 1)[1].strip()
                    break

            namespace: dict[str, Any] = {}
            # SECURITY: Restrict __builtins__ to prevent arbitrary code execution
            safe_ns = {"__builtins__": {k: v for k, v in __builtins__.items() 
                       if k in ("BaseModel", "Field", "__import__", "type", "isinstance", 
                                "issubclass", "hasattr", "getattr", "Exception", 
                                "ValueError", "TypeError", "__name__", "__doc__")}}
            safe_ns.update(namespace)
            exec(code, safe_ns)  # nosec B102 — restricted builtins to prevent RCE

            # 2. If model_name found, try to use it
            if model_name:
                model_class = namespace.get(model_name)
                if model_class:
                    model_class.model_rebuild(_types_namespace=namespace)
                    return model_class.model_json_schema()

            # 3. Fallback: Search for InputModel/OutputModel or single BaseModel
            # Priority: InputModel > OutputModel > Single BaseModel

            # Check for common names
            for name in ["InputModel", "OutputModel"]:
                if (
                    name in namespace
                    and isinstance(namespace[name], type)
                    and issubclass(namespace[name], BaseModel)
                ):
                    namespace[name].model_rebuild(_types_namespace=namespace)
                    return namespace[name].model_json_schema()

            # Prefer a model with a common suffix when helper models are present.
            pydantic_models_by_name = [
                (name, obj)
                for name, obj in namespace.items()
                if isinstance(obj, type)
                and issubclass(obj, BaseModel)
                and obj is not BaseModel
            ]
            for suffix in ["Input", "Output", "Request", "Response"]:
                suffix_matches = [
                    (name, obj)
                    for name, obj in pydantic_models_by_name
                    if name.endswith(suffix)
                ]
                if len(suffix_matches) == 1:
                    suffix_matches[0][1].model_rebuild(_types_namespace=namespace)
                    return suffix_matches[0][1].model_json_schema()
                if len(suffix_matches) > 1:
                    # When there are multiple matches, prefer the last class defined.
                    suffix_matches[-1][1].model_rebuild(_types_namespace=namespace)
                    return suffix_matches[-1][1].model_json_schema()

            # Check for single BaseModel
            pydantic_models = [obj for _, obj in pydantic_models_by_name]

            if len(pydantic_models) == 1:
                pydantic_models[0].model_rebuild(_types_namespace=namespace)
                return pydantic_models[0].model_json_schema()

            self.logger.warning(
                "Could not resolve Pydantic model from code snippet",
                code_sample=code[:100],
            )
            return {}

        except Exception as e:
            self.logger.error(
                "Error compiling schema from code", error=str(e), code_sample=code[:100]
            )
            return {}

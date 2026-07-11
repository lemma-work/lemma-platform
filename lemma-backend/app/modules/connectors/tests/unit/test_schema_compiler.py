import pytest
from app.modules.connectors.infrastructure.adapters.schema_compiler import (
    PydanticCodeSchemaCompiler,
)


class TestPydanticCodeSchemaCompiler:
    @pytest.fixture
    def compiler(self):
        return PydanticCodeSchemaCompiler()

    def test_to_json_schema_with_target_variable(self, compiler):
        code = """#snippet_type: PYDANTIC_MODEL
#target_variable: MyModel

from pydantic import BaseModel

class MyModel(BaseModel):
    foo: str
"""
        schema = compiler.to_json_schema(code)
        assert schema["title"] == "MyModel"
        assert "foo" in schema["properties"]

    def test_to_json_schema_missing_target_variable_fallback_single_model(
        self, compiler
    ):
        code = """
from pydantic import BaseModel

class UniqueModel(BaseModel):
    bar: int
"""
        schema = compiler.to_json_schema(code)
        assert schema["title"] == "UniqueModel"
        assert "bar" in schema["properties"]

    def test_to_json_schema_missing_target_variable_fallback_input_model(
        self, compiler
    ):
        code = """
from pydantic import BaseModel

class OtherModel(BaseModel):
    x: int

class InputModel(BaseModel):
    y: int
"""
        schema = compiler.to_json_schema(code)
        assert schema["title"] == "InputModel"
        assert "y" in schema["properties"]

    def test_to_json_schema_missing_target_variable_fallback_output_model(
        self, compiler
    ):
        code = """
from pydantic import BaseModel

class OutputModel(BaseModel):
    z: int
"""
        schema = compiler.to_json_schema(code)
        assert schema["title"] == "OutputModel"
        assert "z" in schema["properties"]

    def test_to_json_schema_missing_target_variable_fallback_suffix_model(
        self, compiler
    ):
        code = """
from pydantic import BaseModel

class NestedThing(BaseModel):
    value: int

class ListThingsOutput(BaseModel):
    items: list[NestedThing]
"""
        schema = compiler.to_json_schema(code)
        assert schema["title"] == "ListThingsOutput"
        assert "items" in schema["properties"]

    def test_to_json_schema_empty_code(self, compiler):
        assert compiler.to_json_schema("") == {}
        assert compiler.to_json_schema(None) == {}

    def test_to_json_schema_invalid_code(self, compiler):
        code = "this is not python code"
        # Should return empty dict and log error, not raise
        assert compiler.to_json_schema(code) == {}

    def test_to_json_schema_no_pydantic_models(self, compiler):
        code = """
x = 1
y = 2
"""
        assert compiler.to_json_schema(code) == {}

    def test_to_json_schema_multiple_models_ambiguous(self, compiler):
        code = """
from pydantic import BaseModel

class ModelA(BaseModel):
    a: int

class ModelB(BaseModel):
    b: int
"""
        # Should return empty dict because it can't decide which one to use
        # and there is no InputModel/OutputModel
        assert compiler.to_json_schema(code) == {}

    def test_to_json_schema_with_forward_ref(self, compiler):
        code = """#snippet_type: PYDANTIC_MODEL
#target_variable: ForwardRefModel

from __future__ import annotations
from typing import Optional
from pydantic import BaseModel

class ForwardRefModel(BaseModel):
    child: Optional[ForwardRefModel] = None
"""
        schema = compiler.to_json_schema(code)

        # When recursion is involved, the top-level schema might be a Ref to a definition
        if "$ref" in schema:
            assert "$defs" in schema
            assert "ForwardRefModel" in schema["$defs"]
            model_schema = schema["$defs"]["ForwardRefModel"]
            assert model_schema["title"] == "ForwardRefModel"
            props = model_schema["properties"]["child"]
        else:
            assert schema["title"] == "ForwardRefModel"
            props = schema["properties"]["child"]

        # Handle different Pydantic schema structures for recursive refs
        assert "$ref" in props or ("anyOf" in props and "$ref" in props["anyOf"][0])

    def test_emits_only_allowlisted_types_containers_and_field_metadata(self, compiler):
        code = '''
from datetime import date, datetime
from typing import Annotated, Any, Dict, List, Literal, Mapping, Optional, Set, Tuple, Union
from uuid import UUID
from pydantic import BaseModel, Field

class Child(BaseModel):
    identifier: UUID

class InputModel(BaseModel):
    text: str = Field(..., title="Text", description="safe", min_length=1,
                      max_length=20, pattern="^[a-z]+$", examples=["hello"])
    score: float = Field(default=1.0, ge=0, gt=-1, le=10, lt=11)
    child: Child
    maybe: Optional[int] = None
    either: Union[str, int]
    modern_union: str | None = None
    choice: Literal["one", "two"]
    tags: Set[str]
    values: Tuple[int]
    children: List[Child]
    lookup: Dict[str, bool]
    mapping: Mapping[str, Any]
    annotated: Annotated[str, "ignored metadata"]
    created_at: datetime
    birthday: date
'''

        schema = compiler.to_json_schema(code)

        assert schema["required"] == [
            "text",
            "child",
            "either",
            "choice",
            "tags",
            "values",
            "children",
            "lookup",
            "mapping",
            "annotated",
            "created_at",
            "birthday",
        ]
        assert schema["properties"]["text"] == {
            "type": "string",
            "title": "Text",
            "description": "safe",
            "minLength": 1,
            "maxLength": 20,
            "pattern": "^[a-z]+$",
            "examples": ["hello"],
        }
        assert schema["properties"]["score"] == {
            "type": "number",
            "minimum": 0,
            "exclusiveMinimum": -1,
            "maximum": 10,
            "exclusiveMaximum": 11,
        }
        assert schema["properties"]["choice"] == {
            "enum": ["one", "two"],
            "type": "string",
        }
        assert schema["properties"]["tags"] == {
            "type": "array",
            "items": {"type": "string"},
        }
        assert schema["properties"]["lookup"] == {
            "type": "object",
            "additionalProperties": {"type": "boolean"},
        }
        assert schema["properties"]["created_at"] == {
            "type": "string",
            "format": "date-time",
        }
        assert schema["properties"]["birthday"] == {
            "type": "string",
            "format": "date",
        }
        assert schema["$defs"]["Child"]["properties"]["identifier"] == {
            "type": "string",
            "format": "uuid",
        }

    def test_accepts_qualified_pydantic_declarations_and_literal_defaults(self, compiler):
        code = '''
import pydantic

class InputModel(pydantic.BaseModel):
    required: int = ...
    optional: bool = False
    described: str = pydantic.Field(default="value", description="description")
'''

        schema = compiler.to_json_schema(code)

        assert schema["required"] == ["required"]
        assert schema["properties"]["described"]["description"] == "description"

    def test_ignores_module_and_model_docstrings(self, compiler):
        code = '''
"""Generated catalog schema."""
from pydantic import BaseModel

class InputModel(BaseModel):
    """Input documentation."""
    value: str
'''

        assert compiler.to_json_schema(code)["properties"] == {
            "value": {"type": "string"}
        }

    @pytest.mark.parametrize(
        "code",
        [
            "from pathlib import Path",
            "import typing, subprocess",
            "from pydantic import BaseModel\nclass Plain(object):\n value: str",
            "from pydantic import BaseModel, Field\nclass InputModel(BaseModel):\n value: str = list()",
            "from pydantic import BaseModel\nclass InputModel(BaseModel):\n value = 1",
            "from pydantic import BaseModel\nclass InputModel(BaseModel):\n value: str = unknown",
            "from pydantic import BaseModel, Field\nclass InputModel(BaseModel):\n value: str = Field(description=unknown)",
            "from pydantic import BaseModel\nclass InputModel(BaseModel):\n value: bytes",
            "from pydantic import BaseModel\ndef factory():\n return InputModel",
        ],
    )
    def test_rejects_every_non_declarative_or_unsupported_construct(
        self, compiler, code
    ):
        assert compiler.to_json_schema(code) == {}

    @pytest.mark.parametrize(
        "code",
        [
            "import os\nfrom pydantic import BaseModel\nclass InputModel(BaseModel):\n x: str",
            "from pydantic import BaseModel\nclass InputModel(BaseModel):\n x: str\n open('/tmp/pwned', 'w')",
            "from pydantic import BaseModel\nclass InputModel(BaseModel):\n x: str\n while True: pass",
            "from pydantic import BaseModel\n@classmethod\nclass InputModel(BaseModel):\n x: str",
            "from pydantic import BaseModel\nclass InputModel(BaseModel):\n def run(self): return 1",
        ],
    )
    def test_rejects_executable_or_unapproved_code(self, compiler, code):
        assert compiler.to_json_schema(code) == {}

"""Runtime OpenAPI → operation-descriptor import (package-free connectors).

This subpackage turns an OpenAPI JSON spec into per-operation execution
descriptors (``execution`` with ``kind: "http"``) plus input/output JSON schemas, so connectors can
be added by dropping a spec + config with no codegen and no per-connector Python
package. The descriptors are consumed at runtime by
``infrastructure/adapters/openapi_http_executor.py``.
"""

from app.modules.connectors.infrastructure.openapi.spec_import import (
    OpenAPIOperation,
    build_operation_descriptors,
    build_raw_passthrough,
)

__all__ = [
    "OpenAPIOperation",
    "build_operation_descriptors",
    "build_raw_passthrough",
]

"""OpenAPI request bodies for streaming datastore multipart endpoints."""

from app.core.api.streaming_multipart import streaming_multipart_openapi

BINARY_FILE_RESPONSE = {
    200: {
        "description": "File bytes",
        "content": {
            "application/octet-stream": {
                "schema": {"type": "string", "format": "binary"}
            }
        },
    }
}


FILE_UPLOAD_OPENAPI = streaming_multipart_openapi(
    "DatastoreFileUploadRequest",
    properties={
        "data": {
            "type": "string",
            "format": "binary",
            "contentMediaType": "application/octet-stream",
            "title": "Data",
        },
        "name": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "description": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "directory_path": {"type": "string", "default": "/"},
        "search_enabled": {"type": "boolean", "default": True},
        "visibility": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    },
    required=["data"],
)

FILE_UPDATE_OPENAPI = streaming_multipart_openapi(
    "update",
    properties={
        "data": {
            "anyOf": [
                {
                    "type": "string",
                    "format": "binary",
                    "contentMediaType": "application/octet-stream",
                },
                {"type": "null"},
            ]
        },
        "path": {"type": "string"},
        "new_path": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "description": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "search_enabled": {"anyOf": [{"type": "boolean"}, {"type": "null"}]},
        "visibility": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    },
    required=["path"],
)

MARKDOWN_ATTACH_OPENAPI = streaming_multipart_openapi(
    "attach",
    properties={
        "data": {
            "type": "string",
            "format": "binary",
            "contentMediaType": "application/octet-stream",
        },
        "path": {"type": "string"},
        "images": {
            "type": "array",
            "default": [],
            "items": {
                "type": "string",
                "format": "binary",
                "contentMediaType": "application/octet-stream",
            },
        },
    },
    required=["data", "path"],
)

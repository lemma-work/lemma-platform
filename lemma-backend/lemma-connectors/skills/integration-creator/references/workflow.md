# Workflow

Use this when generating or regenerating a package in `lemma-connectors`.

## 1. Confirm the spec

- Put the provider spec in `lemma-backend/openapi_specs/<app>.json`.
- Confirm the app slug you want to expose under `src/lemma_connectors/<app>/`.

## 2. Generate transport, metadata, and canonical models

```bash
lemma-connectors/.venv/bin/python lemma-connectors/scripts/generate_openapi_metadata.py \
  --app <app> \
  --spec openapi_specs/<app>.json \
  --generated-client-output lemma-connectors/src/lemma_connectors/<app>/generated/client \
  --pydantic-models-output lemma-connectors/src/lemma_connectors/<app>/generated/pydantic_models.py \
  --pydantic-registry-output lemma-connectors/src/lemma_connectors/<app>/generated/pydantic_model_registry.json \
  --module-root lemma_connectors.<app>.generated.client.api \
  --output lemma-connectors/src/lemma_connectors/<app>/generated/openapi_metadata.json
```

This step is responsible for:

- spec sanitization
- success-response selection
- binary/file endpoint hints
- generated low-level client code
- canonical provider schema models
- tool metadata
- provider-specific post-processing when generated model types are technically
  valid but ergonomically wrong, such as Gmail base64url fields that must stay
  plain strings instead of `Base64Str`

## 3. Generate typed tool models

```bash
lemma-connectors/.venv/bin/python lemma-connectors/scripts/generate_tool_types.py \
  --metadata lemma-connectors/src/lemma_connectors/<app>/generated/openapi_metadata.json \
  --registry lemma-connectors/src/lemma_connectors/<app>/generated/pydantic_model_registry.json \
  --output lemma-connectors/src/lemma_connectors/<app>/generated/tool_types.py
```

This step should produce:

- typed tool input models
- typed tool output models
- `BinaryContentResult` outputs for file-like endpoints when needed

## 4. Generate resource operations

```bash
lemma-connectors/.venv/bin/python lemma-connectors/scripts/generate_resource_operations.py \
  --app <app> \
  --metadata lemma-connectors/src/lemma_connectors/<app>/generated/openapi_metadata.json \
  --output-dir lemma-connectors/src/lemma_connectors/<app>/resources
```

This step should produce:

- resource-grouped operation files
- logical operation names from cleaned provider `operationId`s
- operation docstrings that help agents choose the right action

## 5. Add or verify app client exports

Each app client should:

- inherit from `BaseInfoClient` or `BaseIntegrationClient`
- point at the generated metadata path
- point at the generated client module path
- call `self.register_resources(build_resources(self))`

If the package is new, also wire:

- `src/lemma_connectors/<app>/__init__.py`
- `src/lemma_connectors/__init__.py`
- any consuming factory or binding layer outside this package

## 6. Quality checklist

- operation names follow provider intent and are not path-noisy
- resource grouping feels natural
- output schemas are fully typed where the provider allows it
- binary/file endpoints expose `BinaryContentResult`
- url-safe/base64 provider wire fields remain usable as plain strings when
  eager decoding would break real responses
- descriptions avoid low-value filler
- excluded surfaces are absent
- large specs like Jira import cleanly after generation

## 7. Validation commands

```bash
./.venv/bin/python -m compileall lemma-connectors/src/lemma_connectors
./.venv/bin/pytest lemma-connectors/tests -q
```

If you are also wiring the main app integration layer, run the focused backend
tests too.

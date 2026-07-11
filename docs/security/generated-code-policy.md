# Generated-code policy

The committed OpenAPI specification is the source for the Python and TypeScript
clients, React L2 hooks, and browser bundles.

- Never hand-edit generated client files.
- Review the OpenAPI diff before reviewing generated churn.
- Run `lemma-python/scripts/generate_openapi_client.sh`,
  `lemma-typescript/scripts/generate_openapi_client.sh`, and the L2/bundle build.
- Commit the specification, generator configuration, SDK version changes, and
  all outputs together.
- CI regenerates clients and fails on drift. Generated sources are excluded
  from stylistic lint but remain subject to codegen-drift, build, and tests.
- Removing a public operation, schema, or compatibility alias requires a
  semver-major SDK/API release and migration notes.

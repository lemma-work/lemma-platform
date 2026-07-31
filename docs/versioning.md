# API and SDK versioning

Lemma's public API schema and generated SDK packages share a compatibility
line. While the platform is in beta, that line is the SemVer major and minor:

- A compatible API schema change bumps the API patch and the patch of each
  affected generated SDK package.
- An SDK-only change bumps only that SDK package patch. Its embedded API version
  remains the version of the schema from which it was generated.
- A breaking API change bumps the API and both SDK packages together to the next
  minor, for example `0.6.x` to `0.7.0`.
- After `1.0`, a breaking API change bumps the shared major compatibility line.

Package patches do not need to match. The API and both SDK packages must always
share the same major and minor versions.

The move from API `4.0.1` to `0.6.3` is a one-time beta normalization. It aligns
the previously independent API counter with the Python and TypeScript SDKs
without claiming a stable `4.x` compatibility contract before the platform has
reached `1.0`.

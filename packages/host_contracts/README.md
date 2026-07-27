# `cad.host/1` contracts

Language-neutral JSON Schema and golden envelopes for the bounded local Named
Pipe protocol between Desktop Agent and Managed AutoCAD Host.

The Phase 5 registry starts with bounded observation operations. Phase 6 adds
the strict, runtime-neutral `cad.program/0.2` create-only contract for layer,
line, circle, polyline, rectangle, text, and linear dimension operations.
Runtime, package, capability, registry hash, policy, preview, and execution
binding are server-selected protocol fields outside the semantic CAD Program.
There is no reflection dispatch, executable payload, assembly name, script,
raw AutoLISP, arbitrary path, or network address field.

`schemas/cad-program-0.2.schema.json` and
`program/golden/cad-program-0.2-digest-vector.json` are the language-neutral
schema and canonical SHA-256 vector. The `0.1` schema and golden program remain
unchanged for lab regression. Managed .NET interprets the allowlist directly;
it does not generate AutoLISP.

`payload_hash` is lowercase SHA-256 of canonical UTF-8 JSON: object keys sorted,
no insignificant whitespace, arrays kept in semantic order, and non-finite
numbers rejected.

All digests inside the Phase 6 execution binding use the explicit
`sha256:<64 lowercase hex>` representation. Legacy Agent capability/package
manifest hashes may remain raw 64-hex before the Gateway normalizes them at the
execution-binding boundary.

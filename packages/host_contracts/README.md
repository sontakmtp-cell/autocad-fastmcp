# `cad.host/1` contracts

Language-neutral JSON Schema and golden envelopes for the bounded local Named
Pipe protocol between Desktop Agent and Managed AutoCAD Host.

The Phase 5 registry starts with bounded observation operations and adds the
`cad.program/0.1` create-only contract after the managed read-only gate. The
program subset allows typed layer, line, circle, polyline, and text operations
with exact document, revision, runtime, package, preview, and execution-digest
binding. There is no reflection dispatch, executable payload, assembly name,
script, raw AutoLISP, arbitrary path, or network address field.

`schemas/cad-program-0.1.schema.json` and `program/golden/` are the
language-neutral write contract examples. Managed .NET interprets this
allowlist directly; it does not generate AutoLISP.

`payload_hash` is lowercase SHA-256 of canonical UTF-8 JSON: object keys sorted,
no insignificant whitespace, arrays kept in semantic order, and non-finite
numbers rejected.

# Shared AutoCAD contracts

`autocad_contracts.program` owns the strict, runtime-neutral
`cad.program/0.2` semantic contract. The seven create-only operations are
versioned by the immutable operation registry. Runtime, Host/package,
capability, registry hash, policy, preview, and execution bindings are not CAD
Program fields; they are selected by the Gateway and carried by typed
`cad.agent/2` commands.

Canonical program digest:

1. Strictly parse `cad.program/0.2`.
2. Materialize schema defaults and omit optional `null` fields.
3. Serialize UTF-8 JSON with object keys sorted, no insignificant whitespace,
   Unicode preserved, non-finite numbers rejected, and operation/vertex array
   order preserved.
4. Compute SHA-256 and prefix the lowercase hex digest with `sha256:`.

The JSON Schema snapshots are under `schemas/`. The cross-language canonical
vector is mirrored in
`../host_contracts/program/golden/cad-program-0.2-digest-vector.json`.
`cad.program/0.1` remains in `host_contracts` only for lab regression.

Phase 6 integration APIs:

- `program_command_payload(command)` returns the canonical command projection:
  `kind`, `effect_class`, exact execution `binding`, and only the typed
  program/preview/validation fields applicable to that command.
- `program_command_payload_hash(command)` computes the raw lowercase 64-hex
  `ProgramCommandMessage.payload_hash` from that projection. Gateway and Agent
  must use this helper rather than duplicate the projection.
- `normalize_sha256_digest(value)` converts a legacy lowercase raw capability
  or package hash to `sha256:<64hex>`. `ProgramExecutionBinding` itself is a
  strict wire boundary and rejects unprefixed digests.

`HelloMessage` and `HeartbeatMessage` expose optional, bounded Phase 6
presence fields on `cad.agent/2`: write lock, hard pause, active
document/revision, active job, support ID, mismatch reason, and
`outcome_unknown`. Existing `cad.agent/1` messages remain unchanged and reject
those Phase 6-only fields.

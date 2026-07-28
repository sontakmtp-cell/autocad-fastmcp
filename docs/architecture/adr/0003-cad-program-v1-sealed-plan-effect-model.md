# ADR 0003: CAD Program v1 sealed compiler and effect boundary

- Status: Accepted for Phase 8 core implementation
- Date: 2026-07-28
- Scope: CAD Program v1 source, Gateway compiler, execution admission and rollback

## Context

`cad.program/0.2` is a strict create-only contract. Phase 7 binds its preview,
trusted approval, execution, receipt and create-owned rollback records, but
`cad.rollback.checkpoint/1` does not contain a pre-image for an entity that was
modified or erased.

CAD Program v1 adds variables, expressions, bounded patterns and immutable
entity references. If Gateway, Desktop Agent and Managed Host each interpret
that source independently, a runtime difference can make the approved preview
describe a different effect from the committed operation.

## Decision

1. `cad.program/1.0` is an immutable authoring contract. Only the Gateway
   compiler evaluates its expression AST, normalizes units, expands patterns,
   materializes references and assigns stable operation/output IDs.
2. The compiler emits a bounded immutable `cad.execution-plan/1`. Preview,
   Phase 7 execution intent and trusted consent bind the source, compiler,
   expansion, effect-manifest and execution-plan digests.
3. Desktop Agent and Managed Host verify the sealed plan and all runtime,
   package, capability, registry, policy and budget pins. They never
   re-evaluate source expressions, re-expand patterns or select a fallback
   write runtime.
4. Canonical JSON, finite normalized numbers and explicit compiler/version
   hashes define digest behavior. Python and C# golden vectors are required
   before an effect-bearing Phase 8 pack can be enabled.
5. Operation registry entries declare an exact version, effect class, entity
   allowlist, output mapping, risk floor, budget contribution, capability key,
   validation strategy and checkpoint/restore strategy.
6. Create-only and create-equivalent operations may use checkpoint v1 only
   when every rollback target is a newly created entity owned by the
   checkpoint. Shared layer, style, block definition or other shared objects
   are never rollback targets.
7. In-place transforms require Host-generated checkpoint/restore v2 evidence
   for each enabled entity type. Checkpoint v1 and generic AutoCAD Undo are not
   valid rollback guarantees for modify or erase effects.
8. Patch and rebase always create new immutable lineage. A released,
   dispatched, running, `outcome_unknown` or terminal execution is never
   mutated in place and never inherits an old consent after compiler, plan,
   registry, runtime or policy changes.
9. Capability admission is granular by operation/version, entity type,
   runtime/release family and support state. Agent self-report and browser or
   model input cannot promote write capability.
10. Phase 8 keeps the existing public MCP tools. Operation packs remain
    internal capabilities; no tool-per-primitive surface is added.

## Effect classes and rollout

- Class A: create-only or create-equivalent. Phase 8 core may enable bounded
  copy, pattern, offset and other allowlisted create-as-new operations on
  Managed .NET R25 after exact preview/approval and checkpoint-v1 evidence.
- Class B: exact in-place transform. Move, rotate and scale remain disabled
  until checkpoint v2, validation, recovery and live rollback evidence pass
  for each entity type.
- Class C: topology-changing modification. Trim, extend, fillet, chamfer, join
  and explode require independent extension gates and remain disabled in the
  Phase 8 core rollout.
- Class D: erase/delete. Exact destructive binding and checkpoint-v2 restore
  evidence are mandatory; delete is not a Phase 8 core exit requirement and
  remains disabled.

## Consequences

- Preview and commit execute the same finite operation list.
- Compiler or capability changes invalidate affected previews and consents.
- `cad.program/0.2`, checkpoint v1 records and Phase 7 rollback semantics stay
  backward compatible and are never reinterpreted as v1.
- Managed .NET R25 / AutoCAD Mechanical 2025 is the first live write target.
  LT write stays off, and ezdxf remains non-authoritative for live DWG commit.
- Phase 8 core cannot be declared complete without live create-equivalent and
  exact-transform evidence, including operation-appropriate rollback.

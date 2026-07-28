# Phase 8 independent security and architecture review

## Review decision

**Overall decision: NO-GO for every effect-bearing Phase 8 flag.**

Slice 8.0 may proceed. Slice 8.1 may proceed only as compile-only work after the
digest and artifact-reference contracts below are frozen. Slices 8.2 through
8.5 remain NO-GO until their applicable findings are closed with automated and
live R25 evidence. Slice 8.6 remains explicitly disabled and is not covered by a
future-looking design statement alone.

This is a design and boundary review, not a claim that the current Phase 0-7
implementation is exploitable. The reviewed Phase 0-7 code is deliberately
narrow: `cad.program/0.2` is strict and create-only, Managed Host dispatch is
allowlisted, Phase 7 approval and release are owner-scoped and CAS-protected,
write has no runtime fallback, and rollback checkpoint v1 erases only the
created entities it owns. Phase 8 increases authority enough that these
properties must become explicit v1 contracts rather than assumptions carried
forward from the smaller v0.2 operation set.

## Scope and threat model

Reviewed boundaries:

- public MCP input and resource surface;
- Gateway source validation, persistence, admission, approval and release;
- Desktop Agent payload verification, runtime selection and retry behavior;
- Managed Host parsing, operation dispatch, transaction, receipt and rollback;
- Portal recent-auth approval;
- Phase 8 source/compiler/plan, capability, patch/rebase and checkpoint-v2
  design.

Threat actors and failure sources considered:

- an untrusted model crafting CAD Program source and MCP arguments;
- browser content or an ordinary Portal user attempting to supply trusted
  fields;
- a compromised or stale Agent self-reporting capabilities;
- a tampered, copied or adversarial DWG containing durable records;
- replay, concurrent patch/rebase/release and crash/drop faults;
- cross-owner, cross-device, cross-document and cross-revision reference reuse;
- compiler, registry, runtime, package, policy or rollout-flag drift;
- accidental fallback or approximation across Managed .NET, AutoLISP/LT and
  ezdxf.

Out of scope:

- production code changes;
- enabling delete, trim, fillet, chamfer, topology packs or LT write;
- customer-pilot external gates such as CA signing and OAuth production
  lifecycle, except where they intersect capability provenance.

## Confirmed Phase 0-7 boundaries to preserve

| Boundary | Current evidence | Security property Phase 8 must preserve |
|---|---|---|
| Strict create-only source | `packages/contracts/src/autocad_contracts/program.py:32-33`, `:141-163`, `:278-365` | Unknown fields and operations fail closed; budgets are checked before dispatch. |
| No arbitrary Host operation | `native/autocad_managed_host/src/AutocadMcp.Host.R25/AutoCadProgramOperations.cs:31-45`, `:70-101`; negative tests at `native/autocad_managed_host/tests/AutocadMcp.Host.Core.Tests/CadProgramV02Tests.cs:32-51` | Host executes an exact typed registry, never a command name, path, assembly, script or reflective type supplied by the model. |
| Exact runtime and no write fallback | `apps/desktop_agent/src/autocad_desktop_agent/runtime/broker.py:91-154` | Write stays on the approved Managed .NET R25 runtime and fails closed on any pin/capability mismatch. |
| Payload and document binding | `apps/desktop_agent/src/autocad_desktop_agent/program_executor.py:48-64`, `:85-101`, `:124-165` | Agent verifies payload hash, deadline, runtime and active document/revision before one narrow Host dispatch. |
| Trusted approval and one-time release | `services/gateway/src/autocad_gateway/phase7_admission.py:371-419`, `:421-524`, `:935-997`; immutable binding trigger at `services/gateway/src/autocad_gateway/infrastructure/sqlite/migrations/0006_phase7_approval_recovery_rollback.sql:216-250` | Model cannot approve; Portal/device decisions bind one exact intent and release is atomic with consent consumption. |
| Atomic create receipt/checkpoint | `native/autocad_managed_host/src/AutocadMcp.Host.R25/AutoCadProgramOperations.cs:485-580` | Effect, receipt and checkpoint commit in one DWG transaction; exact duplicate returns prior evidence. |
| Checkpoint-v1 ownership | `packages/contracts/src/autocad_contracts/phase7.py:472-563`; Host rollback at `native/autocad_managed_host/src/AutocadMcp.Host.R25/AutoCadProgramOperations.cs:283-378` | v1 contains created entities only and rollback revalidates then erases those exact entities. It is not a modify/delete restore mechanism. |
| Public surface remains small | `services/gateway/src/autocad_gateway/app.py:1150-1375`; regression tests at `services/gateway/tests/test_phase6_gateway.py:1050-1077` and `services/gateway/tests/test_phase7_gateway_admission.py:1025-1044` | Approval is not an MCP tool; rollback accepts IDs, not raw handles; no primitive-per-tool expansion. |

## Findings

### S8-001 — Exact approved bytes are not yet a complete cross-boundary contract

**Severity: Critical**

**Risk**

Phase 8 says approval binds the sealed plan and effect digest, but the proposed
fields are not yet defined as one canonical, domain-separated execution
envelope. A Python/C# omission or alternate projection could let preview,
approval and commit refer to different expanded operations while every local
hash comparison still succeeds.

**Evidence**

- Phase 8 lists source, compiler, expansion, effect and execution-plan digests
  at `docs/architecture/Phase-8.md:240-250` and forbids Agent/Host
  reinterpretation at `:252-258`.
- The current `ProgramExecutionBinding` has only program/execution/document and
  runtime-policy pins; it has no source revision, compiler, sealed-plan,
  expansion, effect, validation-profile or checkpoint-strategy digest:
  `packages/contracts/src/autocad_contracts/agent_protocol.py:673-690`.
- The current preview digest projection omits the binding's
  `execution_digest`: `packages/contracts/src/autocad_contracts/agent_protocol.py:705-733`.
  Phase 7 compensates by separately recording preview and commit execution
  digests in the intent (`packages/contracts/src/autocad_contracts/phase7.py:147-179`),
  but that is not a sufficient specification for new Phase 8 plan/effect
  fields.

**Required change**

Freeze one versioned execution-binding contract before write implementation.
Its canonical projection must include, at minimum:

- source program ID, revision, schema and digest;
- compiler ID, version and executable/package hash;
- sealed plan schema and digest;
- expansion and effect-manifest digests;
- exact target-set/materialized-reference digest;
- validation-profile and checkpoint-strategy digests;
- hard budgets;
- runtime, package, operation-registry, capability, policy and rollout-policy
  pins;
- document/snapshot/revision binding;
- action, preview ID/expiry and deterministic receipt ID where applicable.

Use explicit digest domains such as `cad.source/1`, `cad.execution-plan/1`,
`cad.effect-manifest/1`, `cad.preview-binding/2` and
`cad.execution-intent/2`; do not hash ad hoc dictionaries under a shared
generic domain. Gateway, Agent and Host must recompute the same canonical
digests from strict typed records. Host must receive the sealed plan, not the
v1 source.

**Required tests**

- Python/C# golden vectors for every digest domain, including numeric and
  Unicode edge cases.
- One mutation test per field above; each mutation must fail before Host
  effect.
- Reordering, omitted-default, unknown-field, duplicate-key, `-0`, exponent,
  precision, NaN/Infinity and unit-normalization vectors.
- Preview from compiler/registry/policy version N followed by commit under N+1
  must invalidate consent and dispatch no job.
- Agent and Host must reject a source payload when only a sealed plan is
  allowed.

**GO/NO-GO**

- Compile-only Slice 8.1: GO after the contract and golden vectors are frozen.
- Any Phase 8 preview/commit: NO-GO until all three runtimes verify the same
  binding and every mutation test is green.

### S8-002 — Artifact and component references could reintroduce path, URL or command authority

**Severity: High**

**Risk**

`cad_prepare_program` is planned to accept a “bounded artifact reference” and
the source may contain pinned components. Without an exact opaque-reference
contract, these fields can become file paths, UNC/device paths, URLs, archive
members, DLL names or command strings. That would bypass the current
path-free, operation-only boundary and create local file disclosure, SSRF,
path traversal or code-loading opportunities.

**Evidence**

- Arbitrary code and arbitrary file/path/network/environment access are
  explicit non-goals at `docs/architecture/Phase-8.md:185-186`.
- Pinned components and artifact references are introduced at
  `docs/architecture/Phase-8.md:264` and `:437-439`, but the accepted reference
  syntax and resolver trust boundary are not specified.
- The current public tool accepts inline operation data, not a path or URL:
  `services/gateway/src/autocad_gateway/app.py:1150-1190`.
- The current Host parser rejects both an unknown top-level `path` and a
  `load_assembly` operation:
  `native/autocad_managed_host/tests/AutocadMcp.Host.Core.Tests/CadProgramV02Tests.cs:32-51`.

**Required change**

Define artifact/component references as opaque Gateway IDs plus owner,
content-type, byte length and SHA-256 digest. Resolution must occur only from
owner-scoped Gateway storage. The model-visible contract must reject schemes,
slashes, backslashes, drive prefixes, hostnames, archive paths and executable
content types. No source-provided path, URL, environment variable, command,
assembly, class/type name or plugin ID may reach Agent or Host. Materialize and
validate content before compilation; seal only the materialized digest into the
plan.

If external fetching is ever desired, it requires a separate phase and
security review; it must not be smuggled into “artifact reference.”

**Required tests**

- Reject `file:`, `http:`, `https:`, UNC, `\\.\`, drive-letter, ADS, traversal,
  symlink, archive-slip and mixed-encoding variants in every reference field.
- Cross-owner artifact/component lookup returns `not_found`.
- Digest mismatch, content-type mismatch, oversized content, deeply nested
  JSON and compressed-bomb inputs fail before compiler allocation grows beyond
  budget.
- Assert Agent and Host wire schemas contain no path/URL/command/module/type
  field.
- Static tests continue to reject `eval`, reflection, arbitrary command APIs
  and assembly/process loading in Phase 8 execution code.

**GO/NO-GO**

- Inline-source compiler work: GO.
- Artifact/component reference support: NO-GO until the opaque resolver and
  negative matrix are implemented.

### S8-003 — Checkpoint-v2 restore payload provenance and parser rules are underspecified

**Severity: Critical**

**Risk**

A restore descriptor has authority to recreate or replace DWG objects. Merely
calling it “Host-generated” and adding a digest does not define which copy is
authoritative, how tampered DWG records are detected, which typed fields are
allowed, or whether runtime type names/binary serialization can be interpreted.
A forged, copied or stale descriptor could restore attacker-selected geometry,
replace the wrong object, corrupt shared dependencies or become a deserialization
boundary.

**Evidence**

- Phase 8 lists Host-generated restore evidence and forbids model/browser raw
  restore payloads at `docs/architecture/Phase-8.md:352-356`, but does not
  define the authoritative storage copy, parser union or provenance chain.
- Current checkpoint v1 has a strict typed digest projection:
  `packages/contracts/src/autocad_contracts/phase7.py:479-563`.
- Current commit creates the checkpoint from live Host objects and writes
  effect, receipt and checkpoint in one transaction:
  `native/autocad_managed_host/src/AutocadMcp.Host.R25/AutoCadProgramOperations.cs:553-580`.
- Current rollback compares the Gateway-supplied checkpoint digest, document
  revision and fingerprints before erasing:
  `native/autocad_managed_host/src/AutocadMcp.Host.R25/AutoCadProgramOperations.cs:210-279`,
  `:335-378`.
- Current negative tests reject injected handles and unknown checkpoint fields:
  `native/autocad_managed_host/tests/AutocadMcp.Host.Core.Tests/Phase7RollbackTests.cs:10-30`,
  `:47-71`.

**Required change**

Define checkpoint v2 as a strict discriminated union per entity type and restore
strategy. Each descriptor must bind:

- checkpoint/descriptor schema and strategy version;
- source plan/effect/operation ID and digests;
- owner, device, document, space, snapshot and before/after revisions;
- stable target ref, exact pre-effect fingerprint and mandatory dependency
  closure digest;
- runtime/package/registry/compiler/policy/rollout pins;
- bounded typed pre-image fields and restore budget;
- descriptor digest and whole-checkpoint digest.

The Host must generate the descriptor from live objects inside the same
transaction that applies the effect. Gateway must persist the exact canonical
checkpoint bytes/digest as an immutable owner-scoped record. Restore must
compare the DWG record to that Gateway-pinned digest and then revalidate live
target/dependency state. No binary formatter, arbitrary DXF blob, class name,
reflection, handle-only target, command string or source-provided descriptor is
allowed. Restore effect plus restore receipt must commit atomically.

**Required tests**

- Reject model/browser-supplied descriptor fields at MCP, Gateway and Agent.
- Tamper one descriptor field, the descriptor digest, the checkpoint digest,
  and both DWG-side descriptor+digest; all must fail against Gateway evidence.
- Cross-owner/device/document/space/revision/entity replay must fail.
- Unsupported/custom/proxy/vertical object types fail `capability_missing`.
- Dependency deletion/change, fingerprint drift, duplicate target, missing
  target and target-type change fail conflict-safe.
- Maximum-size, excessive-property, deep-container and malformed numeric
  descriptors remain bounded.
- Crash/drop at every point before/after effect, checkpoint, receipt and result
  serialization proves atomicity and recoverability.
- Restore duplicate returns the original receipt; same ID with changed payload
  fails `duplicate_payload_mismatch`.

**GO/NO-GO**

- Slice 8.4 and every in-place transform/delete/topology operation: NO-GO until
  the v2 POC passes this matrix on R25.
- Checkpoint v1 remains GO only for create-owned entities and must not be
  reinterpreted.

### S8-004 — Destructive and transform target binding lacks a mandatory dependency closure

**Severity: Critical**

**Risk**

Phase 8 makes dependency evidence optional in the generic stable reference.
That is too weak for move/scale/mirror-in-place, delete and topology-changing
operations. Exact target identity without exact dependency identity can still
modify the wrong semantic object, break associative dimensions/blocks, consume
different cutters, or restore into a changed dependency graph.

**Evidence**

- The proposed stable ref includes “optional dependency evidence” at
  `docs/architecture/Phase-8.md:299-303`.
- Class B requires before fingerprints, while Class C names targets/cutters
  and dependencies, and Class D bans broad predicates:
  `docs/architecture/Phase-8.md:323-342`.
- Current public rollback intentionally accepts receipt/checkpoint/plan IDs,
  not raw entity handles: `services/gateway/src/autocad_gateway/app.py:1267-1339`.
- Current v1 Host obtains handles only from the authenticated checkpoint and
  revalidates each fingerprint immediately before erase:
  `native/autocad_managed_host/src/AutocadMcp.Host.R25/AutoCadProgramOperations.cs:335-363`.

**Required change**

Create operation-specific target contracts. For every effect-bearing operation,
the sealed plan and effect manifest must contain an ordered canonical
`target_set_digest` covering:

- exact stable refs for every target, cutter, source and dependency;
- required entity/type/space/fingerprint and source snapshot/revision;
- expected cardinality and role of each ref;
- operation-specific tolerance and result-mapping rules;
- shared-object/dependency exclusions;
- expected create/modify/erase counts and maximums.

Dependency evidence may be optional only for a registry entry whose typed Host
implementation proves it has no relevant dependencies. It is mandatory for
every destructive/topology target and for associative or shared objects.
Commit resolves no predicate and performs no fresh selection. A changed,
missing, duplicated or additional target must invalidate preview and consent.

**Required tests**

- Cross-owner/device/document/snapshot/revision refs and raw handle/ObjectId
  inputs fail before dispatch.
- Same handle with changed fingerprint/type/space/dependencies fails.
- Target-set reorder, duplicate, omission, extra target and role swap mutate
  the digest and fail.
- Broad query/predicate resolves once at compile time; adding a matching entity
  after preview does not widen commit.
- Topology cutters, result mapping and partial-failure injection are tested per
  operation/entity type.
- Shared layer/style/block/associative dependency preservation is asserted.

**GO/NO-GO**

- Create-as-new operations with no source mutation: conditional GO after source
  refs are exact and source fingerprints are revalidated.
- In-place transform: NO-GO until target/dependency binding and checkpoint v2
  are green.
- Delete/trim/fillet/chamfer/topology: explicit NO-GO until each operation's
  separate extension gate passes.

### S8-005 — Capability self-report is self-consistent, not independently attested

**Severity: High**

**Risk**

The design correctly says Agent self-report is insufficient, but it does not
define the signed evidence issuer, validity, revocation or server-side
intersection. Current Phase 0-7 code proves a reported manifest hashes to
itself and matches the compiled v0.2 registry; it does not independently prove
that each future operation/entity/runtime combination passed conformance.

**Evidence**

- Phase 8 requires server allowlist, signed package manifest, current evidence
  and cohort policy at `docs/architecture/Phase-8.md:412-422`.
- `HelloMessage` verifies only that the supplied capability manifest hash
  matches its supplied content:
  `packages/contracts/src/autocad_contracts/agent_protocol.py:315-417`.
- Gateway chooses the R25 candidate from that connection manifest and pins its
  values at `services/gateway/src/autocad_gateway/program_services.py:554-623`.
- Agent intersects the binding with its locally observed manifest and compiled
  operation-registry hash at
  `apps/desktop_agent/src/autocad_desktop_agent/runtime/broker.py:118-153`;
  this is valuable defense in depth but remains device-side evidence.

**Required change**

Add an immutable server-owned capability-evidence record signed or ingested
from a trusted release/conformance authority. It must bind operation/version,
entity type, preview/commit/rollback support, runtime/host/release/vertical,
Agent+Host package hashes, registry/compiler/policy versions, evidence suite
version, issued/expiry/revocation state and allowed cohort. Effective write
capability is the intersection of:

1. default-off flags and operation-pack allowlist;
2. server policy/cohort;
3. trusted non-expired capability evidence;
4. signed package provenance;
5. current Agent/Host self-report;
6. exact plan requirements.

Self-report can remove capability immediately; it can never add capability
beyond server evidence.

**Required tests**

- Self-reporting `certified`, a new operation key or a broader entity type does
  not enable write.
- Expired/revoked evidence, changed package hash, wrong host family/release,
  stale registry/compiler version and wrong cohort fail closed.
- Capability removal between preview, approval and release invalidates consent
  and dispatches no job.
- A Host reporting a capability absent from the Agent or Gateway intersection
  still fails.
- LT and ezdxf can never satisfy an R25 live-write evidence requirement.

**GO/NO-GO**

- Contract-only capability schema: GO.
- Any Phase 8 write pack: NO-GO until independent evidence is enforced at
  Gateway and rechecked by Agent/Host.

### S8-006 — Rollout flags and fallback need an immutable policy epoch in the approved binding

**Severity: High**

**Risk**

Flags are default-off and write fallback is forbidden, but the design does not
state that the exact flag/allowlist/cohort decision is digested into the plan
and revalidated at release and Host admission. A static policy version can
remain unchanged while an operator disables a pack, changes an allowlist or
removes a cohort. An already approved execution must not survive that change,
nor downgrade to another runtime or operation approximation.

**Evidence**

- Phase 8 defines independent flags and allowlist at
  `docs/architecture/Phase-8.md:501-516` and says no write fallback at
  `:424-431`.
- The current binding carries one string `policy_version`:
  `packages/contracts/src/autocad_contracts/agent_protocol.py:673-690`.
- Current Agent write selection has no fallback and requires exact R25,
  manifest, registry and policy pins:
  `apps/desktop_agent/src/autocad_desktop_agent/runtime/broker.py:91-154`.
- Phase 8's own NO-GO list requires old consent invalidation on compiler,
  runtime or registry change, but does not explicitly include every pack flag,
  allowlist and cohort mutation: `docs/architecture/Phase-8.md:693-711`.

**Required change**

Generate a canonical `rollout_policy_digest`/monotonic epoch from every
effect-bearing flag, operation-pack allowlist, runtime/entity allowlist,
capability-evidence set and cohort decision. Pin it in source admission, sealed
plan, preview, intent, consent, Agent binding, Host receipt and checkpoint.
Recompute it before preview dispatch, consent decision, release and Host
admission. Disabling a pack must invalidate outstanding previews/consents and
block unreleased jobs. There is no downgrade execution of a v1 plan as v0.2 and
no write fallback to LT/AutoLISP/ezdxf.

**Required tests**

- Toggle each Phase 8 effect flag after prepare, preview and approval; no write
  may dispatch under the stale epoch.
- Remove one operation/entity/runtime/cohort allowlist entry at the same
  boundaries.
- Add a fallback runtime or make R25 unavailable after preview; commit fails
  without alternate dispatch.
- Disable compiler/registry/package version and verify old consent cannot be
  consumed.
- Disabling v1 leaves `cad.program/0.2` create-only and LT read behavior
  unchanged.

**GO/NO-GO**

- Slice 8.0/compile-only: GO with all effect flags off.
- Any effect-bearing flag: NO-GO until policy-epoch invalidation tests pass.

### S8-007 — Patch/rebase immutability is not yet protected against release races

**Severity: High**

**Risk**

“Create a new revision” prevents ordinary in-place edits but does not by itself
prevent a concurrent patch/rebase, preview invalidation, consent decision and
release from observing different lineage states. Application checks without
database constraints can regress later. A released job must remain tied to its
exact old revision while every new revision gets a new plan, preview and
consent.

**Evidence**

- Phase 8 requires new immutable revisions and forbids mutation of released,
  dispatched, running, `outcome_unknown` or terminal execution at
  `docs/architecture/Phase-8.md:373-383`.
- Current `cad_program_revisions` has a `(program_id, revision)` primary key but
  no no-update/no-delete trigger:
  `services/gateway/src/autocad_gateway/infrastructure/sqlite/migrations/0005_phase6_programs.sql:23-61`.
- Current Phase 7 intent has an immutable-binding trigger:
  `services/gateway/src/autocad_gateway/infrastructure/sqlite/migrations/0006_phase7_approval_recovery_rollback.sql:216-250`.
- Current release consumes consent and releases one deterministic job through a
  CAS transaction:
  `services/gateway/src/autocad_gateway/phase7_admission.py:935-997`.

**Required change**

Make source revisions, sealed plans, materialized refs, patch records, rebase
reports and released execution bindings append-only at the database layer.
Patch/rebase creation must CAS against exact parent revision/digest and base
snapshot. It must never update old source/plan/preview/intent/consent/job rows.
Release must atomically verify the exact source/plan/effect/policy digests and
that no invalidation exists for that execution. New lineage may coexist with an
already released old lineage, but cannot replace or redirect it.

Use explicit parent revision/digest, patch digest, rebase base/new snapshot
digests and conflict-report digest. Destructive conflicts are terminal for that
rebase; no semantic auto-merge.

**Required tests**

- Concurrent identical patch is idempotent; same key with different patch
  conflicts.
- Patch-vs-release, rebase-vs-approval, invalidation-vs-release and two-parent
  CAS races have one deterministic winner.
- Direct SQL update/delete of immutable source, plan, ref, intent, consent,
  evidence, receipt and checkpoint records fails.
- Patch of released/running/`outcome_unknown`/terminal execution creates new
  lineage and does not alter the old job or recovery evidence.
- Rebase with stale/missing/type-changed/fingerprint-changed refs emits a
  bounded conflict report and no preview.

**GO/NO-GO**

- Slice 8.3: NO-GO until schema constraints and concurrency tests prove
  append-only lineage.
- Existing v0.2 revision 1 and Phase 7 records must remain readable unchanged.

### S8-008 — Replay/idempotency identity is not defined for modify, restore and multi-effect plans

**Severity: Critical**

**Risk**

Repeating create with the same preview can safely return the existing receipt.
Repeating move, rotate, scale or restore can apply a second effect if the
idempotency identity omits the exact target set, plan/effect digest or
checkpoint strategy. A crash after DWG commit but before result delivery is the
normal case that exposes this error.

**Evidence**

- Current create receipt ID is derived from the exact preview and the Host
  compares program/execution/document binding before returning a duplicate:
  `native/autocad_managed_host/src/AutocadMcp.Host.R25/AutoCadProgramOperations.cs:485-525`,
  `:1199-1211`.
- Current effect, receipt and checkpoint are committed atomically at
  `native/autocad_managed_host/src/AutocadMcp.Host.R25/AutoCadProgramOperations.cs:535-580`.
- Agent serializes per document and sends a started command once:
  `apps/desktop_agent/src/autocad_desktop_agent/program_executor.py:91-112`;
  Phase 7 recovery tests cover `outcome_unknown`, but Phase 8 has no v2
  idempotency projection yet.
- Phase 8 asks for a duplicate/drop matrix at
  `docs/architecture/Phase-8.md:607-617`, without defining the durable
  identity fields.

**Required change**

Define a canonical `effect_identity_digest` for every commit and restore. It
must include action, sealed-plan/effect/target-set digests, document before
revision, deterministic receipt ID, checkpoint strategy/version, operation
pack/version and all runtime/policy/rollout pins. The Host durable ledger key
must be deterministic from the approved execution, not a caller-chosen raw
idempotency key. Existing receipt with identical identity returns the original
result without executing; any mismatch under the same key fails closed.

For modify/delete, effect+receipt+checkpoint-v2 must be one transaction. For
restore, restored effect+rollback receipt must be one transaction. After a
started or unknown write, recovery may query the exact durable receipt only; it
must never resubmit the effect.

**Required tests**

- Drop before Host admission, after transaction open, after each effect, after
  checkpoint/receipt materialization, after commit, during serialization and
  after Agent/Gateway receipt persistence.
- Exact duplicate before and after Agent/Gateway restart has no second effect.
- Same receipt/idempotency ID with changed plan, effect, target, checkpoint,
  runtime, policy or rollout pin fails `duplicate_payload_mismatch`.
- Two concurrent exact commits and two concurrent restores produce one effect
  and one receipt.
- `outcome_unknown` retains the document write lock until exact receipt
  reconciliation or explicit safe resolution.

**GO/NO-GO**

- Create-equivalent Slice 8.2: NO-GO until its expanded multi-output identity
  and full drop matrix pass.
- Slices 8.4/8.5 and all destructive extensions: NO-GO until v2 commit/restore
  matrices pass live R25 fault drills.

### S8-009 — Trusted risk/effect summaries need a normative authority rule

**Severity: High**

**Risk**

The model controls source labels, text and operation arguments. The browser
renders approval information. If any model-authored description, requested risk
or client-computed count enters the trusted summary, an operator can approve a
misleading description even though the underlying plan is different. The
browser must convey a Gateway/Host-derived decision, not become the authority
for owner, risk, target or approval proof.

**Evidence**

- Phase 8 says registry entries carry a risk floor
  (`docs/architecture/Phase-8.md:305-307`) and UI cannot force runtime,
  capability, risk, owner, handles or rollback payload (`:497-499`), but does
  not define the risk aggregation algorithm or trusted-summary source.
- Current Gateway derives static effect summaries from stored program and Host
  preview counts:
  `services/gateway/src/autocad_gateway/phase7_admission.py:1336-1368`.
- Current Portal refetches consent+intent server-side, submits only exact
  digest/version/nonce, and Gateway revalidates:
  `apps/web_portal/src/lib/consent-route.ts:18-55`,
  `services/gateway/src/autocad_gateway/phase7_admission.py:371-419`.
- Current UI explicitly labels Gateway information immutable and rejects
  model-authored descriptions as trusted evidence:
  `apps/web_portal/src/components/ConsentApproval.tsx:67-91`.

**Required change**

Specify that effective risk is the maximum of all registry operation floors,
effect class, target count/type, checkpoint strategy, policy/cohort floor and
any unresolved validation condition. Source/browser input may request stricter
risk but can never lower it. Trusted effect summaries must be generated from
the sealed effect manifest and verified Host preview evidence using fixed
templates; model-authored free text is untrusted and visually separated.

Approval must bind the complete plan/effect/risk/target/checkpoint summary
digests. Portal may transport the one-time decision after recent auth, but
Gateway owns owner/risk/targets and performs the final CAS/revalidation. No
`confirm=true`, hidden handle, restore blob or client-computed proof is accepted.

**Required tests**

- Source fields named `risk`, `owner`, `approved`, `confirm`, `handles`,
  `restore_payload` and misleading “safe/no changes” descriptions are rejected
  or treated as untrusted text.
- Effective risk cannot be lower than any operation/effect/policy floor.
- Host preview count/target mismatch invalidates the intent.
- HTML/control/path/secret-like source text cannot enter the trusted summary.
- Stale auth, CSRF, replay, wrong owner, changed intent digest/version/nonce and
  concurrent decisions dispatch no second job.

**GO/NO-GO**

- Compile-only: GO.
- Any approval-backed Phase 8 write: NO-GO until normative risk derivation and
  trusted-summary binding tests pass.

### S8-010 — Public MCP compatibility needs an exact Phase 8 snapshot, not only a naming rule

**Severity: Medium**

**Risk**

Keeping tool names stable is necessary but insufficient. Adding trusted
runtime/risk/owner/handle/restore fields to an existing tool would expand model
authority without adding a new primitive tool. Changing annotations or
removing v0.2 input compatibility could also regress Phase 0-7 behavior.

**Evidence**

- Phase 8 keeps the current tool set and routes v1 source, patch and rebase
  through `cad_prepare_program` at `docs/architecture/Phase-8.md:433-439`.
- Current Phase 6 has an exact tool/resource snapshot regression:
  `services/gateway/tests/test_phase6_gateway.py:1050-1077`.
- Current Phase 7 test verifies rollback tool presence, no approval tool and no
  raw handles, but does not snapshot the entire Phase 7 surface:
  `services/gateway/tests/test_phase7_gateway_admission.py:1025-1044`.

**Required change**

Freeze exact public snapshots per profile for tool names, descriptions,
annotations, input/output schema hashes and resource templates. Phase 8 may add
a strict discriminated request mode inside `cad_prepare_program`, but must keep
`cad.program/0.2` behavior and must not expose runtime, owner, risk, raw
handle/ObjectId, restore descriptor, capability elevation, approval proof or
command/path knobs. Large records remain owner-scoped resources.

Add an explicit denylist assertion for primitive tool names and sensitive input
fields. Preserve `cad_preview`, `cad_commit`, `cad_validate`,
`cad_preview_rollback` and `cad_commit_rollback`; approval remains outside MCP.

**Required tests**

- Exact Phase 6, Phase 7 and Phase 8 profile snapshots.
- Existing v0.2 golden requests pass byte-for-byte semantic validation.
- No `cad_move`, `cad_rotate`, `cad_scale`, `cad_delete`, `cad_trim`,
  `cad_fillet`, `cad_chamfer`, `cad_approve` or equivalent alias appears.
- Recursive schema scan rejects owner/risk/runtime/handle/ObjectId/restore
  payload/command/path fields exposed as trusted controls.
- Owner-scoped resource ID guessing returns `not_found`.

**GO/NO-GO**

- Public Phase 8 profile: NO-GO until exact snapshots and denylist tests pass.
- Internal operation-pack growth remains permitted behind the unchanged public
  tools.

## Slice gate matrix

| Slice | Decision from this review | Required finding closure |
|---|---|---|
| 8.0 baseline evidence and contract freeze | GO | Keep all effect flags off; preserve current snapshots and v0.2 fixtures. |
| 8.1 source/compiler/sealed plan | CONDITIONAL GO, compile-only | S8-001 and S8-002 contracts/golden vectors; no AutoCAD write. |
| 8.2 create-equivalent R25 | NO-GO | S8-001, S8-004 for source refs, S8-005, S8-006, S8-008, S8-009, S8-010. Checkpoint v1 only for entities created and owned by that execution. |
| 8.3 snapshot refs/patch/rebase | NO-GO | S8-002, S8-004 and S8-007. |
| 8.4 checkpoint-v2 POC | NO-GO | S8-001, S8-003, S8-004, S8-005, S8-006, S8-008. No public modify enablement. |
| 8.5 exact transforms | NO-GO | All Critical/High findings plus live R25 preview/commit/recovery/validation/rollback evidence per entity type. |
| 8.6 destructive/topology extensions | NO-GO, remain disabled | All findings; separate operation-by-operation security review, fault matrix and explicit GO. |
| 8.7 cross-runtime conformance | GO for fixtures/negative tests only | No portable/live-write claim without exact operation/version/entity/runtime evidence. LT write remains off. |

## Mandatory final GO criteria

Phase 8 core is GO only when all of the following are evidenced:

1. All Critical and High findings above are closed in code and tests.
2. Gateway, Agent and Host independently verify the same source/compiler/plan/
   expansion/effect/target/checkpoint/policy digests.
3. No model/browser field can choose owner, trusted risk, capability, runtime,
   raw target, restore payload, path, command or approval proof.
4. Capability is independently attested and intersected with current
   self-report, package provenance, flags, allowlist and cohort policy.
5. Patch/rebase storage is append-only and race-tested against approval/release.
6. Crash/drop and duplicate matrices prove exactly one effect and durable
   reconciliation without blind retry.
7. At least one create-equivalent pack and one exact transform pack pass live
   Mechanical 2025 R25 preview, trusted approval, commit, receipt, recovery,
   validation and operation-appropriate rollback.
8. `cad.program/0.2`, Phase 0-7 tests, LT read, public MCP snapshots and
   checkpoint-v1 semantics remain unchanged.
9. All unsupported operation/entity/runtime combinations fail
   `capability_missing`; no write fallback or silent approximation occurs.

Delete, trim, fillet, chamfer and other topology packs remain NO-GO regardless
of Phase 8 core status until each separate extension gate proves exact target
semantics, checkpoint-v2 restore, atomic receipt/checkpoint, conflict-safe
rollback, trusted approval, automated fault injection, live R25 acceptance and
an updated independent security review.

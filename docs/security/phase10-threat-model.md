# Phase 10 threat model

Status: accepted implementation boundary; integrated/headless validation
passed; live acceptance partial.

## Assets and trust boundaries

Protected assets are owner isolation, immutable snapshots/scenes, document
revision truth, drawing privacy, bounded Gateway capacity and the Phase 6–9
approval/write/recovery authority.

Untrusted inputs include OAuth callers, scene IDs/cursors/filters, snapshot
geometry, DWG text/attributes/layer/block names, Agent/Host payloads and
workflow inputs. FastMCP and Portal are public boundaries; Gateway policy and
owner-scoped repositories are enforcement boundaries; Agent/Host are source
fact providers, not inference or approval authorities.

## Threats and required controls

| Threat | Control | Required evidence |
|---|---|---|
| Cross-owner scene/snapshot/resource IDOR | Owner-filter every lookup before payload access; safe `not_found` | Build/query/resource/Portal/workflow IDOR tests |
| Cursor forgery or replay across filters | HMAC-SHA256 cursor bound to owner, scene, section, filter, offset and projection | Bit-flip and every-binding mismatch tests |
| Entity-order/digest manipulation | Server canonicalization, finite values, sorted evidence, domain-separated SHA-256 | Shuffled key/entity golden vectors |
| Conflicting duplicate publication | Atomic immutable insert and dedup key; conflict reject | Repository replay/conflict/restart tests |
| Dense overlap or huge polyline DoS | Hard entity/byte/cell/candidate/relation/time limits; no all-pairs fallback | Dense overlap, 5k grid and vertex-cap tests |
| Non-finite/invalid geometry | Strict projection validation and explicit status/reason | NaN/Inf/invalid bounds tests |
| Prompt injection in DWG text | Raw text omitted by default, bounded untrusted retrieval, no URL/path/command following | Malicious text redaction/log/error tests |
| Arbitrary query/code execution | Closed enums/filters only; no regex/expression/eval/SQL/HTTP/path/plugin | Forbidden-field/value contract tests |
| Raw drawing disclosure in logs/errors | Safe IDs/digests/counts/codes only | Captured log/error tests |
| Inference used as write authority | Feature ID/confidence cannot target commit; exact snapshot/entity revalidation through Phase 6–9 | Program/admission negative tests |
| Inference lowers risk/approval | Existing Phase 7 policy derives risk/assurance; scene is additive evidence only | Existing Phase 7/8 regression plus injection test |
| Workflow arbitrary action | Closed typed scene steps and internal port; no MCP/tool/plugin dispatch | Contract and action-port tests |
| Restart duplicates scene/CAD effect | Deterministic source-bound child key, immutable scene dedup, persisted child refs | Failure-injection/restart tests |
| Silent runtime downgrade | Explicit source capability/missing reason; no LT write | R25/ezdxf/LT matrix |
| Feature/profile bypass | All flags default off; dependency validation; policy epoch/version bind | Config/profile tests |

## Drawing text handling

The following strings are data, never instructions:

- `ignore previous instructions`;
- URLs and UNC/local paths;
- shell, PowerShell, Python, AutoLISP or AutoCAD command-like text;
- block/layer names and attributes.

They must not affect query shape, feature rules, network/file access, workflow
steps, risk, assurance, approval or commit. Summary and audit logs omit them.
Any explicit authorized text page is bounded, escaped and labelled
`untrusted_content`.

## Availability behavior

Scene building fails closed with typed errors. Budget failure does not publish
an apparently complete graph. Unknown/unsupported/truncated source geometry is
reported in capabilities, warnings and completeness. Timing is recorded as
evidence but CI correctness gates use deterministic counters, not flaky
wall-clock thresholds.

## Secrets

Cursor signing material is configuration-only, at least 32 bytes, and never
stored in scene payload, cursor payload, logs or evidence. OAuth and device
secrets remain governed by prior phases. Scene data stores owner identity
internally but public payload omits it.

## Security acceptance

Engineering GO requires no open Critical or High finding, all required tests
above green, live R25 no-write evidence and preserved Phase 6–9 security
regression. Customer Pilot remains separately gated.

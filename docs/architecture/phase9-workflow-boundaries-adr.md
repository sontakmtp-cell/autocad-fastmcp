# ADR: Phase 9 skill and workflow boundaries

## Decision

Phase 9 skills are immutable first-party catalog records, and workflows are
bounded declarative DAGs persisted by the Gateway.  A workflow may select only
allowlisted step kinds, planners, and templates. It never evaluates code,
imports modules, resolves a caller-selected path/URL, invokes an MCP tool, or
talks to AutoCAD directly.

CAD effects remain owned by the existing Phase 6--8 program, preview, trusted
approval, commit, job/recovery, validation, and rollback paths. The Agent and
Managed Host do not receive skill, workflow, guide, or template semantics.

## Consequences

The public surface is workflow-level rather than a tool per skill. Every run
pins exact catalog/workflow/planner/template/policy data; state and actions use
CAS plus deterministic idempotency. Restart or reconnect reconciles a durable
child action. A started or unknown write is never automatically retried.

Cleanup is audit-only in this phase. Delete/topology, LT write, arbitrary
plugins, marketplace skills, and mixed create-equivalent plus exact-transform
checkpoint strategies remain unavailable.

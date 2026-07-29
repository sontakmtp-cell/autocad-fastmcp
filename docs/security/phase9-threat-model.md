# Phase 9 threat model

## Overview

This repository operates two related AutoCAD MCP paths: a legacy local MCP
server and the opt-in FastMCP Gateway with a Desktop Agent, Managed .NET Host,
SQLite durable state, OAuth-protected public interface, and a Portal BFF.  The
Gateway can ultimately cause an AutoCAD drawing to change, so its protected
assets are drawing integrity, device control, owner isolation, durable audit
records, consent/approval authority, signing material, and OAuth/session
credentials.  Availability matters as well: a duplicated or unreconciled CAD
effect may corrupt a user's drawing even if no data is disclosed.

Phase 9 adds first-party skill packages and durable workflow orchestration. It
must remain a bounded layer over the existing Phase 6--8 program, preview,
approval, commit, recovery, validation, and rollback contracts; it is not a
general automation or plugin platform.

## Threat model, trust boundaries, and assumptions

- An authenticated ChatGPT/MCP client and Portal browser are untrusted for
  effect authority. They can request only schemas and controls exposed by the
  Gateway; an OAuth subject is authenticated but is not trusted to name another
  owner, raise capability, lower risk, or grant approval.
- Gateway authentication and owner-scoped repositories are the boundary between
  tenants. A guessed run, device, program, preview, intent, job, receipt, or
  recovery identifier must be indistinguishable from a missing resource.
- The Portal BFF is the browser boundary. Browser input and cookies cannot
  become owner, consent, risk, assurance, or effect-summary authority; recent
  authentication and the Gateway remain the approval authority.
- A first-party release bundle and its catalog import are trusted only after
  strict schema validation and digest verification. Skill guides, manifests,
  template inputs, and workflow inputs are data, not executable instructions.
- The workflow engine is trusted to persist transitions and outbox actions, but
  must tolerate Gateway restart, worker races, Agent reconnect, and an
  inconclusive started write. SQLite state and event logs are the durable truth,
  not process memory.
- Desktop Agent/Managed Host execute the existing signed R25 lab path and are
  isolated from skill semantics. They accept only existing typed program and
  rollback contracts; no raw path, guide, template, or skill content crosses
  their wire boundary.
- AutoCAD and a drawing are an external side-effect boundary. Preview remains
  non-destructive; every commit still requires the existing sealed plan and
  trusted approval path.  `outcome_unknown` is safety-relevant, not a retryable
  failure.

## Attack surface, mitigations, and attacker stories

Public FastMCP tools/resources, Portal routes, OAuth bearer tokens, catalog
assets, SQLite persistence, Gateway-to-Agent WSS, and workflow-control
payloads are the main exposed surfaces.  A malicious client may submit
oversized or malformed inputs, replay a start/control request, guess another
tenant's identifiers, request a withdrawn skill, use a stale state version, or
attempt fields such as a path, URL, module, command, raw handle, approval,
risk, assurance, or capability override.

The required controls are strict `extra=forbid` contracts; allowlisted step,
planner, and template registries; immutable version/digest pins; bounded JSON
and resources; owner/device/document filters; CAS state versions; deterministic
child idempotency keys; append-only events; action leases; and fail-closed
feature flags.  The existing Phase 7 consent and Phase 8 sealed program paths
remain the only sources of approval and CAD execution authority.  Workflow
replay must reconcile existing child records and must never auto-retry a
started or unknown write.

Operator/release misuse is also material: promoting an unreviewed catalog,
leaving a write flag enabled, or resolving a new default version mid-run could
turn a safe workflow into a different operation. Publication and rollback must
therefore be audited operator actions, while security revocation prevents the
next effect-bearing step.  Telemetry and reports must omit raw drawing content,
credentials, and trusted consent material by default.

The legacy local interface includes explicitly opt-in dangerous operations,
but it is not authority for the new Gateway workflow path. Phase 9 must not
reuse legacy in-memory plan stores, raw command paths, AutoLISP execution, or
backend-private calls. Third-party skills, user-authored workflow code,
network/file/plugin loading, delete/topology packs, and LT write are out of
scope and must fail closed.

## Severity calibration

**Critical:** a remotely reachable path that lets an attacker execute arbitrary
code/commands/LISP, bypass OAuth ownership to control another device, or commit
unapproved destructive CAD effects broadly.

**High:** a cross-tenant run/resource disclosure or control; model/browser
approval bypass; catalog/workflow tampering that can introduce a new CAD effect;
or restart/replay behavior that can duplicate an approved write.

**Medium:** a specific workflow can bypass a policy/capability/revocation guard,
leak sensitive drawing metadata, or become stuck in a way that loses the
operator's ability to safely recover an inconclusive write.

**Low:** bounded information disclosure, audit/availability degradation, or a
validation inconsistency that cannot cross ownership or create an effect. Test
fixtures and local lab-only behavior remain lower severity unless reachable
through the production Gateway profile.

Repository: autocad-fastmcp
Version: eb890ec8224ba68310ff7191baa0f9065434a949

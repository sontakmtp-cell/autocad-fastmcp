# Phase 5 LT/File IPC regression matrix

Status: automated compatibility guardrails added; real AutoCAD LT certification is
not claimed by this file.

## Protected boundaries

| Boundary | Automated evidence | Expected result |
|---|---|---|
| Raw/arbitrary AutoLISP | `tests/test_phase5_lt_regression.py` | Disabled before any IPC/LISP artifact is created |
| Safe File IPC allowlist | `tests/test_phase5_lt_regression.py` | Packaged `drawing-info` still routes; an unknown command never reaches File IPC |
| Agent LT adapter | `apps/desktop_agent/tests/test_phase5_legacy_executor_regression.py` | Adapter remains read-only and constructs `SafeFileIPCBackend(allow_execute_lisp=False)` |
| Legacy executor | `apps/desktop_agent/tests/test_phase5_legacy_executor_regression.py` | Existing C1 summary remains usable and ignores additive runtime context |
| Gateway public contract | `services/gateway/tests/test_phase5_public_regression.py` | Three read-only tools and committed resource/prompt snapshots remain unchanged |
| Arbitrary-code public surface | `services/gateway/tests/test_phase5_public_regression.py` | No LISP, assembly, shell, upload, or runtime-selector control is published |

## Local automated baseline

The pre-change baseline was run on Windows with isolated pytest temporary
directories:

- Root File IPC/remote policy/static LISP: 75 passed, 1 skipped.
- Gateway contract and Phase 4 C1: 13 passed.
- Desktop Agent executor and boundaries: 9 passed.

After adding the Phase 5 guardrails, the combined targeted suites passed:

- Root File IPC/remote policy/static LISP plus Phase 5: 78 passed, 1 skipped.
- Gateway public contract/Phase 4 C1 plus Phase 5: 16 passed.
- Desktop Agent executor/boundaries plus Phase 5: 11 passed.

## Certification boundary

These tests prove Python policy, adapter, and public-contract compatibility only.
Phase 5.5 LT certification still requires fresh evidence from a real supported
AutoCAD LT 2024+ installation, including packaged dispatcher loading, read-only
observe, allowlist rejection, busy/modal behavior, fallback selection, and an
installer/upgrade/rollback run. Absence of that evidence is an external lab
blocker, not a reason to label LT certified.

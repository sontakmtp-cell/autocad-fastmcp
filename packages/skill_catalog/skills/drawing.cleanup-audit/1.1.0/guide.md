# Scene-backed cleanup audit

This version builds or reuses one immutable scene, reads its bounded issue page,
and validates the same scene binding. It is read-only: it never calls an MCP
tool, executes drawing text, authorizes a CAD change, or bypasses the normal
prepare, preview, trusted approval, commit, validation, and recovery path.

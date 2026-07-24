# AutoCAD MCP Managed Host — R25 lab

Phase 5.1–5.3 lab plug-in for AutoCAD/AutoCAD Mechanical 2025 on Windows x64.
It targets .NET 8 and exposes a fixed `cad.host/1` registry: health/drawing
summary, bounded entity/events observation, and the create-only
`cad.program/0.1` preview/commit/validate POC.

## Security boundary

- No TCP/public listener, Auth0/browser token, tenant management, reflection
  dispatch, arbitrary assembly/path/script, destructive primitive, or raw
  command/LISP write.
- The pipe is restricted by `PipeOptions.CurrentUserOnly`.
- A fresh 256-bit secret and unpredictable pipe name are published only in the
  current user's local application-data directory. The Agent reads that
  bootstrap descriptor and verifies the HMAC session proof.
- Frames are 32-bit length-prefixed UTF-8 JSON and limited to 64 KiB.
- A disconnected command is never retried by the Host. Successful create-only
  commits carry an atomic bounded receipt in the DWG so an explicit reconcile
  can return `duplicate` without reapplying effects.
- AutoCAD API work is queued from the pipe worker and executed on AutoCAD's
  `Idle` callback.

The lab bundle is intentionally unsigned. The build/install scripts never claim
or manufacture a signature. Do not publish it as a production package.

## Build and local lab install

```powershell
.\scripts\build-phase5-managed-host.ps1
.\scripts\install-phase5-lab-bundle.ps1
```

The install script copies the built bundle to the current user's Autodesk
ApplicationPlugins directory. AutoCAD LT is not supported and the package
metadata only selects AutoCAD R25.0 on Windows x64.

After AutoCAD Mechanical 2025 loads the bundle, run `AUTOCADMCPSTATUS`. The
command prints only non-secret product/package/pipe readiness information.

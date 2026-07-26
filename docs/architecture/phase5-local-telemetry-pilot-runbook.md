# Phase 5 local telemetry pilot runbook

## Purpose and boundary

This pilot runs on the physical Windows host and receives read-only telemetry
from the host Agent and the `Phase4-Win11-Clean` VM Agent. The AutoCAD Managed
Host never connects to this collector or to the Internet.

The collector accepts only:

- dimensions: `runtime_id`, `runtime_role`, `release_family`, `release_year`,
  `operation_id`, `outcome`, `safe_error_code`;
- measures: `count`, `latency_ms`;
- read-only operations: `agent.presence.health`,
  `drawing.observe.summary`.

It rejects owner identity, access/device tokens, pipe secrets, document paths,
drawing content, raw LISP, CAD Programs, stack traces, unknown fields, write
operations, oversized payloads, and public telemetry endpoints.

Every ingest request also needs a random pilot bearer token. The collector
stores only its SHA-256 hash. Each Agent stores the token with Windows DPAPI;
the token is never written into `agent-config.json`, logs, dashboard or
aggregate evidence.

## One-time host setup

Open PowerShell as Administrator on the physical host.

1. Find the Hyper-V adapter address and prefix:

   ```powershell
   Get-NetIPConfiguration |
     Where-Object InterfaceAlias -like "vEthernet (Default Switch)" |
     Select-Object InterfaceAlias,IPv4Address
   ```

   On the current lab host the observed adapter address was `172.20.96.1/20`.
   Recheck it after a reboot because the Default Switch range can change.

2. Create the collector config, a subnet-scoped firewall rule, and start it:

   ```powershell
   Set-Location D:\AI\autocad-mcp
   .\scripts\configure-phase5-telemetry-pilot.ps1 `
     -ListenHost 172.20.96.1 `
     -VmSubnet 172.20.96.0/20
   ```

3. Check health:

   ```powershell
   .\scripts\test-phase5-telemetry-health.ps1 `
     -BaseUrl http://172.20.96.1:4319
   ```

4. Open `http://172.20.96.1:4319/dashboard` on the physical host.

Do not expose port 4319 through Cloudflare, router forwarding, or a public
firewall rule.

### Accepted lab limitation

For this one-host/one-VM pilot, telemetry uses bearer-authenticated HTTP on the
private Hyper-V Default Switch. DPAPI protects the token at rest, but this
channel does not provide TLS against a hostile peer that can observe or
ARP-spoof that private subnet. Therefore:

- connect only the dedicated `Phase4-Win11-Clean` VM during the pilot;
- never bridge or forward port 4319 to another network;
- rotate the generated pilot token whenever the lab membership changes;
- do not reuse this transport in production.

A production rollout requires host-authenticated TLS or per-device signed
requests with replay protection.

## Agent settings

Set these variables before starting each Agent.

Physical host:

```powershell
$env:AUTOCAD_MCP_TELEMETRY_ENABLED = "1"
$env:AUTOCAD_MCP_TELEMETRY_ENDPOINT = "http://172.20.96.1:4319/ingest/autocad-mcp"
```

VM:

```powershell
$env:AUTOCAD_MCP_TELEMETRY_ENABLED = "1"
$env:AUTOCAD_MCP_TELEMETRY_ENDPOINT = "http://172.20.96.1:4319/ingest/autocad-mcp"
```

Both Agents use the actual Hyper-V host address because the collector binds
only that private interface during this two-device pilot. No inbound VM
firewall rule is needed: the VM makes an outbound HTTP connection to the host.
Use `provision-phase5-agent.ps1`; it copies/protects the token for the current
Windows user and `run-phase5-agent.ps1` supplies it only to the Agent process.

Exporter counters are written to:

`%LOCALAPPDATA%\Kythuatvang\AutoCADAgent\diagnostics\telemetry-status.json`

These counters show accepted, dropped, rejected, and export error totals. A
collector outage or a full queue drops telemetry but does not fail a CAD
operation.

## Pilot checks

1. Start the host collector.
2. Start Agent A on the host and Agent B in the VM.
3. Keep all write switches off.
4. Run health and drawing summary observations from both OAuth test users.
5. Confirm the dashboard contains two runtime/outcome series but no identity,
   device identifiers, drawing names, or paths.
6. Stop the collector, run another observation, and confirm CAD still succeeds
   while `export_errors` or `dropped` increases.
7. Restart the collector and confirm new aggregate measurements arrive.
8. Exercise Agent restart, Managed Host unload, AutoCAD busy/modal, device
   revoke, and reconnect. Record only safe error codes.

Run for 3–7 days. This is lab evidence, not production telemetry.

## Stop and rollback

Run as Administrator:

```powershell
Set-Location D:\AI\autocad-mcp
.\scripts\disable-phase5-telemetry-pilot.ps1
```

This stops the collector and removes only the named pilot firewall rule.
Aggregate data remains in `D:\AutoCAD-MCP-Telemetry` for evidence; delete it
manually only after evidence review.

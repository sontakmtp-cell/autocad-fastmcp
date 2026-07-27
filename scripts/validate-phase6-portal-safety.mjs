import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";

const repoRoot = resolve(import.meta.dirname, "..");
const portalRoot = join(repoRoot, "apps", "web_portal");
const phase6Routes = [
  join(portalRoot, "src", "app", "programs"),
  join(portalRoot, "src", "app", "previews"),
  join(portalRoot, "src", "app", "receipts"),
  join(portalRoot, "src", "app", "validations"),
  join(portalRoot, "src", "app", "jobs"),
];
const requiredFiles = [
  "src/components/BindingSummary.tsx",
  "src/components/Phase6Status.tsx",
  "src/components/Phase6Warning.tsx",
  "tests/e2e/phase6-program.spec.ts",
  "evidence/phase6-failure-matrix.md",
];

function filesUnder(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    return entry.isDirectory() ? filesUnder(path) : [path];
  });
}

const failures = [];
for (const file of requiredFiles) {
  try {
    if (!statSync(join(portalRoot, file)).isFile()) failures.push(`not a file: ${file}`);
  } catch {
    failures.push(`missing: ${file}`);
  }
}

const routeFiles = phase6Routes.flatMap(filesUnder).filter((file) => /\.(ts|tsx)$/.test(file));
const forbiddenRoutePatterns = [
  [/<form\b/i, "Phase 6 route contains a mutation form"],
  [/<button\b/i, "Phase 6 route contains an action button"],
  [/confirm\s*=\s*true/i, "confirm=true is forbidden"],
  [/trusted[_ -]?approval/i, "trusted approval is outside Phase 6"],
  [/risk[_ -]?override/i, "risk override is forbidden"],
  [/\/approvals?\b/i, "approval routes are outside Phase 6"],
];
for (const file of routeFiles) {
  const source = readFileSync(file, "utf8");
  for (const [pattern, message] of forbiddenRoutePatterns) {
    if (pattern.test(source)) {
      failures.push(`${relative(repoRoot, file)}: ${message}`);
    }
  }
}

const envSource = readFileSync(join(portalRoot, "src", "lib", "env.ts"), "utf8");
if (!/PORTAL_PHASE6_UI_ENABLED:[\s\S]*default\("false"\)/.test(envSource)) {
  failures.push("Phase 6 UI flag does not default false");
}
if (!/PORTAL_MANAGED_WRITE_UI_ENABLED:[\s\S]*default\("false"\)/.test(envSource)) {
  failures.push("Managed Write UI flag does not default false");
}
if (!/PORTAL_MANAGED_WRITE_KILL_SWITCH:[\s\S]*default\("true"\)/.test(envSource)) {
  failures.push("Managed Write kill switch does not default active");
}

const result = {
  schema: "autocad-mcp.phase6-portal-safety-check/1",
  checked_route_files: routeFiles.length,
  approval_or_mutation_controls: failures.length === 0 ? "absent" : "check_failed",
  feature_flags: failures.length === 0 ? "fail_closed" : "check_failed",
  status: failures.length === 0 ? "passed" : "failed",
  failures,
};
console.log(JSON.stringify(result, null, 2));
if (failures.length) process.exitCode = 1;

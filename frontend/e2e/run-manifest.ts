import { lstatSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createHash, timingSafeEqual } from "node:crypto";

export type MaterialFixture = {
  id: string;
  technical_identity: string;
  material_name: string;
  folder_path: string | null;
  workflow_status: string;
  relativePath: string;
};

export type SeedState = {
  companyId: string;
  brandId: string;
  projectId: string;
  valid: MaterialFixture;
  missing: MaterialFixture;
  mismatch: MaterialFixture;
};

type RunManifest = {
  schemaVersion: number;
  runGuid: string;
  runnerTokenSha256: string;
  frontendUrl: string;
  backendUrl: string;
  frontendPort: number;
  backendPort: number;
  materialsRoot: string;
  state: SeedState;
};

const repositoryRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const runsRoot = path.join(repositoryRoot, ".e2e-data", "runs");
const artifactsRoot = path.join(repositoryRoot, ".e2e-artifacts");
const guidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function fail(message: string): never {
  throw new Error(`Unsafe direct Playwright invocation refused before requests or fixture writes: ${message}`);
}

function assertNoSymlinkComponents(root: string, target: string): void {
  if (lstatSync(root).isSymbolicLink()) fail(`trusted root is a symlink or junction: ${root}`);
  const relative = path.relative(root, target);
  if (relative === "" || relative.startsWith(`..${path.sep}`) || relative === ".." || path.isAbsolute(relative)) {
    fail(`path is outside the trusted repository: ${target}`);
  }
  let current = root;
  for (const component of relative.split(path.sep)) {
    current = path.join(current, component);
    const item = lstatSync(current);
    if (item.isSymbolicLink()) fail(`symlink or junction is forbidden: ${current}`);
  }
}

function requireString(value: unknown, name: string): string {
  if (typeof value !== "string" || value.length === 0) fail(`${name} is missing`);
  return value;
}

function requirePort(value: unknown, name: string): number {
  if (!Number.isInteger(value) || Number(value) < 1 || Number(value) > 65_535) fail(`${name} is invalid`);
  return Number(value);
}

function assertLoopbackUrl(value: unknown, expectedPort: number, name: string): string {
  const raw = requireString(value, name);
  let url: URL;
  try { url = new URL(raw); } catch { fail(`${name} is not a URL`); }
  if (
    url.protocol !== "http:" || url.hostname !== "127.0.0.1" ||
    url.port !== String(expectedPort) || url.username !== "" || url.password !== "" ||
    url.search !== "" || url.hash !== "" || url.pathname !== "/" || url.origin !== raw
  ) {
    fail(`${name} must be exactly http://127.0.0.1:<runner-port> without credentials, path, query, or fragment`);
  }
  return raw;
}

function validateFixture(value: unknown, name: string): MaterialFixture {
  if (!value || typeof value !== "object") fail(`${name} is invalid`);
  const item = value as Record<string, unknown>;
  const relativePath = requireString(item.relativePath, `${name}.relativePath`);
  if (path.isAbsolute(relativePath) || relativePath.includes("\\") || relativePath.split("/").includes("..")) {
    fail(`${name}.relativePath must be a safe forward-slash relative path`);
  }
  return {
    id: requireString(item.id, `${name}.id`),
    technical_identity: requireString(item.technical_identity, `${name}.technical_identity`),
    material_name: requireString(item.material_name, `${name}.material_name`),
    folder_path: item.folder_path === null ? null : requireString(item.folder_path, `${name}.folder_path`),
    workflow_status: requireString(item.workflow_status, `${name}.workflow_status`),
    relativePath,
  };
}

function loadManifest(): RunManifest {
  const manifestPathValue = process.env.E2E_RUN_MANIFEST;
  const tokenValue = process.env.E2E_RUN_TOKEN;
  if (!manifestPathValue || !tokenValue) fail("a runner manifest and capability token are required");
  const manifestPath = path.resolve(manifestPathValue);
  const runRoot = path.dirname(manifestPath);
  const runGuid = path.basename(runRoot);
  if (!guidPattern.test(runGuid) || path.dirname(runRoot) !== runsRoot || path.basename(manifestPath) !== "run-manifest.json") {
    fail("manifest must be the exact .e2e-data/runs/<GUID>/run-manifest.json path");
  }
  assertNoSymlinkComponents(repositoryRoot, manifestPath);
  const marker = readFileSync(path.join(runRoot, ".reawote-e2e-run"), "utf8");
  if (marker !== `reawote-e2e-owned-v2:${runGuid}`) fail("run ownership marker is invalid");
  const parsed = JSON.parse(readFileSync(manifestPath, "utf8")) as Record<string, unknown>;
  if (parsed.schemaVersion !== 1 || parsed.runGuid !== runGuid) fail("manifest schema or run GUID is invalid");
  const runnerTokenSha256 = requireString(parsed.runnerTokenSha256, "runnerTokenSha256");
  if (!/^[0-9a-f]{64}$/.test(runnerTokenSha256)) fail("runnerTokenSha256 is invalid");
  const expected = Buffer.from(runnerTokenSha256, "hex");
  const supplied = createHash("sha256").update(tokenValue, "utf8").digest();
  if (expected.length !== supplied.length || !timingSafeEqual(expected, supplied)) fail("runner capability token is invalid");
  const frontendPort = requirePort(parsed.frontendPort, "frontendPort");
  const backendPort = requirePort(parsed.backendPort, "backendPort");
  const materialsRoot = path.resolve(requireString(parsed.materialsRoot, "materialsRoot"));
  if (materialsRoot !== path.join(runRoot, "materials")) fail("materialsRoot is outside the exact runner directory");
  assertNoSymlinkComponents(repositoryRoot, materialsRoot);
  if (!parsed.state || typeof parsed.state !== "object") fail("seed state is invalid");
  const state = parsed.state as Record<string, unknown>;
  return {
    schemaVersion: 1,
    runGuid,
    runnerTokenSha256,
    frontendUrl: assertLoopbackUrl(parsed.frontendUrl, frontendPort, "frontendUrl"),
    backendUrl: assertLoopbackUrl(parsed.backendUrl, backendPort, "backendUrl"),
    frontendPort,
    backendPort,
    materialsRoot,
    state: {
      companyId: requireString(state.companyId, "state.companyId"),
      brandId: requireString(state.brandId, "state.brandId"),
      projectId: requireString(state.projectId, "state.projectId"),
      valid: validateFixture(state.valid, "state.valid"),
      missing: validateFixture(state.missing, "state.missing"),
      mismatch: validateFixture(state.mismatch, "state.mismatch"),
    },
  };
}

export const runManifest = loadManifest();
export const e2eRepositoryRoot = repositoryRoot;
const artifactRunRoot = path.join(artifactsRoot, runManifest.runGuid);
assertNoSymlinkComponents(repositoryRoot, artifactRunRoot);
if (readFileSync(path.join(artifactRunRoot, ".reawote-e2e-run"), "utf8") !== `reawote-e2e-owned-v2:${runManifest.runGuid}`) {
  fail("artifact ownership marker is invalid");
}
export const e2eOutputDirectory = path.join(artifactRunRoot, "playwright-results");

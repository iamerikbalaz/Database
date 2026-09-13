import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { createServer } from "node:http";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";

const targetRoot = path.join(os.tmpdir(), `reawote-e2e-direct-guard-${randomUUID()}`);
let postRequests = 0;
const server = createServer((request, response) => {
  if (request.method === "POST") postRequests += 1;
  response.statusCode = 500;
  response.end("guard probe");
});

await new Promise((resolve, reject) => {
  server.once("error", reject);
  server.listen(0, "127.0.0.1", resolve);
});

try {
  const address = server.address();
  assert(address && typeof address === "object");
  const probeUrl = `http://127.0.0.1:${address.port}`;
  const environment = {
    ...process.env,
    E2E_FRONTEND_URL: probeUrl,
    E2E_BACKEND_URL: probeUrl,
    E2E_MATERIALS_ROOT: targetRoot,
  };
  delete environment.E2E_RUN_MANIFEST;
  delete environment.E2E_RUN_TOKEN;
  const executable = process.platform === "win32" ? process.env.ComSpec ?? "cmd.exe" : "npx";
  const arguments_ = process.platform === "win32"
    ? ["/d", "/s", "/c", "npx.cmd playwright test --list"]
    : ["playwright", "test", "--list"];
  const child = spawn(executable, arguments_, {
    cwd: path.resolve(import.meta.dirname, ".."),
    env: environment,
    stdio: ["ignore", "pipe", "pipe"],
  });
  let output = "";
  child.stdout.on("data", (chunk) => { output += chunk; });
  child.stderr.on("data", (chunk) => { output += chunk; });
  const exitCode = await new Promise((resolve, reject) => {
    child.once("error", reject);
    child.once("exit", resolve);
  });
  assert.notEqual(exitCode, 0, "direct Playwright invocation unexpectedly succeeded");
  assert.match(output, /Unsafe direct Playwright invocation refused before requests or fixture writes/);
  assert.equal(postRequests, 0, "direct Playwright invocation emitted a POST request");
  assert.equal(existsSync(targetRoot), false, "direct Playwright invocation wrote a fixture directory");
  process.stdout.write("Direct Playwright invocation failed closed with zero POSTs and zero fixture writes.\n");
} finally {
  await new Promise((resolve) => server.close(resolve));
}

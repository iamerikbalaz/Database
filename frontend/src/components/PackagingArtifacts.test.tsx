import { createHash, webcrypto } from "node:crypto";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { packagingClient } from "../api/packagingClient";
import { PackagingArtifacts } from "./PackagingArtifacts";

const materialId = "10000000-0000-4000-8000-000000000001";
const job = { id: "10000000-0000-4000-8000-000000000002", proofSha256: "a".repeat(64) };
const artifact = (path = "synthetic_1K.zip") => ({ id: createHash("sha256").update(path).digest("hex"), path, size: 1234, sha256: "b".repeat(64) });
const page = (items = [artifact()], next_cursor: number | null = null) => ({ execution_id: job.id, proof_sha256: job.proofSha256, items, next_cursor });
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
beforeEach(() => { vi.stubGlobal("crypto", webcrypto); });
afterEach(() => { vi.unstubAllGlobals(); });

it("loads on demand and uses browser download links bound to the exact accepted proof", async () => {
  const fetch = vi.fn(async () => json(page())); vi.stubGlobal("fetch", fetch);
  render(<PackagingArtifacts materialId={materialId} job={job} />);
  expect(fetch).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Load packaged files" }));
  const link = await screen.findByRole("link", { name: "Download synthetic_1K.zip" });
  expect(link).toHaveAttribute("href", `/api/materials/${materialId}/packaging-executions/${job.id}/artifacts/${artifact().id}?proof_sha256=${job.proofSha256}`);
  expect(link).toHaveAttribute("download"); expect(link).toHaveAttribute("rel", "noopener noreferrer");
  expect(fetch).toHaveBeenCalledTimes(1);
});

it("appends a validated next page and rejects duplicate files", async () => {
  const next = artifact("PREVIEW/český náhled.png");
  const fetch = vi.fn(async (url: string) => json(url.includes("after=1") ? page([next], 2) : url.includes("after=2") ? page([next]) : page([artifact()], 1)));
  vi.stubGlobal("fetch", fetch); render(<PackagingArtifacts materialId={materialId} job={job} />);
  fireEvent.click(screen.getByRole("button", { name: "Load packaged files" }));
  fireEvent.click(await screen.findByRole("button", { name: "More packaged files" }));
  await screen.findByRole("link", { name: "Download PREVIEW/český náhled.png" });
  expect(screen.getAllByRole("link")).toHaveLength(2);
  fireEvent.click(screen.getByRole("button", { name: "More packaged files" }));
  await screen.findByRole("alert"); expect(screen.getAllByRole("link")).toHaveLength(2);
});

it("does not reflect failed response diagnostics", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => json({ detail: "PRIVATE" }, 503)));
  render(<PackagingArtifacts materialId={materialId} job={job} />);
  fireEvent.click(screen.getByRole("button", { name: "Load packaged files" }));
  expect(await screen.findByRole("alert")).not.toHaveTextContent("PRIVATE");
  expect(screen.queryByRole("link")).not.toBeInTheDocument();
});

it.each(["execution", "proof", "identity", "traversal", "size", "duplicate", "cursor"])("rejects substituted or malformed %s", async (change) => {
  const value = page();
  if (change === "execution") value.execution_id = materialId;
  else if (change === "proof") value.proof_sha256 = "c".repeat(64);
  else if (change === "identity") value.items[0].id = "c".repeat(64);
  else if (change === "traversal") value.items[0] = artifact("PREVIEW/../PRIVATE");
  else if (change === "size") value.items[0].size = 16 * 1024 ** 3 + 1;
  else if (change === "duplicate") value.items.push(artifact());
  else value.next_cursor = 2;
  vi.stubGlobal("fetch", vi.fn(async () => json(value)));
  await expect(packagingClient.files(materialId, job)).rejects.toThrow();
});

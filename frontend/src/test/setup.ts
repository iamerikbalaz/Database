import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";
import { webcrypto } from "node:crypto";

// jsdom supplies random UUIDs but not the browser's secure-context digest API.
Object.defineProperty(globalThis.crypto, "subtle", { configurable: true, value: webcrypto.subtle });

afterEach(cleanup);

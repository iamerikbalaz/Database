// @vitest-environment node
import { createServer as createHttpServer } from "node:http";
import { once } from "node:events";
import { createServer as createViteServer, mergeConfig } from "vite";
import { expect, it } from "vitest";
import config from "../vite.config";

it("forwards /api/companies unchanged through the actual Vite proxy", async () => {
  const paths: (string | undefined)[] = [];
  const backend = createHttpServer((request, response) => {
    paths.push(request.url);
    response.setHeader("Content-Type", "application/json");
    response.statusCode = request.url === "/api/companies" ? 200 : 404;
    response.end(
      JSON.stringify(
        request.url === "/api/companies" ? [] : { detail: "Not found" },
      ),
    );
  });
  backend.listen(0, "127.0.0.1");
  await once(backend, "listening");
  const address = backend.address();
  if (!address || typeof address === "string")
    throw new Error("Missing backend test port");
  const target = "http://127.0.0.1:" + address.port;
  const vite = await createViteServer(
    mergeConfig(config, {
      configFile: false,
      logLevel: "silent",
      server: {
        host: "127.0.0.1",
        port: 0,
        hmr: false,
        watch: null,
        proxy: { "/api": { target } },
      },
    }),
  );
  try {
    await vite.listen();
    const port = vite.httpServer?.address();
    if (!port || typeof port === "string")
      throw new Error("Missing frontend test port");
    const direct = await fetch(target + "/api/companies");
    const proxied = await fetch(
      "http://127.0.0.1:" + port.port + "/api/companies",
    );
    expect(direct.status).toBe(200);
    expect(proxied.status).toBe(200);
    expect(await proxied.json()).toEqual(await direct.json());
    expect(paths).toEqual(["/api/companies", "/api/companies"]);
  } finally {
    await vite.close();
    backend.closeAllConnections();
    await new Promise<void>((resolve, reject) =>
      backend.close((error) => (error ? reject(error) : resolve())),
    );
  }
}, 15000);

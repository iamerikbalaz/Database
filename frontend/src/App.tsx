import { useCallback, useEffect, useState } from "react";

type HealthStatus = "loading" | "connected" | "unavailable";

interface HealthResponse {
  status: "ok" | "degraded";
  database: "connected" | "unavailable";
  service: string;
  version: string;
}

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL ?? "/api").replace(/\/$/, "");

async function fetchBackendHealth(): Promise<HealthResponse> {
  const response = await fetch(`${apiBaseUrl}/health`);
  if (!response.ok) {
    throw new Error(`Backend returned ${response.status}`);
  }

  return (await response.json()) as HealthResponse;
}

function App() {
  const [health, setHealth] = useState<HealthStatus>("loading");
  const [details, setDetails] = useState<HealthResponse | null>(null);

  const checkBackend = useCallback(async () => {
    try {
      const payload = await fetchBackendHealth();
      setDetails(payload);
      setHealth(payload.status === "ok" ? "connected" : "unavailable");
    } catch {
      setDetails(null);
      setHealth("unavailable");
    }
  }, []);

  useEffect(() => {
    let isActive = true;

    void fetchBackendHealth()
      .then((payload) => {
        if (isActive) {
          setDetails(payload);
          setHealth(payload.status === "ok" ? "connected" : "unavailable");
        }
      })
      .catch(() => {
        if (isActive) {
          setDetails(null);
          setHealth("unavailable");
        }
      });

    return () => {
      isActive = false;
    };
  }, []);

  const retryBackend = () => {
    setHealth("loading");
    void checkBackend();
  };

  return (
    <main className="page-shell">
      <section className="status-card" aria-labelledby="app-title">
        <p className="eyebrow">Interní systém</p>
        <h1 id="app-title">REAWOTE</h1>
        <p className="intro">Základ správy PBR materiálů a publikačního procesu.</p>

        <div className={`status status--${health}`} role="status" aria-live="polite">
          <span className="status__dot" aria-hidden="true" />
          <div>
            <span className="status__label">Backend</span>
            <strong>
              {health === "loading" && "Ověřuji spojení…"}
              {health === "connected" && "Připojeno"}
              {health === "unavailable" && "Nedostupné"}
            </strong>
          </div>
        </div>

        {details && (
          <p className="details">
            Databáze: {details.database === "connected" ? "připojena" : "nedostupná"} · API v
            {details.version}
          </p>
        )}

        {health === "unavailable" && (
          <button type="button" onClick={retryBackend}>
            Zkusit znovu
          </button>
        )}
      </section>
    </main>
  );
}

export default App;

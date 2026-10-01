import { useState } from "react";
import { directoryClient, type Customer } from "../api/directoryClient";
import { sessionGeneration } from "../auth/sessionTransport";

export function CustomerCsvExport({ rows, disabled }: { rows: Customer[]; disabled: boolean }) {
  const [busy, setBusy] = useState(false), [message, setMessage] = useState("");
  return <><button className="button" disabled={disabled || busy || !rows.length} onClick={async () => {
    const generation = sessionGeneration();
    setBusy(true); setMessage("");
    try {
      const result = await directoryClient.exportCustomers(rows.map(row => row.id));
      if (generation !== sessionGeneration()) return;
      const url = URL.createObjectURL(new Blob([result.csv], { type: "text/csv;charset=utf-8" }));
      const link = document.createElement("a"); link.href = url; link.download = result.filename; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setMessage(result.warnings.length ? `CSV downloaded. ${result.warnings.join(" ")}` : "CSV downloaded. Published remains unchanged.");
    } catch (error) { if (generation === sessionGeneration()) setMessage(error instanceof Error ? error.message : "Brand CSV export failed."); }
    finally { setBusy(false); }
  }}>{busy ? "Exporting…" : `Export selected brands CSV (${rows.length})`}</button>{message && <p role="status" className="form-hint">{message}</p>}</>;
}

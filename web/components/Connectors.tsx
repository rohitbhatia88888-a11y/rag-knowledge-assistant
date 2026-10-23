"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type Connector } from "@/lib/api";

/** Admin panel: Google Drive connectors, their sync status, and a manual sync button. */
export function Connectors({ token }: { token: string }) {
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [name, setName] = useState("Company Drive");
  const [folders, setFolders] = useState("");
  const [error, setError] = useState("");

  const refresh = useCallback(() => {
    api<Connector[]>(token, "/connectors").then(setConnectors).catch((e) => setError(e.message));
  }, [token]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    if (!connectors.some((c) => c.status === "syncing" || c.status === "new")) return;
    const t = setInterval(refresh, 3000);
    return () => clearInterval(t);
  }, [connectors, refresh]);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    const folder_ids = folders.split(",").map((s) => s.trim()).filter(Boolean);
    try {
      await api(token, "/connectors", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind: "gdrive", name, config: { folder_ids } }),
      });
      setFolders("");
      refresh();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function sync(id: string, full: boolean) {
    setError("");
    await api(token, `/connectors/${id}/sync?full=${full}`, { method: "POST" }).catch((e) => setError(e.message));
    refresh();
  }

  return (
    <section className="docs">
      <h2>Connectors</h2>
      <ul>
        {connectors.map((c) => (
          <li key={c.id} title={c.last_error ?? ""}>
            <div className="doc-title">{c.name}</div>
            <div className="muted small">
              Google Drive · <span className={`status ${c.status === "ok" ? "ready" : c.status}`}>{c.status}</span> ·{" "}
              {c.document_count} docs
              {c.last_sync_at && ` · synced ${new Date(c.last_sync_at).toLocaleTimeString()}`}
            </div>
            {c.last_error && <div className="error">{c.last_error}</div>}
            <button className="link small" onClick={() => sync(c.id, false)}>
              Sync now
            </button>{" "}
            <button className="link small" onClick={() => sync(c.id, true)}>
              Full resync
            </button>
          </li>
        ))}
      </ul>
      <form className="connector-form" onSubmit={add}>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name" required />
        <input value={folders} onChange={(e) => setFolders(e.target.value)} placeholder="Drive folder IDs, comma-separated" required />
        <button type="submit">Add Drive folder</button>
      </form>
      {error && <p className="error">{error}</p>}
    </section>
  );
}

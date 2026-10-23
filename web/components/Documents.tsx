"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type Doc } from "@/lib/api";

export function Documents({ token, userId, readOnly = false }: { token: string; userId: string; readOnly?: boolean }) {
  const [docs, setDocs] = useState<Doc[]>([]);
  const [acl, setAcl] = useState("group:everyone");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(() => {
    api<Doc[]>(token, "/documents").then(setDocs).catch((e) => setError(e.message));
  }, [token]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Poll while anything is still being ingested.
  useEffect(() => {
    if (!docs.some((d) => d.status === "queued" || d.status === "processing")) return;
    const t = setInterval(refresh, 2000);
    return () => clearInterval(t);
  }, [docs, refresh]);

  async function upload(files: FileList | null) {
    if (!files?.length) return;
    setBusy(true);
    setError("");
    try {
      for (const file of Array.from(files)) {
        const form = new FormData();
        form.append("file", file);
        form.append("acl", acl);
        await api(token, "/documents", { method: "POST", body: form });
      }
      refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: string) {
    await api(token, `/documents/${id}`, { method: "DELETE" }).catch((e) => setError(e.message));
    refresh();
  }

  return (
    <section className="docs">
      <h2>{readOnly ? "Documents you can see" : "Documents"}</h2>
      {!readOnly && (
        <>
          <label className="small">
            Visible to
            <input value={acl} onChange={(e) => setAcl(e.target.value)} placeholder="group:hr,user:bob" />
          </label>
          <label className={`upload ${busy ? "disabled" : ""}`}>
            {busy ? "Uploading…" : "Upload PDF / Markdown"}
            <input
              type="file"
              multiple
              accept=".pdf,.md,.markdown,.txt"
              disabled={busy}
              onChange={(e) => {
                upload(e.target.files);
                e.target.value = "";
              }}
            />
          </label>
        </>
      )}
      {error && <p className="error">{error}</p>}
      <ul>
        {docs.map((d) => (
          <li key={d.id} title={d.error ?? d.acl.join(", ")}>
            <div className="doc-title">{d.title}</div>
            <div className="muted small">
              <span className={`status ${d.status}`}>{d.status}</span>
              {d.chunk_count != null && ` · ${d.chunk_count} chunks`} · {d.acl.join(", ")}
            </div>
            {d.created_by === userId && (
              <button className="link small" onClick={() => remove(d.id)}>
                Delete
              </button>
            )}
          </li>
        ))}
        {!docs.length && <li className="muted small">No documents you can see yet.</li>}
      </ul>
    </section>
  );
}

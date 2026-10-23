export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Source = {
  n: number;
  chunk_id: string;
  document_id: string;
  document_title: string;
  headings: string[];
  page_start: number;
  page_end: number;
  text: string;
  score: number;
};

export type Doc = {
  id: string;
  title: string;
  acl: string[];
  status: "queued" | "processing" | "ready" | "failed";
  error: string | null;
  page_count: number | null;
  chunk_count: number | null;
  created_by: string;
  mime_type: string;
};

export type Connector = {
  id: string;
  kind: string;
  name: string;
  config: { folder_ids: string[]; subject?: string };
  enabled: boolean;
  status: "new" | "syncing" | "ok" | "failed";
  last_sync_at: string | null;
  last_error: string | null;
  stats: Record<string, number | boolean> | null;
  document_count: number;
};

export type StreamHandlers = {
  onSession: (sessionId: string) => void;
  onSources: (sources: Source[], searchQuery: string) => void;
  onToken: (text: string) => void;
  onDone: (d: { message_id: string; cited: number[]; latency_ms: number }) => void;
  onError: (message: string) => void;
};

export async function api<T>(token: string, path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { ...(init.headers ?? {}), Authorization: `Bearer ${token}` },
  });
  if (res.status === 401) {
    // Session expired or revoked: back to sign-in.
    sessionStorage.removeItem("token");
    window.location.reload();
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(typeof body.detail === "string" ? body.detail : `${res.status} ${res.statusText}`);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

export async function devToken(userId: string, tenantId: string, groups: string[]): Promise<string> {
  const res = await fetch(`${API_URL}/auth/dev-token`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_id: userId, tenant_id: tenantId, groups }),
  });
  if (!res.ok) throw new Error("Could not get a dev token");
  return (await res.json()).token;
}

/** POST /chat and parse its Server-Sent Events stream. */
export async function streamChat(token: string, question: string, sessionId: string | null, h: StreamHandlers) {
  const res = await fetch(`${API_URL}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({ question, session_id: sessionId }),
  });
  if (!res.ok || !res.body) {
    const body = await res.json().catch(() => ({}));
    h.onError(body.detail ?? `Request failed (${res.status})`);
    return;
  }
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    let sep;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const raw = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      const event = raw.match(/^event: (.*)$/m)?.[1];
      const data = JSON.parse(raw.match(/^data: (.*)$/m)?.[1] ?? "null");
      if (event === "session") h.onSession(data.session_id);
      else if (event === "sources") h.onSources(data.sources, data.search_query);
      else if (event === "token") h.onToken(data.text);
      else if (event === "done") h.onDone(data);
      else if (event === "error") h.onError(data.message);
    }
  }
}

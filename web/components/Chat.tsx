"use client";

import { useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { api, API_URL, streamChat, type Source } from "@/lib/api";

type Turn = {
  question: string;
  answer: string;
  sources: Source[];
  cited: number[];
  messageId?: string;
  error?: string;
  rating?: 1 | -1;
  pending: boolean;
};

export function Chat({ token }: { token: string }) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [active, setActive] = useState<Source | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  const update = (fn: (t: Turn) => Turn) =>
    setTurns((ts) => [...ts.slice(0, -1), fn(ts[ts.length - 1])]);

  async function ask(question: string) {
    setTurns((ts) => [...ts, { question, answer: "", sources: [], cited: [], pending: true }]);
    setInput("");
    await streamChat(token, question, sessionId, {
      onSession: setSessionId,
      onSources: (sources) => update((t) => ({ ...t, sources })),
      onToken: (text) => {
        update((t) => ({ ...t, answer: t.answer + text }));
        bottom.current?.scrollIntoView({ block: "end" });
      },
      onDone: (d) => update((t) => ({ ...t, cited: d.cited, messageId: d.message_id, pending: false })),
      onError: (error) => update((t) => ({ ...t, error, pending: false })),
    });
    update((t) => ({ ...t, pending: false }));
  }

  async function rate(index: number, rating: 1 | -1) {
    const turn = turns[index];
    if (!turn.messageId) return;
    await api(token, "/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message_id: turn.messageId, rating }),
    });
    setTurns((ts) => ts.map((t, i) => (i === index ? { ...t, rating } : t)));
  }

  async function openSource(s: Source) {
    // The file endpoint needs the bearer token, so fetch it and open a blob URL at the cited page.
    const res = await fetch(`${API_URL}/documents/${s.document_id}/file`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) return;
    const url = URL.createObjectURL(await res.blob());
    window.open(`${url}#page=${s.page_start}`, "_blank", "noopener");
  }

  const pending = turns.some((t) => t.pending);

  return (
    <div className="chat">
      <div className="thread">
        {!turns.length && (
          <div className="empty">
            <h1>Ask about your company's documents</h1>
            <p className="muted">Answers cite the exact page and passage, and only use documents you can access.</p>
          </div>
        )}
        {turns.map((t, i) => (
          <div key={i} className="turn">
            <div className="question">{t.question}</div>
            <div className="answer">
              {t.answer ? (
                <ReactMarkdown
                  components={{
                    a: ({ href, children }) => {
                      const n = Number(href?.replace("#cite-", ""));
                      const src = t.sources.find((s) => s.n === n);
                      return src ? (
                        <button className="cite" onClick={() => setActive(src)} title={src.document_title}>
                          {n}
                        </button>
                      ) : (
                        <a href={href}>{children}</a>
                      );
                    },
                  }}
                >
                  {t.answer.replace(/\[(\d+)\]/g, "[$1](#cite-$1)")}
                </ReactMarkdown>
              ) : (
                t.pending && <span className="muted">{t.sources.length ? "Writing…" : "Searching…"}</span>
              )}
              {t.error && <p className="error">{t.error}</p>}
            </div>
            {t.sources.length > 0 && (
              <div className="sources">
                {t.sources.map((s) => (
                  <button
                    key={s.chunk_id}
                    className={`source ${t.cited.includes(s.n) ? "cited" : ""}`}
                    onClick={() => setActive(s)}
                  >
                    <span className="n">{s.n}</span> {s.document_title} · p.{s.page_start}
                  </button>
                ))}
              </div>
            )}
            {t.messageId && (
              <div className="rating">
                <button className={t.rating === 1 ? "on" : ""} onClick={() => rate(i, 1)} aria-label="Helpful">
                  👍
                </button>
                <button className={t.rating === -1 ? "on" : ""} onClick={() => rate(i, -1)} aria-label="Not helpful">
                  👎
                </button>
              </div>
            )}
          </div>
        ))}
        <div ref={bottom} />
      </div>

      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          if (input.trim() && !pending) ask(input.trim());
        }}
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask a question, e.g. How many days of annual leave do I get?"
          autoFocus
        />
        <button type="submit" disabled={pending || !input.trim()}>
          Ask
        </button>
      </form>

      {active && (
        <div className="passage" role="dialog">
          <div className="passage-head">
            <div>
              <strong>{active.document_title}</strong>
              <div className="muted small">
                {active.page_start === active.page_end
                  ? `Page ${active.page_start}`
                  : `Pages ${active.page_start}–${active.page_end}`}
                {active.headings.length > 0 && ` · ${active.headings.join(" › ")}`}
              </div>
            </div>
            <button className="link" onClick={() => setActive(null)}>
              Close
            </button>
          </div>
          <pre>{active.text}</pre>
          <button onClick={() => openSource(active)}>Open document at this page</button>
        </div>
      )}
    </div>
  );
}

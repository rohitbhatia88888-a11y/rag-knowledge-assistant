"use client";

import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { ArrowRight, Close, ExternalLink, Send, ThumbsDown, ThumbsUp } from "@/components/Icons";
import { api, API_URL, streamChat, type Source } from "@/lib/api";
import { documentName } from "@/lib/format";

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

function pages(s: Source): string {
  return s.page_start === s.page_end ? `p. ${s.page_start}` : `pp. ${s.page_start}–${s.page_end}`;
}

export function Chat({ token, suggestions = [] }: { token: string; suggestions?: string[] }) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [active, setActive] = useState<Source | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const opener = useRef<string | null>(null); // data-focus-id of the control that opened the passage

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
    }).catch(() => undefined);
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

  function showPassage(s: Source, trigger: HTMLElement) {
    opener.current = trigger.dataset.focusId ?? null;
    setActive(s);
  }

  function closePassage() {
    setActive(null);
    // Return focus to the citation that opened it. Look it up again: markdown re-renders replace the element.
    const id = opener.current;
    if (id) setTimeout(() => document.querySelector<HTMLElement>(`[data-focus-id="${id}"]`)?.focus(), 0);
  }

  // Passage panel: focus its close button when it opens, close on Escape.
  useEffect(() => {
    if (!active) return;
    closeButton.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && closePassage();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active]);

  const pending = turns.some((t) => t.pending);

  return (
    <div className="chat">
      <div className="thread">
        {!turns.length && (
          <div className="empty">
            <p className="eyebrow">Ask anything</p>
            <h1>What would you like to know?</h1>
            <p className="muted">Every answer cites its page and passage, and only uses documents you can access.</p>
            {suggestions.length > 0 && (
              <div className="suggestions" role="list" aria-label="Suggested questions">
                {suggestions.map((q) => (
                  <button key={q} role="listitem" className="suggestion" onClick={() => ask(q)}>
                    <span>{q}</span>
                    <ArrowRight size={16} />
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
        {turns.map((t, i) => {
          // Once answered, show sources only if the answer cited some: after "I don't know" they would mislead.
          const showSources = t.sources.length > 0 && (t.pending || t.cited.length > 0 || !!t.error);
          return (
            <article key={i} className="turn" aria-label={`Question ${i + 1}`}>
              <div className="question">{t.question}</div>
              <div className="answer-card">
                <p className="label">Answer</p>
                <div className="answer" aria-live="polite" aria-busy={t.pending}>
                  {t.answer ? (
                    <ReactMarkdown
                      components={{
                        a: ({ href, children }) => {
                          const n = Number(href?.replace("#cite-", ""));
                          const src = t.sources.find((s) => s.n === n);
                          return src ? (
                            <button
                              className="cite"
                              data-focus-id={`cite-${i}-${n}`}
                              onClick={(e) => showPassage(src, e.currentTarget)}
                              aria-label={`Source ${n}: ${documentName(src.document_title)}, ${pages(src)}`}
                            >
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
                    t.pending && (
                      <span className="status-line">
                        <span className="dots" aria-hidden="true" />
                        {t.sources.length ? "Writing the answer…" : "Searching documents you can access…"}
                      </span>
                    )
                  )}
                  {t.error && (
                    <p className="error" role="alert">
                      {t.error}
                    </p>
                  )}
                </div>
                {showSources && (
                  <ol className="footnotes" aria-label="Sources">
                    {t.sources.map((s) => {
                      const cited = t.cited.includes(s.n);
                      return (
                        <li key={s.chunk_id} className={cited ? "cited" : ""}>
                          <button
                            className="footnote"
                            data-focus-id={`source-${i}-${s.n}`}
                            onClick={(e) => showPassage(s, e.currentTarget)}
                            aria-label={`Source ${s.n}${cited ? ", cited" : ""}: ${documentName(s.document_title)}, ${pages(s)}`}
                          >
                            <span className="fn-n">{s.n}</span>
                            <span className="fn-doc">{documentName(s.document_title)}</span>
                            <span className="fn-meta">
                              {pages(s)}
                              {s.headings.length > 0 && ` · ${s.headings[s.headings.length - 1]}`}
                            </span>
                          </button>
                        </li>
                      );
                    })}
                  </ol>
                )}
                {t.messageId && (
                  <div className="rating" role="group" aria-label="Was this answer helpful?">
                    <span className="muted small">Helpful?</span>
                    <button
                      className={`icon-button ${t.rating === 1 ? "on" : ""}`}
                      onClick={() => rate(i, 1)}
                      aria-label="Helpful"
                      aria-pressed={t.rating === 1}
                    >
                      <ThumbsUp />
                    </button>
                    <button
                      className={`icon-button ${t.rating === -1 ? "on" : ""}`}
                      onClick={() => rate(i, -1)}
                      aria-label="Not helpful"
                      aria-pressed={t.rating === -1}
                    >
                      <ThumbsDown />
                    </button>
                  </div>
                )}
              </div>
            </article>
          );
        })}
        <div ref={bottom} />
      </div>

      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          if (input.trim() && !pending) ask(input.trim());
        }}
      >
        <div className="composer-field">
          <label htmlFor="question" className="visually-hidden">
            Your question
          </label>
          <input
            id="question"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ask about policies, runbooks, benefits…"
            autoComplete="off"
            autoFocus
          />
          <button type="submit" disabled={pending || !input.trim()} className="send" aria-label="Ask">
            <Send />
          </button>
        </div>
        <p className="composer-hint">Answers cite their sources · press Enter to ask</p>
      </form>

      {active && (
        <div className="passage" role="dialog" aria-modal="false" aria-labelledby="passage-title">
          <div className="passage-head">
            <div>
              <p className="label">Source</p>
              <h2 id="passage-title">{documentName(active.document_title)}</h2>
              <p className="passage-meta">
                {pages(active)}
                {active.headings.length > 0 && ` · ${active.headings.join(" › ")}`}
              </p>
            </div>
            <button ref={closeButton} className="icon-button" onClick={closePassage} aria-label="Close passage">
              <Close />
            </button>
          </div>
          <div className="sheet">
            <mark>{active.text}</mark>
          </div>
          <button className="secondary" onClick={() => openSource(active)}>
            <ExternalLink size={16} /> Open document at {pages(active)}
          </button>
        </div>
      )}
    </div>
  );
}

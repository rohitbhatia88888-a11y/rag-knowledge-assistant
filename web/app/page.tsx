"use client";

import { useEffect, useState } from "react";
import { Chat } from "@/components/Chat";
import { Connectors } from "@/components/Connectors";
import { Documents } from "@/components/Documents";
import { ArrowRight, Check, Lock } from "@/components/Icons";
import { Wordmark } from "@/components/Wordmark";
import { API_URL, devToken } from "@/lib/api";
import { documentName, initials } from "@/lib/format";

type Identity = { token: string; userId: string; tenantId: string; groups: string[] };
type Persona = { id: string; name: string; about: string; questions: string[]; groups: string[]; documents: string[] };

const REPO_URL = "https://github.com/rohitbhatia88888-a11y/rag-knowledge-assistant";

/** Read identity from our session token's claims. The API verifies the signature; this is display only. */
function identityFromToken(token: string): Identity | null {
  try {
    const claims = JSON.parse(atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
    if (claims.exp && claims.exp * 1000 < Date.now()) return null;
    return { token, userId: claims.sub, tenantId: claims.tenant, groups: claims.groups ?? [] };
  } catch {
    return null;
  }
}

function loadPersona(): Persona | null {
  try {
    return JSON.parse(sessionStorage.getItem("persona") ?? "null");
  } catch {
    return null;
  }
}

export default function Home() {
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [persona, setPersona] = useState<Persona | null>(null);
  const [ssoError, setSsoError] = useState("");

  useEffect(() => {
    // The SSO callback redirects here with #token=... or #error=...
    const fragment = new URLSearchParams(window.location.hash.slice(1));
    if (fragment.has("token") || fragment.has("error")) {
      history.replaceState(null, "", window.location.pathname);
      const id = fragment.get("token") ? identityFromToken(fragment.get("token")!) : null;
      if (id) {
        sessionStorage.setItem("token", id.token);
        setIdentity(id);
      } else {
        setSsoError(fragment.get("error") ?? "Sign-in failed");
      }
      return;
    }
    const saved = sessionStorage.getItem("token");
    if (saved) {
      setIdentity(identityFromToken(saved));
      setPersona(loadPersona());
    }
  }, []);

  function signOut() {
    sessionStorage.removeItem("token");
    sessionStorage.removeItem("persona");
    setIdentity(null);
    setPersona(null);
  }

  if (!identity) {
    return (
      <SignIn
        initialError={ssoError}
        onSignIn={(id, p) => {
          sessionStorage.setItem("token", id.token);
          if (p) sessionStorage.setItem("persona", JSON.stringify(p));
          setIdentity(id);
          setPersona(p ?? null);
        }}
      />
    );
  }

  const displayName = persona ? persona.name : identity.userId;
  return (
    <div className="shell">
      <aside className="sidebar">
        <Wordmark />
        <div className="who">
          <span className="avatar" aria-hidden="true">
            {initials(displayName)}
          </span>
          <div className="who-text">
            <strong>{displayName}</strong>
            <span className="muted small">
              {identity.tenantId}
              {identity.groups.length ? ` · ${identity.groups.join(", ")}` : ""}
            </span>
          </div>
          <button className="link small" onClick={signOut}>
            {persona ? "Switch" : "Sign out"}
          </button>
        </div>
        {persona && (
          <p className="demo-note">
            Public demo. Halcyon Robotics is fictional. Switch persona to see answers change with what each role may
            read.
          </p>
        )}
        {identity.groups.includes("admins") && <Connectors token={identity.token} />}
        <Documents token={identity.token} userId={identity.userId} readOnly={!!persona} />
      </aside>
      <main className="main">
        <Chat key={identity.token} token={identity.token} suggestions={persona?.questions ?? []} />
      </main>
    </div>
  );
}

function SignIn({
  onSignIn,
  initialError,
}: {
  onSignIn: (id: Identity, persona?: Persona) => void;
  initialError: string;
}) {
  const [config, setConfig] = useState<{ sso: boolean; dev_login: boolean; demo?: boolean } | null>(null);
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [userId, setUserId] = useState("alice");
  const [tenantId, setTenantId] = useState("acme");
  const [groups, setGroups] = useState("hr");
  const [error, setError] = useState(initialError);

  useEffect(() => {
    fetch(`${API_URL}/auth/config`)
      .then((r) => r.json())
      .then((c) => {
        setConfig(c);
        if (c.demo) fetch(`${API_URL}/auth/demo/personas`).then((r) => r.json()).then(setPersonas);
      })
      .catch(() => setError("Cannot reach the API"));
  }, []);

  async function pickPersona(p: Persona) {
    const res = await fetch(`${API_URL}/auth/demo/token`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ persona: p.id }),
    });
    const id = res.ok ? identityFromToken((await res.json()).token) : null;
    if (id) onSignIn(id, p);
    else setError("Could not sign in");
  }

  // Until the API says which sign-in this deployment uses, show nothing rather than the wrong screen.
  if (!config && !error) return <div className="signin" aria-busy="true" />;

  if (config?.demo) {
    // Every document any persona can read, in a stable order (company-wide first).
    const allDocs = Array.from(new Set(personas.flatMap((p) => p.documents)));
    return (
      <div className="landing">
        <header className="topbar">
          <Wordmark />
          <a className="topbar-link" href={REPO_URL}>
            Source &amp; evaluation
          </a>
        </header>

        <section className="hero">
          <p className="eyebrow">Public demo · Halcyon Robotics (fictional)</p>
          <h1>
            Answers from your company&rsquo;s documents, <em>limited to the ones you&rsquo;re allowed to read.</em>
          </h1>
          <p className="lede">
            Folio searches policies, runbooks and contracts, answers in plain language, and cites the exact page and
            passage. Permissions are enforced inside retrieval, so restricted documents never reach the model.
          </p>
          <dl className="stats">
            <div>
              <dt>0.96</dt>
              <dd>RAGAS faithfulness on 141 test questions</dd>
            </div>
            <div>
              <dt>25 / 25</dt>
              <dd>unanswerable or forbidden questions refused</dd>
            </div>
            <div>
              <dt>p. &amp; §</dt>
              <dd>every claim cites its page and passage</dd>
            </div>
          </dl>
        </section>

        <section className="pick" aria-labelledby="pick-title">
          <h2 id="pick-title">Choose who you are</h2>
          <div className="personas">
            {personas.map((p) => (
              <button key={p.id} className="persona" onClick={() => pickPersona(p)}>
                <span className="persona-top">
                  <span className="avatar" aria-hidden="true">
                    {initials(p.name)}
                  </span>
                  <strong>{p.name}</strong>
                  <ArrowRight size={18} />
                </span>
                <span className="persona-about">{p.about}</span>
                <span className="meter" aria-label={`Can read ${p.documents.length} of ${allDocs.length} documents`}>
                  <span className="meter-bar" aria-hidden="true">
                    {allDocs.map((d) => (
                      <span key={d} className={p.documents.includes(d) ? "on" : ""} />
                    ))}
                  </span>
                  <span className="meter-label">
                    {p.documents.length} of {allDocs.length} documents
                  </span>
                </span>
              </button>
            ))}
          </div>
          {error && <p className="error">{error}</p>}
        </section>

        {personas.length > 0 && (
          <section className="matrix-section" aria-labelledby="matrix-title">
            <h2 id="matrix-title">Who can read what</h2>
            <p className="muted">
              The same question gets a different answer for each role. Retrieval filters by these permissions before
              ranking, so an engineer asking about salary bands gets &ldquo;I don&rsquo;t know&rdquo;, not a leak.
            </p>
            <div className="matrix-scroll">
              <table className="matrix">
                <thead>
                  <tr>
                    <th scope="col">Document</th>
                    {personas.map((p) => (
                      <th key={p.id} scope="col">
                        {p.name}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {allDocs.map((d) => (
                    <tr key={d}>
                      <th scope="row">{documentName(d)}</th>
                      {personas.map((p) =>
                        p.documents.includes(d) ? (
                          <td key={p.id} className="yes">
                            <Check size={16} />
                            <span className="visually-hidden">can read</span>
                          </td>
                        ) : (
                          <td key={p.id} className="no">
                            <Lock size={14} />
                            <span className="visually-hidden">no access</span>
                          </td>
                        ),
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}

        <footer className="landing-foot muted small">
          Questions are rate-limited. Built with FastAPI, Qdrant hybrid search, a cross-encoder re-ranker and Claude
          Haiku. <a href={REPO_URL}>Read the code and evaluation</a>.
        </footer>
      </div>
    );
  }

  return (
    <div className="signin">
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          try {
            const g = groups.split(",").map((s) => s.trim()).filter(Boolean);
            const id = identityFromToken(await devToken(userId, tenantId, g));
            if (id) onSignIn(id);
          } catch (err) {
            setError((err as Error).message);
          }
        }}
      >
        <Wordmark />
        <h1>Sign in</h1>
        {config?.sso && (
          <a className="button" href={`${API_URL}/auth/oidc/login`}>
            Sign in with SSO
          </a>
        )}
        {config?.dev_login && (
          <>
            <p className="muted small">Dev sign-in: pick a user and groups to test permissions.</p>
            <label>
              User <input value={userId} onChange={(e) => setUserId(e.target.value)} required />
            </label>
            <label>
              Tenant <input value={tenantId} onChange={(e) => setTenantId(e.target.value)} required />
            </label>
            <label>
              Groups <input value={groups} onChange={(e) => setGroups(e.target.value)} placeholder="hr, admins" />
            </label>
            <button type="submit">Sign in</button>
          </>
        )}
        {config && !config.sso && !config.dev_login && <p className="muted">No sign-in method is configured.</p>}
        {error && <p className="error">{error}</p>}
      </form>
    </div>
  );
}

"use client";

import { useEffect, useState } from "react";
import { Chat } from "@/components/Chat";
import { Connectors } from "@/components/Connectors";
import { Documents } from "@/components/Documents";
import { API_URL, devToken } from "@/lib/api";

type Identity = { token: string; userId: string; tenantId: string; groups: string[] };
type Persona = { id: string; name: string; about: string; questions: string[]; groups: string[] };

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

  return (
    <div className="shell">
      <aside className="sidebar">
        {persona && (
          <div className="demo-banner">
            <strong>Public demo</strong> · Halcyon Robotics is a fictional company. Switch persona to see how answers change
            with what each role is allowed to read.
          </div>
        )}
        <div className="who">
          <div>
            <strong>{persona ? persona.name : identity.userId}</strong> · {identity.tenantId}
          </div>
          <div className="muted">{identity.groups.length ? identity.groups.join(", ") : "no groups"}</div>
          <button className="link" onClick={signOut}>
            {persona ? "Switch persona" : "Sign out"}
          </button>
        </div>
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

  if (config?.demo) {
    return (
      <div className="signin">
        <div className="demo-intro">
          <h1>Knowledge Assistant</h1>
          <p className="muted">
            A permission-aware RAG assistant over the documents of Halcyon Robotics, a fictional company. Answers cite
            the exact page and passage, and each persona only retrieves what its role may read. Pick one:
          </p>
          <div className="personas">
            {personas.map((p) => (
              <button key={p.id} className="persona" onClick={() => pickPersona(p)}>
                <strong>{p.name}</strong>
                <span className="muted small">{p.about}</span>
              </button>
            ))}
          </div>
          <p className="muted small">
            Questions are rate-limited. Source code and evaluation results:{" "}
            <a href="https://github.com/rohitbhatia88888-a11y/rag-knowledge-assistant">GitHub</a>.
          </p>
          {error && <p className="error">{error}</p>}
        </div>
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
        <h1>Knowledge Assistant</h1>
        {config?.sso && (
          <a className="button" href={`${API_URL}/auth/oidc/login`}>
            Sign in with SSO
          </a>
        )}
        {config?.dev_login && (
          <>
            <p className="muted">Dev sign-in: pick a user and groups to test permissions.</p>
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

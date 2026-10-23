"use client";

import { useEffect, useState } from "react";
import { Chat } from "@/components/Chat";
import { Connectors } from "@/components/Connectors";
import { Documents } from "@/components/Documents";
import { API_URL, devToken } from "@/lib/api";

type Identity = { token: string; userId: string; tenantId: string; groups: string[] };

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

export default function Home() {
  const [identity, setIdentity] = useState<Identity | null>(null);
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
    if (saved) setIdentity(identityFromToken(saved));
  }, []);

  if (!identity) {
    return (
      <SignIn
        initialError={ssoError}
        onSignIn={(id) => {
          sessionStorage.setItem("token", id.token);
          setIdentity(id);
        }}
      />
    );
  }

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="who">
          <div>
            <strong>{identity.userId}</strong> · {identity.tenantId}
          </div>
          <div className="muted">{identity.groups.length ? identity.groups.join(", ") : "no groups"}</div>
          <button
            className="link"
            onClick={() => {
              sessionStorage.removeItem("token");
              setIdentity(null);
            }}
          >
            Sign out
          </button>
        </div>
        {identity.groups.includes("admins") && <Connectors token={identity.token} />}
        <Documents token={identity.token} userId={identity.userId} />
      </aside>
      <main className="main">
        <Chat key={identity.token} token={identity.token} />
      </main>
    </div>
  );
}

function SignIn({ onSignIn, initialError }: { onSignIn: (id: Identity) => void; initialError: string }) {
  const [config, setConfig] = useState<{ sso: boolean; dev_login: boolean } | null>(null);
  const [userId, setUserId] = useState("alice");
  const [tenantId, setTenantId] = useState("acme");
  const [groups, setGroups] = useState("hr");
  const [error, setError] = useState(initialError);

  useEffect(() => {
    fetch(`${API_URL}/auth/config`)
      .then((r) => r.json())
      .then(setConfig)
      .catch(() => setError("Cannot reach the API"));
  }, []);

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

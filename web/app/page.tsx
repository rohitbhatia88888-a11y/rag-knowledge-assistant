"use client";

import { useEffect, useState } from "react";
import { Chat } from "@/components/Chat";
import { Documents } from "@/components/Documents";
import { devToken } from "@/lib/api";

type Identity = { token: string; userId: string; tenantId: string; groups: string[] };

export default function Home() {
  const [identity, setIdentity] = useState<Identity | null>(null);

  useEffect(() => {
    const saved = sessionStorage.getItem("identity");
    if (saved) setIdentity(JSON.parse(saved));
  }, []);

  if (!identity) {
    return (
      <SignIn
        onSignIn={(id) => {
          sessionStorage.setItem("identity", JSON.stringify(id));
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
              sessionStorage.removeItem("identity");
              setIdentity(null);
            }}
          >
            Switch user
          </button>
        </div>
        <Documents token={identity.token} userId={identity.userId} />
      </aside>
      <main className="main">
        <Chat key={identity.token} token={identity.token} />
      </main>
    </div>
  );
}

function SignIn({ onSignIn }: { onSignIn: (id: Identity) => void }) {
  const [userId, setUserId] = useState("alice");
  const [tenantId, setTenantId] = useState("acme");
  const [groups, setGroups] = useState("hr");
  const [error, setError] = useState("");

  return (
    <div className="signin">
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          try {
            const g = groups.split(",").map((s) => s.trim()).filter(Boolean);
            onSignIn({ token: await devToken(userId, tenantId, g), userId, tenantId, groups: g });
          } catch (err) {
            setError((err as Error).message);
          }
        }}
      >
        <h1>Knowledge Assistant</h1>
        <p className="muted">Dev sign-in. Production uses SSO; pick a user and groups to test permissions.</p>
        <label>
          User <input value={userId} onChange={(e) => setUserId(e.target.value)} required />
        </label>
        <label>
          Tenant <input value={tenantId} onChange={(e) => setTenantId(e.target.value)} required />
        </label>
        <label>
          Groups <input value={groups} onChange={(e) => setGroups(e.target.value)} placeholder="hr, eng" />
        </label>
        <button type="submit">Sign in</button>
        {error && <p className="error">{error}</p>}
      </form>
    </div>
  );
}

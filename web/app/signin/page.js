"use client";

import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import GreenHeader from "../../components/GreenHeader";
import { registerAccount, signInAccount } from "../../lib/auth";

export default function SignInPage() {
  return (
    <Suspense fallback={null}>
      <SignIn />
    </Suspense>
  );
}

function SignIn() {
  const params = useSearchParams();
  const next = params.get("next") || "/";
  const [mode, setMode] = useState("signin");
  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(ev) {
    ev.preventDefault();
    setBusy(true);
    setError("");
    try {
      if (mode === "create") {
        await registerAccount({ email, username, password });
      } else {
        await signInAccount({ identifier, password });
      }
      // a full load, so the private pages see the new session cookie
      window.location.href = next.startsWith("/") ? next : "/";
    } catch (e) {
      setError(e.message || "Could not sign in.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="home-root">
      <GreenHeader compact />
      <main className="auth-wrap">
        <form className="auth-card" onSubmit={submit}>
          <h1>{mode === "create" ? "Create your Crossr account" : "Sign in to Crossr"}</h1>
          <p className="auth-copy">
            Sign in, or create your account with the email you were invited with.
          </p>
          <div className="auth-tabs">
            <button type="button" className={mode === "create" ? "active" : ""} onClick={() => setMode("create")}>
              Create account
            </button>
            <button type="button" className={mode === "signin" ? "active" : ""} onClick={() => setMode("signin")}>
              Sign in
            </button>
          </div>
          {mode === "create" ? (
            <>
              <label>
                Email
                <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="email" />
              </label>
              <label>
                Username
                <input value={username} onChange={(e) => setUsername(e.target.value)} required autoComplete="username" />
              </label>
            </>
          ) : (
            <label>
              Email or username
              <input value={identifier} onChange={(e) => setIdentifier(e.target.value)} required autoComplete="username" />
            </label>
          )}
          <label>
            Password
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required autoComplete={mode === "create" ? "new-password" : "current-password"} />
          </label>
          {error ? <p className="error">{error}</p> : null}
          <button className="primary" disabled={busy}>
            {busy ? "Saving…" : mode === "create" ? "Create account" : "Sign in"}
          </button>
        </form>
      </main>
    </div>
  );
}

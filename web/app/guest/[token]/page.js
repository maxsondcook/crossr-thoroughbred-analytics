"use client";

import { useState } from "react";
import GreenHeader from "../../../components/GreenHeader";
import { apiPost } from "../../../lib/api";

// A one-time guest link (crossr_core/guest_pass.py). Nothing is used up until the button is
// clicked, so link previews in messages and email scanners can't spend the pass.
export default function GuestLinkPage({ params }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function open() {
    setBusy(true);
    setError("");
    try {
      await apiPost("/api/access/guest/redeem", { token: params.token });
      window.location.href = "/"; // a full load, so the private pages see the new session cookie
    } catch (e) {
      setError(e.message || "This guest link can't be used.");
      setBusy(false);
    }
  }

  return (
    <div className="home-root">
      <GreenHeader compact />
      <main className="auth-wrap">
        <div className="auth-card">
          <h1>You&apos;re invited to Crossr</h1>
          <p className="auth-copy">
            This link works once. Opening it gives this browser 24 hours of access, with no account needed.
          </p>
          {error ? <p className="error">{error}</p> : null}
          <button className="primary" type="button" onClick={open} disabled={busy}>
            {busy ? "Opening…" : "Open Crossr"}
          </button>
        </div>
      </main>
    </div>
  );
}

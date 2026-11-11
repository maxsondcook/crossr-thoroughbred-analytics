"use client";

import { useEffect, useState } from "react";
import { apiGet, apiPost } from "../../../lib/api";

const ACTIONS = {
  pending: [["approved", "Approve"], ["denied", "Deny"]],
  approved: [["revoked", "Revoke"]],
  denied: [["approved", "Approve"]],
  revoked: [["approved", "Restore"]],
};

export default function AccessAdminPage() {
  const [rows, setRows] = useState(null);
  const [error, setError] = useState("");
  const [invite, setInvite] = useState("");

  async function load() {
    try {
      const data = await apiGet("/api/access/requests");
      setRows(data.requests || []);
      setError("");
    } catch (e) {
      setError(e.status === 403 ? "Only the owner can manage access." : e.message);
    }
  }

  async function decide(email, status) {
    try {
      const data = await apiPost("/api/access/decide", { email, status });
      setRows(data.requests || []);
    } catch (e) {
      setError(e.message);
    }
  }

  useEffect(() => { load(); }, []);

  const pending = (rows || []).filter((r) => r.status === "pending").length;
  return (
    <div className="barn-grid">
      <div className="panel wide">
        <h4>ACCESS</h4>
        <p>
          ONLY EMAILS YOU APPROVE CAN CREATE AN ACCOUNT OR SIGN IN. TO LET SOMEONE IN, APPROVE THEIR EMAIL HERE, THEN SEND
          THEM YOUR PRIVATE SIGN-IN ADDRESS. EVERYONE ELSE SEES &quot;SITE NOT FOUND&quot;. {pending ? `${pending} WAITING FOR YOU.` : ""}
        </p>
        <form
          onSubmit={(e) => { e.preventDefault(); if (invite.trim()) { decide(invite.trim(), "approved"); setInvite(""); } }}
          style={{ display: "flex", gap: 8, marginBottom: 16 }}
        >
          <input type="email" placeholder="Email to let in" value={invite} onChange={(e) => setInvite(e.target.value)} />
          <button className="primary">Approve</button>
        </form>
        {error ? <p className="error">{error}</p> : null}
        {rows === null && !error ? <p className="empty">Loading…</p> : null}
        {rows && rows.length ? (
          <div className="history-wrap">
            <table>
              <thead>
                <tr><th>Email</th><th>Name</th><th>Reason</th><th>Status</th><th>Requested</th><th /></tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.email}>
                    <td>{r.email}</td>
                    <td>{r.name || "—"}</td>
                    <td>{r.reason || "—"}</td>
                    <td>{r.status.toUpperCase()}</td>
                    <td>{(r.created_at || "").slice(0, 10)}</td>
                    <td style={{ whiteSpace: "nowrap" }}>
                      {(ACTIONS[r.status] || []).map(([status, label]) => (
                        <button key={status} type="button" className="ghost" onClick={() => decide(r.email, status)}>{label}</button>
                      ))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : rows ? <p className="empty">No requests yet.</p> : null}
      </div>
      <GuestLinks />
    </div>
  );
}

const GUEST_ACTIVE = new Set(["unused", "active"]);

function GuestLinks() {
  const [passes, setPasses] = useState(null);
  const [label, setLabel] = useState("");
  const [made, setMade] = useState("");
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState("");

  async function run(fn) {
    try {
      setError("");
      await fn();
    } catch (e) {
      setError(e.status === 403 ? "Only the owner can make guest links." : e.message);
    }
  }

  useEffect(() => { run(async () => setPasses((await apiGet("/api/access/guest-passes")).passes || [])); }, []);

  function create(e) {
    e.preventDefault();
    run(async () => {
      const data = await apiPost("/api/access/guest-passes", { label });
      setMade(`${window.location.origin}${data.pass.link_path}`);
      setCopied(false);
      setPasses(data.passes || []);
      setLabel("");
    });
  }

  function revoke(id) {
    run(async () => setPasses((await apiPost(`/api/access/guest-passes/${id}/revoke`, {})).passes || []));
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(made);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  return (
    <div className="panel wide">
      <h4>ONE-TIME GUEST LINKS</h4>
      <p>
        A GUEST LINK WORKS ONCE: THE FIRST PERSON TO OPEN IT GETS 24 HOURS OF ACCESS WITHOUT AN ACCOUNT. UNOPENED LINKS
        EXPIRE AFTER 7 DAYS. THE LINK IS SHOWN ONLY ONCE, SO COPY IT NOW.
      </p>
      <form onSubmit={create} style={{ display: "flex", gap: 8, marginBottom: 16 }}>
        <input placeholder="Who is it for? (optional)" value={label} onChange={(e) => setLabel(e.target.value)} maxLength={120} />
        <button className="primary">Make link</button>
      </form>
      {made ? (
        <div style={{ display: "flex", gap: 8, marginBottom: 16, alignItems: "center" }}>
          <input readOnly value={made} onFocus={(e) => e.target.select()} style={{ flex: 1 }} />
          <button type="button" className="ghost" onClick={copy}>{copied ? "Copied" : "Copy"}</button>
        </div>
      ) : null}
      {error ? <p className="error">{error}</p> : null}
      {passes && passes.length ? (
        <div className="history-wrap">
          <table>
            <thead>
              <tr><th>For</th><th>Status</th><th>Made</th><th>Opened</th><th>Access ends</th><th /></tr>
            </thead>
            <tbody>
              {passes.map((p) => (
                <tr key={p.id}>
                  <td>{p.label || `Guest ${p.id}`}</td>
                  <td>{p.status.toUpperCase()}</td>
                  <td>{(p.created_at || "").slice(0, 16)}</td>
                  <td>{(p.claimed_at || "—").slice(0, 16)}</td>
                  <td>{(p.pass_expires_at || "—").slice(0, 16)}</td>
                  <td>
                    {GUEST_ACTIVE.has(p.status) ? (
                      <button type="button" className="ghost" onClick={() => revoke(p.id)}>Revoke</button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : passes ? <p className="empty">No guest links yet.</p> : null}
    </div>
  );
}

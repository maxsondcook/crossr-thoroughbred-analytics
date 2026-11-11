"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { apiGet } from "../../lib/api";
import PedigreeChart from "../../components/PedigreeChart";

// Text box with horse-name suggestions from /api/search.
function HorsePicker({ id, label, value, onChange }) {
  const [hits, setHits] = useState([]);

  useEffect(() => {
    if (value.trim().length < 2) {
      setHits([]);
      return;
    }
    const t = setTimeout(() => {
      apiGet(`/api/search?q=${encodeURIComponent(value)}&limit=10`)
        .then((d) => setHits(d.results || []))
        .catch(() => setHits([]));
    }, 180);
    return () => clearTimeout(t);
  }, [value]);

  return (
    <label style={{ flex: "1 1 220px" }}>
      <span className="sub">{label}</span>
      <input
        list={`${id}-hits`}
        value={value}
        placeholder="Horse name"
        onChange={(e) => onChange(e.target.value)}
        style={{ width: "100%" }}
      />
      <datalist id={`${id}-hits`}>
        {hits.map((h) => {
          const name = h.year ? `${h.name} (${h.year})` : h.name;
          return <option key={`${h.key}|${h.year || ""}`} value={name} />;
        })}
      </datalist>
    </label>
  );
}

function CompareInner() {
  const router = useRouter();
  const params = useSearchParams();
  const a = params.get("a") || "";
  const b = params.get("b") || "";
  const [pickA, setPickA] = useState(a);
  const [pickB, setPickB] = useState(b);
  // undefined = loading, null = not found
  const [left, setLeft] = useState(undefined);
  const [right, setRight] = useState(undefined);
  const [sim, setSim] = useState(undefined);

  useEffect(() => {
    setPickA(a);
    setPickB(b);
    setLeft(undefined);
    setRight(undefined);
    setSim(undefined);
    if (a) apiGet(`/api/horses/${encodeURIComponent(a)}`).then(setLeft).catch(() => setLeft(null));
    if (b) apiGet(`/api/horses/${encodeURIComponent(b)}`).then(setRight).catch(() => setRight(null));
    if (a && b) {
      const qs = new URLSearchParams({ a, b });
      apiGet(`/api/similarity?${qs}`)
        .then(setSim)
        .catch(() => setSim(null));
    }
  }, [a, b]);

  function submit(ev) {
    ev.preventDefault();
    const qs = new URLSearchParams();
    if (pickA.trim()) qs.set("a", pickA.trim());
    if (pickB.trim()) qs.set("b", pickB.trim());
    router.push(`/compare?${qs}`);
  }

  function col(name, d) {
    if (!name) return <p className="empty">Choose a horse above.</p>;
    if (d === undefined) return <p className="empty">Loading…</p>;
    if (d === null) return <p className="empty">No horse found for “{name}”.</p>;
    if (d.ambiguous) {
      return <p className="empty">More than one {d.name}: add the foaling year ({(d.years || []).join(" or ")}).</p>;
    }
    const s = d.stallion || {};
    return (
      <div className="panel">
        <h2>{d.name}</h2>
        {d.stallion ? (
          <>
            <p>{s.farm} · {s.stud_fee} · Rank {s.rank || "NA"}</p>
            <p>Foaled {s.year_foaled || d.year} · Stud {s.entered_stud}</p>
          </>
        ) : d.year ? (
          <p>Foaled {d.year}</p>
        ) : null}
        <PedigreeChart tree={d.pedigree} showSubject={false} />
      </div>
    );
  }

  return (
    <div>
      <form className="panel" onSubmit={submit} style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "flex-end", marginBottom: 12 }}>
        <HorsePicker id="cmp-a" label="First horse" value={pickA} onChange={setPickA} />
        <HorsePicker id="cmp-b" label="Second horse" value={pickB} onChange={setPickB} />
        <button className="primary" type="submit" disabled={!pickA.trim() || !pickB.trim()}>
          Compare
        </button>
      </form>
      {a && b ? (
        <div className="panel" style={{ textAlign: "center", marginBottom: 12 }}>
          <h4>PEDIGREE SIMILARITY</h4>
          {sim === undefined ? (
            <p className="empty">Calculating similarity…</p>
          ) : sim === null ? (
            <p className="empty">Could not compare these pedigrees.</p>
          ) : (
            <>
              <b style={{ display: "block", fontSize: 36, color: "var(--green)", marginTop: 6 }}>{sim.percent}%</b>
              {sim.label ? <p className="sub">{sim.label}</p> : null}
              {sim.label === "Shared ancestors" && sim.shared_ancestors?.length ? (
                <p className="sub">{sim.shared_ancestors.join(", ")}</p>
              ) : null}
            </>
          )}
        </div>
      ) : (
        <p className="empty">Pick two horses to compare their pedigrees side by side.</p>
      )}
      {a || b ? (
        <div className="dash" style={{ gridTemplateColumns: "1fr 1fr" }}>
          {col(a, left)}
          {col(b, right)}
        </div>
      ) : null}
    </div>
  );
}

export default function ComparePage() {
  return (
    <Suspense fallback={<p className="empty">Loading comparison…</p>}>
      <CompareInner />
    </Suspense>
  );
}

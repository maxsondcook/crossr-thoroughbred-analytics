"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { apiGet } from "../lib/api";

const CATS = [
  ["yearlings", "Yearlings", "var(--accent, #e3a24a)"],
  ["weanlings", "Weanlings", "#5fb8a6"],
  ["all", "All horses", "#8aa0c8"],
];

function money(n) {
  if (n == null) return "—";
  return `$${Math.round(n).toLocaleString()}`;
}

/** Weighted average over completed-or-not years [from, to] from the by-year rows. */
function rangeSummary(rows, cat, from, to) {
  let sold = 0;
  let gross = 0;
  for (const r of rows || []) {
    if (r.year < from || r.year > to) continue;
    const n = Number(r[`${cat}_sold`]) || 0;
    const avg = Number(r[`${cat}_avg`]);
    if (n && Number.isFinite(avg)) {
      sold += n;
      gross += avg * n;
    }
  }
  return { sold, average: sold ? gross / sold : null };
}

function SummaryTable({ data }) {
  const latest = data.latest_completed_year;
  const years = (data.by_year || []).map((r) => r.year).sort((a, b) => a - b);
  const [range, setRange] = useState({ from: latest - 2, to: latest });
  useEffect(() => { setRange({ from: latest - 2, to: latest }); }, [latest]);
  const isDefault = range.from === latest - 2 && range.to === latest;
  const rangeLabel = range.from === range.to ? `${range.from}` : `${range.from}–${range.to}`;
  const periods = [
    ["current", `${data.current_year} (to date)`],
    ["latest", `${latest}`],
    ["lifetime", "Lifetime"],
  ];
  const setFrom = (v) => setRange((r) => ({ from: v, to: Math.max(v, r.to) }));
  const setTo = (v) => setRange((r) => ({ from: Math.min(v, r.from), to: v }));
  return (
    <div className="history-wrap">
      {years.length ? (
        <div className="range-picker">
          <span>Range</span>
          <select value={range.from} onChange={(e) => setFrom(Number(e.target.value))} aria-label="From year">
            {years.map((y) => <option key={y} value={y}>{y}</option>)}
          </select>
          <span>to</span>
          <select value={range.to} onChange={(e) => setTo(Number(e.target.value))} aria-label="To year">
            {years.map((y) => <option key={y} value={y}>{y}</option>)}
          </select>
          {!isDefault ? (
            <button className="ghost" type="button" onClick={() => setRange({ from: latest - 2, to: latest })}>
              Last 3 years
            </button>
          ) : null}
        </div>
      ) : null}
      <table>
        <thead>
          <tr>
            <th>Average price</th>
            <th className="range-col">{rangeLabel}</th>
            {periods.map(([k, label]) => <th key={k}>{label}</th>)}
          </tr>
        </thead>
        <tbody>
          {CATS.map(([cat, label]) => {
            // The default range uses the API's exact last-3-years figures (with median).
            const exact = isDefault ? data.summary?.[cat]?.last3 : null;
            const r = exact || rangeSummary(data.by_year, cat, range.from, range.to);
            return (
              <tr key={cat}>
                <td>{label}</td>
                <td
                  className="money range-col"
                  title={exact ? `${exact.sold} sold of ${exact.offered} offered · median ${money(exact.median)}` : `${r.sold} sold`}
                >
                  {r.average != null ? money(r.average) : "—"}
                  {r.sold ? <span className="muted" style={{ fontSize: 12 }}> · {r.sold}</span> : null}
                </td>
                {periods.map(([p]) => {
                  const s = data.summary?.[cat]?.[p];
                  return (
                    <td key={p} className="money" title={s ? `${s.sold} sold of ${s.offered} offered · median ${money(s.median)}` : ""}>
                      {s && s.average != null ? money(s.average) : "—"}
                      {s && s.sold ? <span className="muted" style={{ fontSize: 12 }}> · {s.sold}</span> : null}
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="sub" style={{ fontSize: 12, marginTop: 6 }}>
        Sold lots only (RNAs not counted as sales, outs excluded); small number = horses sold.
        {` ${data.current_year} is incomplete, so annual averages use ${latest}.`}
      </p>
    </div>
  );
}

function shortMoney(v) {
  return v >= 1e6 ? `$${(v / 1e6).toFixed(1)}M` : `$${Math.round(v / 1000)}k`;
}

function YearChart({ rows }) {
  const [hidden, setHidden] = useState({});
  const [active, setActive] = useState(null); // hovered / tapped year index
  const W = 640, H = 220, P = { l: 56, r: 12, t: 12, b: 28 };
  const shown = CATS.filter(([cat]) => !hidden[cat]);
  const values = rows.flatMap((r) => shown.map(([cat]) => r[`${cat}_avg`]).filter((v) => v != null));
  if (!rows.length || !values.length) return <p className="catalog-empty">No priced sales by year.</p>;
  const max = Math.max(...values) * 1.08;
  const x = (i) => P.l + (rows.length === 1 ? 0 : (i * (W - P.l - P.r)) / (rows.length - 1));
  const y = (v) => H - P.b - (v / max) * (H - P.t - P.b);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * max);
  const step = Math.max(1, Math.ceil(rows.length / 10));
  return (
    <div>
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 6 }}>
        {CATS.map(([cat, label, color]) => (
          <label key={cat} style={{ display: "flex", gap: 6, alignItems: "center", cursor: "pointer", fontSize: 13 }}>
            <input type="checkbox" checked={!hidden[cat]} onChange={() => setHidden((h) => ({ ...h, [cat]: !h[cat] }))} />
            <span style={{ width: 12, height: 3, background: color, display: "inline-block" }} />
            {label}
          </label>
        ))}
      </div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        style={{ width: "100%", height: "auto" }}
        role="img"
        aria-label="Average sale price by year"
        onMouseLeave={() => setActive(null)}
      >
        {ticks.map((t) => (
          <g key={t}>
            <line x1={P.l} x2={W - P.r} y1={y(t)} y2={y(t)} stroke="currentColor" opacity="0.12" />
            <text x={P.l - 6} y={y(t) + 4} fontSize="10" textAnchor="end" fill="currentColor" opacity="0.7">
              {t >= 1e6 ? `$${(t / 1e6).toFixed(1)}M` : `$${Math.round(t / 1000)}k`}
            </text>
          </g>
        ))}
        {rows.map((r, i) => (i % step === 0 || i === rows.length - 1) ? (
          <text key={r.year} x={x(i)} y={H - 8} fontSize="10" textAnchor="middle" fill="currentColor" opacity="0.7">
            {r.year}{r.complete ? "" : "*"}
          </text>
        ) : null)}
        {active != null && rows[active] ? (
          <line x1={x(active)} x2={x(active)} y1={P.t} y2={H - P.b} stroke="currentColor" opacity="0.35" strokeDasharray="3 3" />
        ) : null}
        {shown.map(([cat, label, color]) => {
          // Break the line at years with no sales instead of drawing through them.
          const segs = [];
          let cur = [];
          rows.forEach((r, i) => {
            const v = r[`${cat}_avg`];
            if (v == null) { if (cur.length) segs.push(cur); cur = []; } else cur.push([x(i), y(v), r, v]);
          });
          if (cur.length) segs.push(cur);
          return (
            <g key={cat}>
              {segs.map((seg, k) => (
                <polyline key={k} fill="none" stroke={color} strokeWidth="2" points={seg.map(([a, b]) => `${a},${b}`).join(" ")} />
              ))}
              {segs.flat().map(([a, b, r, v]) => (
                <g key={`${cat}-${r.year}`}>
                  <circle cx={a} cy={b} r={active != null && rows[active] === r ? 5 : 3} fill={color}>
                    <title>{`${label} ${r.year}: ${money(v)} (${r[`${cat}_sold`]} sold)`}</title>
                  </circle>
                  {active != null && rows[active] === r ? (
                    <text x={a} y={b - 9} fontSize="11" fontWeight="700" textAnchor="middle" fill={color}>{shortMoney(v)}</text>
                  ) : null}
                </g>
              ))}
            </g>
          );
        })}
        {/* Invisible year columns: hover or tap one to read its numbers below. */}
        {rows.map((r, i) => {
          const half = rows.length === 1 ? (W - P.l - P.r) / 2 : (W - P.l - P.r) / (rows.length - 1) / 2;
          return (
            <rect
              key={`hit-${r.year}`}
              x={x(i) - half}
              y={P.t}
              width={half * 2}
              height={H - P.t - P.b}
              fill="transparent"
              style={{ cursor: "pointer" }}
              onMouseEnter={() => setActive(i)}
              onClick={() => setActive((cur) => (cur === i ? null : i))}
            />
          );
        })}
      </svg>
      {active != null && rows[active] ? (
        <div className="chart-readout">
          <b>{rows[active].year}{rows[active].complete ? "" : " (to date)"}</b>
          {shown.map(([cat, label, color]) => {
            const v = rows[active][`${cat}_avg`];
            return (
              <span key={cat}>
                <i style={{ background: color }} />
                {label}: {v != null ? `${money(v)} avg · ${rows[active][`${cat}_sold`] ?? 0} sold` : "no sales"}
              </span>
            );
          })}
        </div>
      ) : (
        <p className="sub" style={{ fontSize: 12, margin: "4px 0 0" }}>Hover or tap a year to see its numbers.</p>
      )}
      <p className="sub" style={{ fontSize: 12 }}>* year in progress. Gaps are years with no sales in that category.</p>
    </div>
  );
}

const SORTS = {
  hip_asc: (a, b) => (parseInt(a.hip, 10) || 0) - (parseInt(b.hip, 10) || 0),
  hip_desc: (a, b) => (parseInt(b.hip, 10) || 0) - (parseInt(a.hip, 10) || 0),
  price_desc: (a, b) => (b.price || 0) - (a.price || 0),
  price_asc: (a, b) => (a.price ?? Infinity) - (b.price ?? Infinity),
};

function SaleHorses({ lots, hrefFor }) {
  const [status, setStatus] = useState("all");
  const [type, setType] = useState("all");
  const [sex, setSex] = useState("all");
  const [bms, setBms] = useState([]);
  const [sort, setSort] = useState("hip_asc");
  const types = useMemo(() => [...new Set(lots.map((l) => l.type))].sort(), [lots]);
  const sexes = useMemo(() => [...new Set(lots.map((l) => l.sex).filter(Boolean))].sort(), [lots]);
  const damsires = useMemo(() => [...new Set(lots.map((l) => l.damsire).filter(Boolean))].sort(), [lots]);
  useEffect(() => { setStatus("all"); setType("all"); setSex("all"); setBms([]); }, [lots]);
  const shown = lots
    .filter((l) => status === "all" || l.status === status)
    .filter((l) => type === "all" || l.type === type)
    .filter((l) => sex === "all" || l.sex === sex)
    .filter((l) => !bms.length || bms.includes(l.damsire))
    .slice()
    .sort(SORTS[sort]);
  return (
    <div style={{ marginTop: 12 }}>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-start" }}>
        <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Sold status">
          <option value="all">Sold &amp; not sold</option>
          <option value="sold">Sold</option>
          <option value="not sold">Not sold (RNA)</option>
          <option value="out">Out</option>
        </select>
        <select value={type} onChange={(e) => setType(e.target.value)} aria-label="Horse type">
          <option value="all">All types</option>
          {types.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <select value={sex} onChange={(e) => setSex(e.target.value)} aria-label="Sex">
          <option value="all">All sexes</option>
          {sexes.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <select value={sort} onChange={(e) => setSort(e.target.value)} aria-label="Sort">
          <option value="hip_asc">Hip ↑</option>
          <option value="hip_desc">Hip ↓</option>
          <option value="price_desc">Price ↓</option>
          <option value="price_asc">Price ↑</option>
        </select>
      </div>
      {damsires.length ? (
        <div className="bms-filter">
          <span className="filter-label">
            Broodmare sires{bms.length ? ` · ${bms.length} selected` : " · tick any number"}
          </span>
          <div className="filter-sire-list" role="group" aria-label="Broodmare sires">
            {damsires.map((d) => (
              <label key={d} className="filter-check">
                <input
                  type="checkbox"
                  checked={bms.includes(d)}
                  onChange={() => setBms((cur) => (cur.includes(d) ? cur.filter((x) => x !== d) : [...cur, d]))}
                />
                <span>{d}</span>
                <em>{lots.filter((l) => l.damsire === d).length}</em>
              </label>
            ))}
          </div>
          {bms.length ? (
            <button type="button" className="ghost filter-clear" onClick={() => setBms([])}>Clear broodmare sires</button>
          ) : null}
        </div>
      ) : null}
      <div className="history-wrap" style={{ marginTop: 8 }}>
        <table>
          <thead>
            <tr><th>Hip</th><th>Horse</th><th>Type</th><th>Sex</th><th>Broodmare Sire</th><th>Status</th><th>Price</th></tr>
          </thead>
          <tbody>
            {shown.map((l) => (
              <tr key={l.id}>
                <td>{l.hip}</td>
                <td>{l.name ? <Link href={hrefFor(l.name)}>{l.name}</Link> : "—"}</td>
                <td>{l.type}</td>
                <td>{l.sex || "—"}</td>
                <td>{l.damsire || "—"}</td>
                <td>{l.status}</td>
                <td className="money">{l.status === "sold" ? money(l.price) : (l.price_raw || "—")}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!shown.length ? <p className="catalog-empty">No horses match these filters.</p> : null}
      </div>
    </div>
  );
}

export default function StallionSalesPanel({ name, hrefFor }) {
  const [data, setData] = useState(null);
  const [catalogId, setCatalogId] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    setError("");
    const q = catalogId ? `?catalog_id=${catalogId}` : "";
    apiGet(`/api/sales/sire/${encodeURIComponent(name)}/summary${q}`)
      .then(setData)
      .catch((e) => setError(e.message));
  }, [name, catalogId]);
  useEffect(() => { setCatalogId(null); setData(null); }, [name]);

  if (error) return <div className="panel panel-wide"><h4>SALES</h4><p className="error">{error}</p></div>;
  if (!data) return <div className="panel panel-wide"><h4>SALES</h4><p className="catalog-empty">Loading…</p></div>;
  if (!data.lots_total) return null; // no progeny ever offered: hide the panel
  const sale = data.sales.find((s) => s.catalog_id === data.selected_catalog_id);
  const ss = data.sale_stats || {};
  return (
    <div className="panel panel-wide">
      <h4>SALES · {data.lots_total.toLocaleString()} PROGENY OFFERED</h4>
      <SummaryTable data={data} />
      <h4 style={{ marginTop: 16 }}>AVERAGE PRICE BY YEAR</h4>
      <YearChart rows={data.by_year} />
      <h4 style={{ marginTop: 16 }}>BY SALE</h4>
      <select
        value={data.selected_catalog_id || ""}
        onChange={(e) => setCatalogId(Number(e.target.value))}
        aria-label="Sale"
        style={{ maxWidth: "100%" }}
      >
        {data.sales.map((s) => (
          <option key={s.catalog_id} value={s.catalog_id}>
            {s.year} · {s.title} — {s.progeny} offered, {s.sold} sold
          </option>
        ))}
      </select>
      {sale ? (
        <div className="kpi-row" style={{ marginTop: 10 }}>
          {CATS.map(([cat, label]) => ss[cat]?.offered ? (
            <div className="kpi" key={cat}>
              <span>{label}</span>
              <b>{money(ss[cat].average)}</b>
              <span className="muted" style={{ fontSize: 12 }}>
                {ss[cat].sold}/{ss[cat].offered} sold · high {money(ss[cat].high)}
              </span>
            </div>
          ) : null)}
        </div>
      ) : null}
      <SaleHorses lots={data.sale_lots || []} hrefFor={hrefFor} />
    </div>
  );
}

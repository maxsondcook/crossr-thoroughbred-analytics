"use client";

import { Fragment, Suspense, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { apiGet, horseHref } from "../../../lib/api";
import { isStarred, toggleStar, saveFavorite } from "../../../lib/barn";
import PedigreeChart from "../../../components/PedigreeChart";
import FemaleFamily from "../../../components/FemaleFamily";
import StallionSalesPanel from "../../../components/StallionSalesPanel";
import {
  horseHrefWithReturn,
  rememberSalesVisit,
  resolveReturnTarget,
} from "../../../lib/appNav";

function money(n) {
  if (n == null || n === "") return "—";
  if (typeof n === "string" && /\$/.test(n)) return n;
  const num = Number(n);
  if (!Number.isFinite(num)) return String(n);
  return `$${num.toLocaleString()}`;
}

function pickStat(source, ...keys) {
  if (!source || typeof source !== "object") return null;
  for (const key of keys) {
    const value = source[key];
    if (value != null && value !== "" && value !== "N/A") return value;
  }
  return null;
}

function formatStatValue(value) {
  if (value == null || value === "") return null;
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return null;
    return Number.isInteger(value) ? String(value) : String(value);
  }
  return String(value);
}

function collectStatCards(source, fields) {
  const cards = [];
  for (const field of fields) {
    const value = formatStatValue(pickStat(source, ...(field.keys || [field.label])));
    if (value == null) continue;
    cards.push({ label: field.label, value });
  }
  return cards;
}

const RACING_STAT_FIELDS = [
  { label: "Crops", keys: ["Crops"] },
  { label: "Foals", keys: ["Foals"] },
  { label: "Crops of Racing Age", keys: ["Crops of Racing Age"] },
  { label: "Foals of Racing Age", keys: ["Foals of Racing Age"] },
  { label: "Champions", keys: ["Champions"] },
  { label: "Graded Blacktype Winners", keys: ["Graded Blacktype Winners"] },
  { label: "Blacktype Winners", keys: ["Blacktype Winners"] },
  { label: "Blacktype Placers", keys: ["Blacktype Placers"] },
  { label: "Starters", keys: ["Starters"] },
  { label: "Starters %", keys: ["Starters %", "Starters%"] },
  { label: "Winners %", keys: ["Winners %", "Winners%", "Winners"] },
  { label: "Blacktype Winners %", keys: ["Blacktype Winners %", "Blacktype Winners%", "Black Type %", "BTW %"] },
  { label: "Blacktype Placers %", keys: ["Blacktype Placers %", "Blacktype Placers%"] },
  { label: "Wins %", keys: ["Wins %", "Wins%"] },
  { label: "Placings %", keys: ["Placings %", "Placings%"] },
  { label: "Comparable Index", keys: ["Comparable Index", "CI"] },
  { label: "Avg Distance Raced", keys: ["Avg Distance Raced"] },
  { label: "Avg Winning Distance", keys: ["Avg Winning Distance"] },
  { label: "Avg Max Win Distance", keys: ["Avg Max Win Distance"] },
  { label: "Avg Min Win Distance", keys: ["Avg Min Win Distance"] },
];

const EARNINGS_STAT_FIELDS = [
  { label: "Earnings", keys: ["Earnings", "Total Earnings"] },
  { label: "Avg Earnings / starter", keys: ["Avg Earnings/starter", "Average Earnings Per Runner", "Avg Earnings"] },
  { label: "Avg Earnings / start", keys: ["Avg Earnings/start"] },
  { label: "Median Earnings", keys: ["Median Earnings"] },
  { label: "Avg Earnings Index", keys: ["Avg Earnings Index", "AEI"] },
];

const LEGACY_STAT_FIELDS = [
  { label: "Total Earnings", keys: ["Total Earnings"] },
  { label: "Average Progeny Win Distance", keys: ["Average Progeny Win Distance"] },
  { label: "GSW / G1W Ratio", keys: ["GSW / G1W Ratio"] },
];

/** Broodmare-sire years as a ranking list, unless "Leading Broodmare Sires" already has them. */
function withBroodmareSireList(meta, rows, countLabel) {
  const lists = meta.lists || [];
  if (!rows.length || lists.some((l) => /broodmare/i.test(l.id))) return meta;
  const id = "broodmare_sire";
  return {
    ...meta,
    lists: [...lists, { id, label: "Broodmare Sire", group: "bloodhorse" }],
    columns: {
      ...(meta.columns || {}),
      [id]: [
        { key: "year", label: "Year", type: "number" },
        { key: "rank", label: "Rank", type: "number" },
        { key: "earnings", label: "Earnings", type: "money" },
        { key: "count", label: countLabel || "Runners", type: "number" },
      ],
    },
    rows: { ...(meta.rows || {}), [id]: rows.map((r) => ({ ...r, earnings_num: r.earnings, count_num: r.count })) },
  };
}

/** Race name without its "(Gr. 3)" / "(G1)" suffix when the class tag already shows the grade. */
function raceTitle(r) {
  const name = String(r.race_name || "");
  return raceGrade(r) ? name.replace(/\s*\((?:Gr\.?|Grade|G)\s*[1-3]\)\s*/i, " ").trim() : name;
}

/** Grade tag for any graded start (win or not): G1 / G2 / G3. */
function raceGrade(r) {
  if (r.class && /^G[1-3]$/.test(r.class)) return r.class;
  const g = String(r.grade || "").match(/[1-3]/) || String(r.race_name || "").match(/\((?:Gr\.?|Grade|G)\s*([1-3])\)/i);
  return g ? `G${g[1] || g[0]}` : null;
}

/** 2016-06-30 → 06-30-2016 (month-day-year). */
function mdy(date) {
  const m = String(date || "").match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? `${m[2]}-${m[3]}-${m[1]}` : (date || "—");
}

function ordinal(place) {
  const n = parseInt(place, 10);
  if (!Number.isFinite(n)) return place || "—";
  const tail = n % 100 >= 11 && n % 100 <= 13 ? "th" : ({ 1: "st", 2: "nd", 3: "rd" })[n % 10] || "th";
  return `${n}${tail}`;
}

/** Every start, oldest first (falls back to wins only when starts aren't available). */
function RaceList({ starts, wins }) {
  const all = (starts || []).length ? starts : (wins || []).map((w) => ({ ...w, place: "1" }));
  if (!all.length) return null;
  const winsOnly = !(starts || []).length;
  return (
    <div className="race-list">
      <h5>{winsOnly ? `Wins · ${all.length}` : `Races · ${all.length}`}</h5>
      <div className="history-wrap">
        <table>
          <thead>
            <tr><th>Date</th><th>Track</th><th>Race</th><th>Finish</th></tr>
          </thead>
          <tbody>
            {all.map((r, i) => (
              <tr key={`${r.date}-${r.race_number || i}`} className={String(r.place) === "1" ? "race-win" : undefined}>
                <td>{mdy(r.date)}</td>
                <td>{r.track || "—"}</td>
                <td>
                  {r.chart_url ? <a href={r.chart_url} target="_blank" rel="noopener noreferrer">{raceTitle(r) || "Race"}</a> : (raceTitle(r) || "—")}
                  {raceGrade(r) ? <span className="race-class"> {raceGrade(r)}</span> : null}
                </td>
                <td>{ordinal(r.place)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

const CAREER_VIEW = "__career";
const SEASON_VIEW = "__season";

/** Stat cards with "X" and "X %" folded into one box: "698 (52.6%)". */
function pairedCards(section, skip = []) {
  const entries = Object.entries(section || {}).filter(
    ([label, value]) => value != null && value !== "" && !skip.includes(label),
  );
  const pct = new Map(entries.filter(([label]) => label.endsWith(" %")).map(([label, v]) => [label.slice(0, -2), v]));
  return entries
    .filter(([label]) => !label.endsWith(" %"))
    .map(([label, value]) => ({ label, value: pct.has(label) ? `${value} (${pct.get(label)})` : String(value) }));
}

/** Career and current-season summaries for the Stallion Statistics dropdown (empty ones dropped). */
function progenySummaries(ps, extraCareer = []) {
  const earnings = ps?.earnings || {};
  const seasonYear = String(ps?.season?.Season || "").match(/\d{4}/)?.[0] || "";
  const seasonEarnKeys = Object.keys(earnings).filter((k) => /^\d{4} Earnings$/.test(k) || k === "Avg Earnings / runner");
  const careerEarn = Object.fromEntries(Object.entries(earnings).filter(([k]) => !seasonEarnKeys.includes(k)));
  const seasonEarn = Object.fromEntries(Object.entries(earnings).filter(([k]) => seasonEarnKeys.includes(k)));
  const career = [...pairedCards(ps?.career, ["As of"]), ...pairedCards(careerEarn), ...extraCareer];
  const season = [...pairedCards(ps?.season, ["Season"]), ...pairedCards(seasonEarn)];
  const out = [];
  if (career.length) {
    out.push({
      id: CAREER_VIEW,
      label: "Career progeny statistics",
      note: ps?.career?.["As of"] ? `As of ${ps.career["As of"]}` : "",
      cards: career,
    });
  }
  if (season.length) {
    out.push({
      id: SEASON_VIEW,
      label: `Current season${seasonYear ? ` (${seasonYear})` : ""}`,
      note: ps?.season?.Season && !/^\d{4}$/.test(ps.season.Season) ? ps.season.Season : "",
      cards: season,
    });
  }
  return out;
}

const TOP_PROGENY_PREVIEW = 25;

function parseMetricNumber(val) {
  if (val == null || val === "") return null;
  if (typeof val === "number" && Number.isFinite(val)) return val;
  const text = String(val).trim();
  const paren = text.match(/\(\s*\$?\s*([\d,]+(?:\.\d+)?)\s*\)\s*$/);
  if (paren) {
    const n = Number(paren[1].replace(/,/g, ""));
    return Number.isFinite(n) ? n : null;
  }
  const cleaned = text.replace(/[,$%\s]/g, "").replace(/^\$/, "");
  if (!cleaned || cleaned === "-" || cleaned === ".") return null;
  const n = Number(cleaned);
  return Number.isFinite(n) ? n : null;
}

function formatRankingCell(row, col) {
  const key = col.key;
  const raw = row[key];
  if (raw == null || raw === "") return "—";
  if (key === "rank") return `#${raw}`;
  if (col.type === "money") {
    if (typeof raw === "string" && /\$/.test(raw)) return raw;
    const n = row[`${key}_num`] ?? parseMetricNumber(raw);
    return n != null ? money(n) : String(raw);
  }
  return String(raw);
}

// Shown elsewhere on the page, so left out of the rankings table.
const RANKING_HIDDEN_KEYS = new Set(["chief_earner", "chief_earner_earnings", "stud_fee", "farm"]);
// Runner/winner column pairs collapsed into one Win% column.
// Raw runner/winner counts are left out; SW% (stakes winners ÷ runners) stands in for BT SWs.
const RUNNER_KEYS = new Set([
  "runners", "winners",
  "all_surfaces_runners", "all_surfaces_winners",
  "turf_runners", "turf_winners",
]);
const SW_PCT = { key: "sw_pct", label: "SW%", type: "percent" };

function shapeRankingTable(rawColumns, rawRows, rankList) {
  const keys = new Set(rawColumns.map((c) => c.key));
  const canSwPct = keys.has("runners") && keys.has("stakes_winners");
  const replacesStakesWins = canSwPct && keys.has("stakes_wins");
  const columns = [];
  for (const col of rawColumns) {
    // The Stud Fee list ranks by fee, so it keeps that column.
    if (RANKING_HIDDEN_KEYS.has(col.key) && !(rankList === "stud_fee" && col.key === "stud_fee")) continue;
    // Broodmare-sire tables keep Runners (it was the old Broodmare Sire panel's count column).
    if (RUNNER_KEYS.has(col.key) && !(rankList === "leading_broodmare_sires" && col.key === "runners")) continue;
    if (col.key === "stakes_wins" && replacesStakesWins) {
      columns.push(SW_PCT);
      continue;
    }
    columns.push(col);
    if (col.key === "stakes_winners" && canSwPct && !replacesStakesWins) columns.push(SW_PCT);
  }
  const rows = canSwPct
    ? rawRows.map((row) => {
        const r = row.runners_num ?? parseMetricNumber(row.runners);
        const sw = row.stakes_winners_num ?? parseMetricNumber(row.stakes_winners);
        const pct = r && sw != null ? (sw / r) * 100 : null;
        return { ...row, sw_pct_num: pct, sw_pct: pct != null ? `${pct.toFixed(1)}%` : null };
      })
    : rawRows;
  return { columns, rows };
}

// Regional international lists fold into their parent list as earnings columns.
const RANKING_REGIONS = [
  { suffix: "_north_america", key: "north_america_earnings", label: "NA" },
  { suffix: "_japan_and_hong_kong", key: "japan_hong_kong_earnings", label: "HK/Japan" },
  { suffix: "_europe_and_dubai", key: "europe_dubai_earnings", label: "Europe/ME" },
];

function regionalParent(listId) {
  const region = RANKING_REGIONS.find((r) => listId.endsWith(r.suffix));
  return region ? listId.slice(0, -region.suffix.length) : null;
}

function mergeRegionalLists(meta) {
  const allLists = meta.lists || [];
  const ids = new Set(allLists.map((l) => l.id));
  const lists = allLists.filter((l) => {
    const parent = regionalParent(l.id);
    return !(parent && ids.has(parent));
  });
  const columns = { ...(meta.columns || {}) };
  const rows = { ...(meta.rows || {}) };
  for (const list of lists) {
    const regional = RANKING_REGIONS.filter((r) => ids.has(list.id + r.suffix));
    if (!regional.length) continue;
    const byYear = new Map();
    for (const row of rows[list.id] || []) byYear.set(row.year, { ...row });
    for (const r of regional) {
      for (const row of rows[list.id + r.suffix] || []) {
        const merged = byYear.get(row.year) || {
          year: row.year,
          rank: row.rank,
          year_earnings: row.year_earnings,
          year_earnings_num: row.year_earnings_num,
        };
        if (merged[r.key] == null && row.region_earnings != null) {
          merged[r.key] = row.region_earnings;
          merged[`${r.key}_num`] = row.region_earnings_num;
        }
        byYear.set(row.year, merged);
      }
    }
    rows[list.id] = [...byYear.values()];
    columns[list.id] = [
      { key: "year", label: "Year", type: "number" },
      { key: "rank", label: "Rank", type: "number" },
      { key: "year_earnings", label: "Earnings", type: "money" },
      ...regional.map((r) => ({ key: r.key, label: r.label, type: "money" })),
    ];
  }
  return splitSalesList(combineCropLists({ lists, columns, rows }));
}

// First/second/third-crop lists cover one year each, so they show as one list with a Crop column.
const CROP_LIST_GROUPS = [
  {
    id: "leading_crop_sires",
    label: "Leading 1st–3rd Crop Sires",
    parts: [
      ["leading_first_crop_sires", "1st"],
      ["leading_second_crop_sires", "2nd"],
      ["leading_third_crop_sires", "3rd"],
    ],
  },
  {
    id: "leading_international_crop_sires",
    label: "Leading International 1st–3rd Crop Sires",
    parts: [
      ["leading_international_first_crop_sires", "1st"],
      ["leading_international_2nd_crop_sires", "2nd"],
      ["leading_international_3rd_crop_sires", "3rd"],
    ],
  },
];

// First-crop-only stats read as mostly blank across three crops, so leave them out.
const CROP_HIDDEN_KEYS = new Set(["repeat_winners", "current_2yos", "yearling_average"]);

// Yearling sale figures published with the AEI list get their own Sales Results list.
const AEI_LIST = "leading_sires_by_aei";
const SALES_LIST = "yearling_sales_results";
const SALES_KEYS = [
  "lifetime_yearlings_sold",
  "lifetime_yearling_average",
  "yearling_window",
  "window_yearlings_sold",
  "window_yearling_average",
];

const SALES_LABELS = {
  lifetime_yearlings_sold: "Lifetime Sold",
  lifetime_yearling_average: "Lifetime Avg",
  yearling_window: "Window",
  window_yearlings_sold: "Window Sold",
  window_yearling_average: "Window Avg",
};

function splitSalesList(meta) {
  const aeiCols = meta.columns[AEI_LIST];
  if (!aeiCols || !aeiCols.some((c) => SALES_KEYS.includes(c.key))) return meta;
  const columns = { ...meta.columns };
  columns[AEI_LIST] = aeiCols.filter((c) => !SALES_KEYS.includes(c.key));
  columns[SALES_LIST] = [
    { key: "year", label: "Year", type: "number" },
    ...aeiCols
      .filter((c) => SALES_KEYS.includes(c.key))
      .map((c) => ({ ...c, label: SALES_LABELS[c.key] || c.label })),
  ];
  const rows = { ...meta.rows, [SALES_LIST]: meta.rows[AEI_LIST] || [] };
  const lists = [...meta.lists];
  const at = lists.findIndex((l) => l.id === AEI_LIST);
  if (at === -1) return meta;
  lists.splice(at + 1, 0, { ...lists[at], id: SALES_LIST, label: "Yearling Sales Results" });
  return { lists, columns, rows };
}

function cropGroupFor(listId) {
  return CROP_LIST_GROUPS.find((g) => g.parts.some(([id]) => id === listId)) || null;
}

function combineCropLists(meta) {
  let lists = [...meta.lists];
  const columns = { ...meta.columns };
  const rows = { ...meta.rows };
  for (const group of CROP_LIST_GROUPS) {
    const present = group.parts.filter(([id]) => lists.some((l) => l.id === id));
    if (!present.length) continue;
    const merged = [];
    const cols = [
      { key: "year", label: "Year", type: "number" },
      { key: "crop", label: "Crop", type: "text" },
    ];
    for (const [id, crop] of present) {
      for (const row of rows[id] || []) merged.push({ ...row, crop, crop_num: parseInt(crop, 10) });
      for (const col of columns[id] || []) {
        if (CROP_HIDDEN_KEYS.has(col.key)) continue;
        if (!cols.some((c) => c.key === col.key)) cols.push(col);
      }
    }
    const at = lists.findIndex((l) => l.id === present[0][0]);
    const firstList = lists[at];
    lists = lists.filter((l) => !present.some(([id]) => id === l.id));
    lists.splice(at, 0, { ...firstList, id: group.id, label: group.label });
    columns[group.id] = cols;
    rows[group.id] = merged;
  }
  return { lists, columns, rows };
}

function StallionRankingsPanel({ rankingMeta: rawRankingMeta, rankList: rawRankList, setRankList, summaries = [] }) {
  const rankingMeta = useMemo(() => mergeRegionalLists(rawRankingMeta), [rawRankingMeta]);
  const parentList = regionalParent(rawRankList) && rankingMeta.lists.some((l) => l.id === regionalParent(rawRankList))
    ? regionalParent(rawRankList)
    : rawRankList;
  const groupedList = cropGroupFor(parentList)?.id || parentList;
  const isBook = (id) => (rankingMeta.lists || []).some((l) => l.id === id && l.group === "book");
  const rankList = isBook(groupedList)
    ? ((rankingMeta.lists || []).find((l) => l.group !== "book")?.id || groupedList)
    : groupedList;
  const lists = rankingMeta.lists || [];
  const bloodhorseLists = lists.filter((list) => list.group !== "book");
  // Book ranks (stud fee, mares bred) live in the Stud Fee & Mares Bred panel.
  const bookLists = [];
  const { columns, rows } = useMemo(
    () => shapeRankingTable(
      (rankingMeta.columns && rankingMeta.columns[rankList]) || [
        { key: "year", label: "Year", type: "number" },
        { key: "rank", label: "Rank", type: "number" },
      ],
      (rankingMeta.rows && rankingMeta.rows[rankList]) || [],
      rankList,
    ),
    [rankingMeta, rankList],
  );

  const [sortKey, setSortKey] = useState("year");
  const [sortDir, setSortDir] = useState("desc");

  useEffect(() => {
    setSortKey("year");
    setSortDir("desc");
  }, [rankList]);

  const visible = useMemo(() => {
    const dir = sortDir === "asc" ? 1 : -1;
    const copy = [...rows];
    copy.sort((a, b) => {
      const aNum = a[`${sortKey}_num`] ?? parseMetricNumber(a[sortKey]);
      const bNum = b[`${sortKey}_num`] ?? parseMetricNumber(b[sortKey]);
      // Missing values (—) always sort last, whichever direction.
      const aBlank = a[sortKey] == null || a[sortKey] === "";
      const bBlank = b[sortKey] == null || b[sortKey] === "";
      if (aBlank !== bBlank) return aBlank ? 1 : -1;
      if (aNum != null && bNum != null && aNum !== bNum) return (aNum - bNum) * dir;
      if (aNum != null && bNum == null) return -1;
      if (aNum == null && bNum != null) return 1;
      const as = String(a[sortKey] ?? "");
      const bs = String(b[sortKey] ?? "");
      return as.localeCompare(bs, undefined, { numeric: true }) * dir;
    });
    return copy;
  }, [rows, sortKey, sortDir]);

  function toggleSort(key) {
    if (sortKey === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
      return;
    }
    setSortKey(key);
    setSortDir(key === "year" || key === "rank" ? "desc" : "desc");
  }

  const summary = summaries.find((item) => item.id === rawRankList) || null;
  if (!lists.length && !summaries.length) return null;
  const selected = summary ? summary.id : rankList;

  return (
    <div className="stallion-rankings-panel">
      <h4>STALLION STATISTICS</h4>
      <div className="rankings-toolbar">
        <select
          value={selected}
          onChange={(e) => setRankList(e.target.value)}
          aria-label="Statistics view"
        >
          {summaries.map((item) => (
            <option key={item.id} value={item.id}>{item.label}</option>
          ))}
          {bloodhorseLists.map((list) => (
            <option key={list.id} value={list.id}>{list.label}</option>
          ))}
          {bookLists.length ? (
            <optgroup label="Book">
              {bookLists.map((list) => (
                <option key={list.id} value={list.id}>{list.label}</option>
              ))}
            </optgroup>
          ) : null}
        </select>
      </div>
      {summary ? (
        <div className="stats-summary">
          {summary.note ? <p className="rankings-meta">{summary.note}</p> : null}
          <div className="stats-grid">
            {summary.cards.map((card) => (
              <div className="stat-box" key={`${summary.id}-${card.label}`}>
                <span>{card.label}</span>
                <strong>{card.value}</strong>
              </div>
            ))}
          </div>
        </div>
      ) : (
      <>
      <p className="rankings-meta">
        {rows.length} {rows.length === 1 ? "year" : "years"}
        {sortKey ? ` · sorted by ${columns.find((c) => c.key === sortKey)?.label || sortKey} (${sortDir})` : ""}
      </p>
      <div className="history-wrap rankings-rich-wrap">
        <table className="rankings-rich-table">
          <thead>
            <tr>
              {columns.map((col) => (
                <th key={col.key}>
                  <button
                    type="button"
                    className={`rankings-sort${sortKey === col.key ? " active" : ""}`}
                    onClick={() => toggleSort(col.key)}
                  >
                    {col.label}
                    {sortKey === col.key ? (sortDir === "asc" ? " ↑" : " ↓") : ""}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visible.map((row) => (
              <tr key={`${rankList}-${row.year}-${row.table_name || ""}`}>
                {columns.map((col) => (
                  <td
                    key={col.key}
                    className={col.type === "money" || col.key === "aei" || col.key === "comparable_index" ? "money" : undefined}
                  >
                    {formatRankingCell(row, col)}
                  </td>
                ))}
              </tr>
            ))}
            {!visible.length ? (
              <tr>
                <td colSpan={Math.max(columns.length, 1)} className="rankings-empty">
                  No ranking rows match this filter.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
      </>
      )}
    </div>
  );
}

const BMS_COLUMNS = [
  ["mg1w", "#MG1Ws", "Multiple Grade 1 winners"],
  ["g1w", "#G1Ws", "Grade 1 winners (one G1)"],
  ["gsw", "#GSWs", "All graded stakes winners"],
  ["g2w", "#G2Ws", "Best win a Grade 2"],
  ["g3w", "#G3Ws", "Best win a Grade 3"],
  ["sw", "#SWs", "Ungraded / listed stakes winners"],
];

const SALE_TYPES = [["yearlings", "Yearlings"], ["weanlings", "Weanlings"], ["all", "All ages"]];

/** Which broodmare-sire cross brings the most at auction, by sale type. */
function CrossSalesTable({ name }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [type, setType] = useState("yearlings");
  const [minSold, setMinSold] = useState(2);
  const [openRow, setOpenRow] = useState(null);
  const typeMatch = { yearlings: ["Yearling"], weanlings: ["Weanling"], all: null }[type];
  useEffect(() => {
    setData(null);
    setError("");
    apiGet(`/api/sales/sire/${encodeURIComponent(name)}/summary`).then(setData).catch((e) => setError(e.message));
  }, [name]);
  if (error) return <p className="error">{error}</p>;
  if (!data) return <p className="catalog-empty">Loading sales…</p>;
  if (!data.by_damsire) return <p className="catalog-empty">Sales by cross isn&apos;t available on this server yet.</p>;
  const rows = data.by_damsire
    .filter((r) => (r[type]?.sold || 0) >= minSold && r[type]?.average != null)
    .sort((a, b) => b[type].average - a[type].average);
  return (
    <>
      <h4>SALES BY CROSS · {rows.length}</h4>
      <div className="cross-sales-tools">
        <div className="view-toggle" style={{ display: "flex", gap: 6 }}>
          {SALE_TYPES.map(([id, label]) => (
            <button key={id} type="button" className={type === id ? "primary" : "ghost"} onClick={() => setType(id)}>
              {label}
            </button>
          ))}
        </div>
        <label>
          At least
          <select value={minSold} onChange={(e) => setMinSold(Number(e.target.value))}>
            {[1, 2, 3, 5, 10].map((n) => <option key={n} value={n}>{n} sold</option>)}
          </select>
        </label>
      </div>
      {rows.length ? (
        <div className="history-wrap">
          <table>
            <thead>
              <tr><th>Broodmare Sire</th><th>Average</th><th>Median</th><th>Top price</th><th>Sold</th></tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const lots = (r.lots || [])
                  .filter((l) => !typeMatch || typeMatch.includes(l.type))
                  .sort((a, b) => (b.status === "sold") - (a.status === "sold") || (b.price || 0) - (a.price || 0));
                const isOpen = openRow === r.broodmare_sire;
                return (
                  <Fragment key={r.broodmare_sire}>
                    <tr
                      className={lots.length ? "bms-row" : undefined}
                      onClick={lots.length ? () => setOpenRow(isOpen ? null : r.broodmare_sire) : undefined}
                      aria-expanded={lots.length ? isOpen : undefined}
                    >
                      <td>
                        {lots.length ? <span className="bms-caret">{isOpen ? "▾" : "▸"}</span> : null}
                        {r.broodmare_sire}
                      </td>
                      <td className="money">{money(r[type].average)}</td>
                      <td className="money">{r[type].median != null ? money(r[type].median) : "—"}</td>
                      <td className="money">{r[type].high != null ? money(r[type].high) : "—"}</td>
                      <td>{r[type].sold} of {r[type].offered}</td>
                    </tr>
                    {isOpen ? (
                      <tr className="bms-horses">
                        <td colSpan={5}>
                          {lots.map((l, i) => (
                            <div key={`${l.sale}-${l.hip}-${i}`} className="bms-horse cross-lot">
                              <span className="cross-lot-name">{l.name || "Unnamed"}</span>
                              <span className="bms-horse-meta">
                                {[l.type, l.sex, `${l.sale} ${l.year}`, l.hip ? `Hip ${l.hip}` : null].filter(Boolean).join(" · ")}
                              </span>
                              <span className={`cross-lot-price${l.status === "sold" ? "" : " unsold"}`}>
                                {l.status === "sold" ? money(l.price) : l.status === "not sold" ? `RNA${l.price ? ` ${money(l.price)}` : ""}` : l.status}
                              </span>
                            </div>
                          ))}
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="catalog-empty">No crosses with {minSold}+ sold in this category.</p>
      )}
      <p className="sub" style={{ fontSize: 12, marginTop: 6 }}>
        Sold lots only (RNAs and outs excluded), all years. Lower &quot;at least&quot; to include one-off sales.
      </p>
    </>
  );
}

const CLASS_ORDER = ["MG1W", "G1W", "MGSW", "G2W", "G3W", "MSW", "SW"];

// "Tapit Gr/R", "Tapit (2001)" and "TAPIT" are the same broodmare sire.
const COAT_SUFFIX = /\s+(?:gr\/?ro?|gr\/r|dk\s*b\/?br?|b\/?br|ch|blk|bl|br|b|gr|ro)\.?$/i;
function bmsKey(name) {
  return String(name || "").replace(/\s*\(\d{4}\)\s*$/, "").trim().replace(COAT_SUFFIX, "").trim().toUpperCase();
}

function BmsCrossesTable({ rows, progeny = [], hrefFor }) {
  // Hide columns that are zero for every broodmare sire of this stallion.
  const cols = BMS_COLUMNS.filter(([key]) => rows.some((r) => r[key] > 0));
  const [open, setOpen] = useState(() => new Set());
  // The horses behind each cross come from Top Progeny (same sire, matched on broodmare sire).
  const byBms = useMemo(() => {
    const map = new Map();
    for (const p of progeny) {
      const key = bmsKey(p.broodmare_sire);
      if (!key) continue;
      if (!map.has(key)) map.set(key, []);
      map.get(key).push(p);
    }
    for (const list of map.values()) {
      list.sort((a, b) => (CLASS_ORDER.indexOf(a.class) + 99) % 99 - (CLASS_ORDER.indexOf(b.class) + 99) % 99
        || (Number(b.earnings) || 0) - (Number(a.earnings) || 0));
    }
    return map;
  }, [progeny]);
  const toggle = (key) => setOpen((cur) => {
    const next = new Set(cur);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });
  return (
    <>
      <h4>BEST BROODMARE SIRE CROSSES · {rows.length}</h4>
      <div className="history-wrap">
        <table>
          <thead>
            <tr>
              <th>Broodmare Sire</th>
              {cols.map(([key, label, title]) => <th key={key} title={title}>{label}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const key = bmsKey(r.broodmare_sire);
              const horses = byBms.get(key) || [];
              const isOpen = open.has(key);
              return (
                <Fragment key={r.broodmare_sire}>
                  <tr
                    className={horses.length ? "bms-row" : undefined}
                    onClick={horses.length ? () => toggle(key) : undefined}
                    aria-expanded={horses.length ? isOpen : undefined}
                  >
                    <td>
                      {horses.length ? <span className="bms-caret">{isOpen ? "▾" : "▸"}</span> : null}
                      {r.broodmare_sire}
                    </td>
                    {cols.map(([col]) => <td key={col}>{r[col] || "—"}</td>)}
                  </tr>
                  {isOpen ? (
                    <tr className="bms-horses">
                      <td colSpan={cols.length + 1}>
                        {horses.map((h) => (
                          <div key={h.name} className="bms-horse">
                            <Link href={hrefFor ? hrefFor(h.name) : `/horses/${encodeURIComponent(h.name)}`} onClick={(e) => e.stopPropagation()}>
                              {h.name}
                            </Link>
                            <span className="bms-horse-meta">
                              {h.class || ""}
                              {h.dam ? ` · out of ${String(h.dam).toUpperCase()}` : ""}
                              {h.earnings ? ` · ${money(h.earnings)}` : ""}
                            </span>
                          </div>
                        ))}
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}

// Breeding panel by profile type: suggested stallions for fillies/mares,
// similar active stallions for colts/horses, nothing for geldings.
const BREEDING_TYPES = new Set(["racing_female", "broodmare", "racing_male"]);

function BreedingPanel({ panel, hrefFor }) {
  if (!panel) {
    return (
      <div className="panel panel-wide">
        <h4>STALLIONS</h4>
        <p className="catalog-empty">Loading…</p>
      </div>
    );
  }
  const suggested = panel.kind === "suggested_stallions";
  const rows = panel.rows || [];
  return (
    <div className="panel panel-wide">
      <h4>{suggested ? "SUGGESTED STALLIONS" : "SIMILAR ACTIVE STALLIONS"}</h4>
      {rows.length ? (
        <div className="history-wrap">
          <table>
            <thead>
              <tr>
                <th>Stallion</th>
                <th>Location</th>
                <th>Stud Fee</th>
                <th>{suggested ? "Proven crosses" : "Similarity"}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.name}>
                  <td><Link href={hrefFor(r.name)}>{r.name}</Link></td>
                  <td>{r.location || "—"}</td>
                  <td className="money">{r.stud_fee || "—"}</td>
                  <td>{suggested ? (r.evidence || "—") : (r.similarity != null ? `${r.similarity}%` : "—")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="catalog-empty">
          {suggested
            ? "No standing stallions have proven crosses with this mare's sire line yet."
            : "No active stallions with a comparable pedigree found."}
        </p>
      )}
    </div>
  );
}

function HorsePage() {
  const { name } = useParams();
  const router = useRouter();
  const searchParams = useSearchParams();
  const decoded = decodeURIComponent(name || "");
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [tick, setTick] = useState(0);
  const [rankList, setRankList] = useState("leading_sire");
  const [showAllProgeny, setShowAllProgeny] = useState(false);
  const [breeding, setBreeding] = useState(null);
  const [progenyView, setProgenyView] = useState("top");
  const layout = "tabs";
  const [tab, setTab] = useState("general");
  useEffect(() => { setTab("general"); }, [decoded]);
  const returnTarget = useMemo(() => resolveReturnTarget(searchParams), [searchParams]);
  const salesReturn = returnTarget.sales;
  const hrefFor = useMemo(
    () => (horseName) => (salesReturn ? horseHrefWithReturn(horseName, salesReturn) : horseHref(horseName)),
    [salesReturn],
  );

  useEffect(() => {
    if (salesReturn) rememberSalesVisit(salesReturn, decoded);
  }, [salesReturn, decoded]);

  useEffect(() => {
    setData(null);
    setRankList("leading_sire");
    apiGet(`/api/horses/${encodeURIComponent(decoded)}`)
      .then((payload) => {
        setData(payload);
        const lists = (payload && payload.sire_rankings && payload.sire_rankings.lists) || [];
        const career = payload?.stallion?.progeny_stats?.career;
        const preferred = lists.find((list) => list.id === "leading_sire") || lists[0];
        setRankList(career && Object.keys(career).length ? CAREER_VIEW : (preferred ? preferred.id : "leading_sire"));
      })
      .catch((e) => setError(e.message));
  }, [decoded]);

  const profileType = data?.classification?.profile_type;
  useEffect(() => {
    setBreeding(null);
    if (!BREEDING_TYPES.has(profileType)) return;
    apiGet(`/api/horses/${encodeURIComponent(decoded)}/breeding`)
      .then(setBreeding)
      .catch(() => setBreeding({ kind: profileType === "racing_male" ? "similar_stallions" : "suggested_stallions", rows: [] }));
  }, [decoded, profileType]);

  function goBack() {
    const href = returnTarget?.href;
    if (href) {
      router.push(href);
      return;
    }
    if (typeof window !== "undefined" && window.history.length > 1) {
      router.back();
      return;
    }
    router.push("/stallions");
  }

  const backLabel = returnTarget?.label
    ? `← Go Back · ${returnTarget.label}`
    : "← Go Back";

  if (error) {
    return (
      <div>
        <div className="sales-return-bar">
          <button className="primary" type="button" onClick={goBack}>{backLabel}</button>
          <button className="ghost" type="button" onClick={goBack}>Close</button>
        </div>
        <p className="error">{error}</p>
      </div>
    );
  }
  if (!data) {
    return (
      <div>
        <div className="sales-return-bar">
          <button className="primary" type="button" onClick={goBack}>{backLabel}</button>
          <button className="ghost" type="button" onClick={goBack}>Close</button>
        </div>
        <p className="empty">Loading profile…</p>
      </div>
    );
  }
  if (data.ambiguous) {
    return (
      <div className="panel">
        <div className="sales-return-bar">
          <button className="primary" type="button" onClick={goBack}>{backLabel}</button>
          <button className="ghost" type="button" onClick={goBack}>Close</button>
        </div>
        <h2>Which {data.name}?</h2>
        <p className="sub">Multiple horses named {data.name} are in the pedigree database. Choose a foaling year.</p>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 12 }}>
          {(data.years || []).map((year) => (
            <Link key={year} className="primary" href={hrefFor(`${data.name} (${year})`)}>
              {data.name} ({year})
            </Link>
          ))}
        </div>
      </div>
    );
  }

  const s = data.stallion || {};
  const cls = data.classification || {};
  // Pensioned / deceased from a recorded status change; stud cards themselves always say "active".
  const offDuty = ["deceased", "pensioned"].includes(cls.stallion_status) ? cls.stallion_status : null;
  const studStatus = offDuty || s.status;
  const isStallion = Boolean(data.stallion) || Boolean(cls.is_stallion);
  const isFemale = cls.profile_type === "racing_female" || cls.profile_type === "broodmare";
  const typeLabel = {
    broodmare: "BROODMARE",
    racing_female: cls.sex ? cls.sex.toUpperCase() : "FILLY / MARE",
    racing_male: cls.sex ? cls.sex.toUpperCase() : "COLT / HORSE",
    gelding: "GELDING",
  }[cls.profile_type];
  const sireName = data.pedigree?.sire?.name;
  const damName = data.pedigree?.dam?.name;
  const racing = s.progeny_racing || {};
  const earn = s.progeny_earnings || {};
  const legacy = s.legacy_progeny || {};
  const racingCards = collectStatCards(racing, RACING_STAT_FIELDS);
  const earningsCards = collectStatCards(earn, EARNINGS_STAT_FIELDS);
  const legacyCards = collectStatCards(legacy, LEGACY_STAT_FIELDS);
  const ps = s.progeny_stats || {};
  // Older stallion-stats fields join the career summary when present.
  const statSummaries = progenySummaries(ps, [...racingCards, ...earningsCards, ...legacyCards]);
  const totals = data.race_totals || {};
  const family = data.female_family || [];
  const bmsCountLabel = (data.broodmare_sire || []).find((row) => row.count_label)?.count_label || "Runners";
  const rankingMeta = withBroodmareSireList(data.sire_rankings || {}, data.broodmare_sire || [], bmsCountLabel);
  const RANK_LISTS = rankingMeta.lists || [];
  // Stud fee / mares bred ranks show beside the book figures, not in the rankings list.
  const bookRank = { stud_fee: {}, mares_bred: {} };
  for (const id of Object.keys(bookRank)) {
    for (const row of (rankingMeta.rows || {})[id] || []) bookRank[id][row.year] = row.rank;
  }

  const tabbed = layout === "tabs";
  const TABS = [
    ["general", "General Info", true],
    ["pedigree", "Pedigree", true],
    ["race", "Race Record", true],
    ["studbook", "Stud Book", (data.stud_book || []).length > 0],
    ["stats", "Statistics", Boolean(data.stallion && (RANK_LISTS.length || statSummaries.length))],
    ["stallions", "Stallions", BREEDING_TYPES.has(cls.profile_type)],
    ["sales", "Sales", isStallion],
    ["progeny", "Progeny", isStallion],
  ].filter(([, , has]) => has);
  const activeTab = TABS.some(([id]) => id === tab) ? tab : "general";
  const show = (id) => !tabbed || activeTab === id;

  return (
    <div className={`dash${tabbed ? " dash-tabbed" : ""}`}>
      <div className="sales-return-bar dash-span">
        <button className="primary" type="button" onClick={goBack}>
          {backLabel}
        </button>
        <button className="ghost" type="button" onClick={goBack}>Close</button>
        <button
          className="ghost star-btn"
          type="button"
          title="Star"
          onClick={() => { toggleStar(data.name); saveFavorite(data.name); setTick((n) => n + 1); }}
        >
          {isStarred(data.name) && tick >= 0 ? "★" : "☆"}
        </button>
      </div>
      {tabbed ? (
        <nav className="horse-tabs dash-span" aria-label="Horse sections">
          {TABS.map(([id, label]) => (
            <button
              key={id}
              type="button"
              className={`horse-tab${activeTab === id ? " active" : ""}`}
              aria-current={activeTab === id ? "page" : undefined}
              onClick={() => setTab(id)}
            >
              {label}
            </button>
          ))}
        </nav>
      ) : null}
      {show("pedigree") ? (
      <div className="ped-col">
        <div className="panel">
          <h4>PEDIGREE</h4>
          <PedigreeChart
            tree={data.pedigree}
            showSubject={false}
            hrefFor={hrefFor}
            expandedExtra={(
              <div className="panel family-panel">
                <h4>FEMALE FAMILY</h4>
                <FemaleFamily family={family} hrefFor={hrefFor} />
              </div>
            )}
          />
        </div>
        <div className="panel family-panel" style={{ marginTop: 12 }}>
          <h4>FEMALE FAMILY</h4>
          <FemaleFamily family={family} hrefFor={hrefFor} />
        </div>
      </div>
      ) : null}

      <div className="horse-panels">
        {show("general") ? (
        <div className="panel">
          {data.stallion ? (
            <div className="hero">
              <div className="farm">{s.farm || "PEDIGREE PROFILE"}</div>
              <h1>{data.name.toUpperCase()}</h1>
              <div className="sub">
                Foaled: {s.year_foaled || data.year || "—"} · Entered Stud: {s.entered_stud || "—"}
                {studStatus && studStatus !== "active" ? ` · ${studStatus[0].toUpperCase()}${studStatus.slice(1)}` : ""}
              </div>
              <div className="kpi-row">
                <div className="kpi">
                  <span>Stud Fee</span>
                  <b>{offDuty ? offDuty.toUpperCase() : s.stud_fee || "—"}</b>
                </div>
                {s.rank && String(s.rank).toUpperCase() !== "N/A" ? (
                  <div className="kpi">
                    <span>Sire Rank</span>
                    <b className="muted">#{s.rank}</b>
                  </div>
                ) : null}
              </div>
            </div>
          ) : (
            <div className="hero">
              <div className="farm">{typeLabel || (isStallion ? "STALLION" : "PEDIGREE PROFILE")}</div>
              <h1>{data.name.toUpperCase()}</h1>
              <div className="sub">
                Foaled: {data.year || "—"}
                {sireName ? ` · By ${sireName}` : ""}
                {damName ? ` out of ${damName}` : ""}
              </div>
            </div>
          )}
          {s.career_highlight ? (
            <div className="notes" style={{ marginTop: 16 }}>{s.career_highlight}</div>
          ) : null}
          {(isStallion && !offDuty) || isFemale ? (
            <div style={{ marginTop: 16, textAlign: "center" }}>
              <Link
                className="primary"
                href={isStallion ? `/hypomating?sire=${encodeURIComponent(data.name)}` : `/hypomating?mare=${encodeURIComponent(data.name)}`}
              >
                {isStallion ? "Select for hypomating" : "Use as broodmare"}
              </Link>
            </div>
          ) : null}
        </div>
        ) : null}

        {show("race") ? (
        <div className="panel">
          <h4>RACE RECORD</h4>
          <table>
            <thead>
              <tr>
                <th>Season</th><th>Age</th><th>Starts</th><th>1st</th><th>2nd</th><th>3rd</th><th>Earnings</th>
              </tr>
            </thead>
            <tbody>
              {(data.race_record || []).map((r) => (
                <tr key={r.age}>
                  <td>{r.season}</td>
                  <td>{String(r.age ?? "").match(/\d+/)?.[0] || r.age}</td>
                  <td>{r.starts}</td>
                  <td>{r.firsts}</td>
                  <td>{r.seconds}</td>
                  <td>{r.thirds}</td>
                  <td className="money">{r.earnings_display}</td>
                </tr>
              ))}
              <tr>
                <td>TOTAL</td>
                <td>—</td>
                <td>{totals.starts || 0}</td>
                <td>{totals.firsts || 0}</td>
                <td>{totals.seconds || 0}</td>
                <td>{totals.thirds || 0}</td>
                <td className="money">{money(totals.earnings)}</td>
              </tr>
            </tbody>
          </table>
          {s.major_wins ? <div className="notes" style={{ marginTop: 12 }}>{s.major_wins}</div> : null}
          <RaceList starts={data.race_starts} wins={data.race_wins} />
        </div>
        ) : null}

        {show("studbook") && (data.stud_book || []).length ? (
          <div className="panel panel-wide">
            <h4>STUD FEE & MARES BRED</h4>
          <div className="history-wrap">
            <table className="book-table">
              <thead>
                <tr>
                  <th>Year</th>
                  <th title="Stud fee">Fee</th>
                  <th title="Stud fee rank">Fee Rk</th>
                  <th title="Mares bred">Mares</th>
                  <th title="Mares bred rank">MB Rk</th>
                  <th title="Live foals">Foals</th>
                  <th title="Live foal %">LF %</th>
                </tr>
              </thead>
              <tbody>
                {data.stud_book.map((row) => (
                  <tr key={row.year}>
                    <td>{row.year}</td>
                    <td className="money">{row.stud_fee || "—"}</td>
                    <td>{bookRank.stud_fee[row.year] ? `#${bookRank.stud_fee[row.year]}` : "—"}</td>
                    <td>{row.mares_bred ?? "—"}</td>
                    <td>{bookRank.mares_bred[row.year] ? `#${bookRank.mares_bred[row.year]}` : "—"}</td>
                    <td>{row.live_foals ?? "—"}</td>
                    <td>{row.live_foal_pct || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          </div>
        ) : null}

        {show("stats") && data.stallion && (RANK_LISTS.length || statSummaries.length) ? (
          <div className="panel panel-wide">
            <StallionRankingsPanel
              rankingMeta={rankingMeta}
              rankList={rankList}
              setRankList={setRankList}
              summaries={statSummaries}
            />
          </div>
        ) : null}

        {show("stallions") && BREEDING_TYPES.has(cls.profile_type) ? <BreedingPanel panel={breeding} hrefFor={hrefFor} /> : null}

        {show("sales") && isStallion ? <StallionSalesPanel name={data.name} hrefFor={hrefFor} /> : null}

        {show("progeny") && isStallion ? (
        <div className="panel">
          {isStallion ? (
            <div className="view-toggle" style={{ display: "flex", gap: 8, marginBottom: 10, flexWrap: "wrap" }}>
              <button
                type="button"
                className={progenyView === "top" ? "primary" : "ghost"}
                onClick={() => setProgenyView("top")}
              >
                TOP PROGENY
              </button>
              <button
                type="button"
                className={progenyView === "bms" ? "primary" : "ghost"}
                onClick={() => setProgenyView("bms")}
              >
                BEST BROODMARE SIRE CROSSES
              </button>
              <button
                type="button"
                className={progenyView === "sales" ? "primary" : "ghost"}
                onClick={() => setProgenyView("sales")}
              >
                SALES BY CROSS
              </button>
            </div>
          ) : null}
          {progenyView === "sales" ? (
            <CrossSalesTable name={data.name} />
          ) : progenyView === "bms" && (data.bms_crosses || []).length ? (
            <BmsCrossesTable rows={data.bms_crosses} progeny={data.top_progeny || []} hrefFor={hrefFor} />
          ) : (
          <>
          <h4>TOP PROGENY{(data.top_progeny || []).length ? ` · ${data.top_progeny.length}` : ""}</h4>
          {(data.top_progeny || []).length ? (
            <>
            <table>
              <thead>
                <tr><th>Horse</th><th>Broodmare Sire</th><th>Class</th><th>Earnings</th></tr>
              </thead>
              <tbody>
                {/* Best class first, then earnings (API order). */}
                {(showAllProgeny ? data.top_progeny : data.top_progeny.slice(0, TOP_PROGENY_PREVIEW))
                  .map((p) => (
                  <tr key={p.name}>
                    <td><Link href={hrefFor(p.name)}>{p.name}</Link></td>
                    <td>{p.broodmare_sire}</td>
                    <td>{p.class}</td>
                    <td className="money">{p.earnings != null ? money(p.earnings) : (p.earnings_display || "—")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {data.top_progeny.length > TOP_PROGENY_PREVIEW ? (
              <button className="ghost" type="button" onClick={() => setShowAllProgeny((v) => !v)} style={{ marginTop: 8 }}>
                {showAllProgeny ? "SHOW FEWER" : "SHOW ALL"}
              </button>
            ) : null}
            </>
          ) : (
            <p className="catalog-empty">No top progeny recorded for this stallion yet.</p>
          )}
          </>
          )}
        </div>
        ) : null}

      </div>
    </div>
  );
}

export default function HorsePageSuspense() {
  return (
    <Suspense fallback={<p className="empty">Loading profile…</p>}>
      <HorsePage />
    </Suspense>
  );
}

"use client";

import { useEffect, useRef, useState } from "react";

const STYLES = {
  pen: { stroke: "#ef6b73", width: 2.5, opacity: 1 },
  highlighter: { stroke: "#ffe600", width: 16, opacity: 0.45 },
};
const ERASE_RADIUS = 14;

function load(key) {
  try {
    return JSON.parse(window.localStorage.getItem(key) || "[]");
  } catch {
    return [];
  }
}

function save(key, strokes) {
  if (!key) return;
  try {
    if (strokes.length) window.localStorage.setItem(key, JSON.stringify(strokes));
    else window.localStorage.removeItem(key);
  } catch {
    /* storage full or blocked: drawings just won't persist */
  }
}

function pathOf(points) {
  return points.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`).join(" ");
}

/**
 * Freehand pen / highlighter / eraser layer over a scrolling column.
 * Lives inside the column (position: relative) and spans its full scroll height,
 * so marks move with the content. Strokes persist in localStorage under storageKey.
 *
 * tool: null (off) | "pen" | "highlighter" | "eraser".
 * clearSignal bumps to wipe this layer. command = { target, kind: "undo" | "redo", n }
 * replays history for the layer whose id matches target. onEdit(id) fires after every
 * change (stroke, erase, clear) so the page can route undo/redo to the right layer.
 */
export default function MarkupLayer({ id, storageKey, tool, clearSignal, command, onEdit }) {
  const svgRef = useRef(null);
  const [strokes, setStrokes] = useState([]);
  const strokesRef = useRef([]);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const drawing = useRef(null);
  const past = useRef([]);
  const future = useRef([]);
  const onEditRef = useRef(onEdit);
  onEditRef.current = onEdit;

  const apply = (next) => {
    strokesRef.current = next;
    setStrokes(next);
  };

  // Commit a change: remember the previous state for undo, persist, notify.
  const commit = (prev, next) => {
    past.current.push(prev);
    future.current = [];
    apply(next);
    save(storageKey, next);
    onEditRef.current?.(id);
  };

  useEffect(() => {
    past.current = [];
    future.current = [];
    apply(storageKey ? load(storageKey) : []);
  }, [storageKey]);

  useEffect(() => {
    if (!clearSignal || !strokesRef.current.length) return;
    commit(strokesRef.current, []);
  }, [clearSignal]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!command || command.target !== id) return;
    const from = command.kind === "undo" ? past.current : future.current;
    const to = command.kind === "undo" ? future.current : past.current;
    if (!from.length) return;
    to.push(strokesRef.current);
    const next = from.pop();
    apply(next);
    save(storageKey, next);
  }, [command]); // eslint-disable-line react-hooks/exhaustive-deps

  // Match the column's full scrollable size.
  useEffect(() => {
    const host = svgRef.current?.parentElement;
    if (!host) return undefined;
    const measure = () => setSize({ w: host.scrollWidth, h: host.scrollHeight });
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(host);
    for (const child of host.children) if (child !== svgRef.current) ro.observe(child);
    return () => ro.disconnect();
  }, [storageKey]);

  function point(e) {
    const box = svgRef.current.getBoundingClientRect();
    return [e.clientX - box.left, e.clientY - box.top];
  }

  function eraseAt([x, y]) {
    const cur = strokesRef.current;
    const next = cur.filter((s) => !s.points.some(([px, py]) => Math.hypot(px - x, py - y) < ERASE_RADIUS));
    if (next.length !== cur.length) apply(next);
  }

  function onDown(e) {
    if (!tool) return;
    e.preventDefault();
    svgRef.current.setPointerCapture(e.pointerId);
    const before = strokesRef.current;
    const p = point(e);
    if (tool === "eraser") {
      drawing.current = { eraser: true, before };
      eraseAt(p);
      return;
    }
    drawing.current = { before, stroke: { tool, points: [p] } };
    apply([...before, drawing.current.stroke]);
  }

  function onMove(e) {
    const d = drawing.current;
    if (!d) return;
    const p = point(e);
    if (d.eraser) {
      eraseAt(p);
      return;
    }
    d.stroke = { ...d.stroke, points: [...d.stroke.points, p] };
    apply([...d.before, d.stroke]);
  }

  function onUp() {
    const d = drawing.current;
    drawing.current = null;
    if (!d) return;
    if (d.eraser && strokesRef.current.length === d.before.length) return; // nothing erased
    commit(d.before, strokesRef.current);
  }

  return (
    <svg
      ref={svgRef}
      className={`markup-layer${tool ? ` active tool-${tool}` : ""}`}
      width={size.w}
      height={size.h}
      onPointerDown={onDown}
      onPointerMove={onMove}
      onPointerUp={onUp}
      onPointerCancel={onUp}
    >
      {strokes.map((s, i) => {
        const st = STYLES[s.tool] || STYLES.pen;
        return (
          <path
            key={i}
            d={pathOf(s.points)}
            fill="none"
            stroke={st.stroke}
            strokeWidth={st.width}
            strokeOpacity={st.opacity}
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        );
      })}
    </svg>
  );
}

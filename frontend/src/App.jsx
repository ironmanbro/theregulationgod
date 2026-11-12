import { useEffect, useMemo, useRef, useState } from "react";

const API = import.meta.env.VITE_API_URL || "http://localhost:8000";
const ROUTES = ["ALL", "FDI", "FPI", "FVCI"];
const REGULATORS = [
  ["RBI", "Banking / FEMA"], ["SEBI", "Capital Markets"], ["DPIIT", "FDI Policy"], ["DEA", "Finance Ministry"],
  ["MCA", "Corporate Law"], ["CCI", "Competition"], ["CBDT", "Direct Tax"], ["IFSCA", "GIFT IFSC"],
  ["NSDL", "Depository"], ["CDSL", "Depository"], ["NSE", "Exchange"], ["BSE", "Exchange"],
  ["IRDAI", "Insurance"], ["PFRDA", "Pensions"], ["TRAI", "Telecom"], ["DoT", "Telecom"],
  ["MIB", "Broadcasting"], ["CDSCO", "Pharma"], ["DGCA", "Aviation"], ["PNGRB", "Oil & Gas"],
  ["CERC", "Power"], ["FSSAI", "Food"], ["AAI", "Airports"], ["AERB", "Nuclear"], ["NABARD", "Agri Finance"],
];
const IMPACT_STYLE = {
  CRITICAL: "bg-red-500/15 text-red-400 border-red-500/40",
  HIGH: "bg-orange-500/15 text-orange-400 border-orange-500/40",
  MEDIUM: "bg-amber-500/15 text-amber-300 border-amber-500/40",
  LOW: "bg-emerald-500/15 text-emerald-400 border-emerald-500/40",
};
const ROUTE_STYLE = {
  FDI: "text-sky-400 border-sky-500/40", FPI: "text-violet-400 border-violet-500/40",
  FVCI: "text-pink-400 border-pink-500/40", GENERAL: "text-zinc-400 border-zinc-600",
};
async function patchItem(dealId, id, changes) {
  const r = await fetch(`${API}/api/deals/${dealId}/checklist/${id}`, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(changes),
  });
  if (!r.ok) throw new Error(r.status);
  return r.json();
}

export default function App() {
  const [route, setRoute] = useState("ALL");
  const [selected, setSelected] = useState(new Set());
  const [search, setSearch] = useState("");
  const [debounced, setDebounced] = useState("");
  const [mode, setMode] = useState("keyword");
  const [rows, setRows] = useState([]);
  const [total, setTotal] = useState(0);
  const [status, setStatus] = useState("idle");
  const [checklist, setChecklist] = useState([]);
  const [clError, setClError] = useState("");
  const [deals, setDeals] = useState([]);
  const [dealId, setDealId] = useState(null);
  const [newDeal, setNewDeal] = useState("");
  const [editingNote, setEditingNote] = useState(null); // { id, text }
  const noteCancelled = useRef(false);

  useEffect(() => { const t = setTimeout(() => setDebounced(search.trim()), 300); return () => clearTimeout(t); }, [search]);

  useEffect(() => {
    const ctrl = new AbortController();
    const semantic = mode === "semantic" && debounced.length >= 2;
    let url;
    if (semantic) {
      const p = new URLSearchParams({ q: debounced, route, limit: "50" });
      if (selected.size) p.set("regulators", [...selected].join(","));
      url = `${API}/api/regulations/semantic-search?${p}`;
    } else {
      const p = new URLSearchParams({ route, limit: "200" });
      if (selected.size) p.set("regulators", [...selected].join(","));
      if (debounced) p.set("search", debounced);
      url = `${API}/api/regulations?${p}`;
    }
    setStatus("loading");
    fetch(url, { signal: ctrl.signal })
      .then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then((d) => {
        const items = semantic ? d : d.items;
        setRows(items); setTotal(semantic ? items.length : d.total); setStatus("live");
      })
      .catch((e) => { if (e.name !== "AbortError") setStatus("error"); });
    return () => ctrl.abort();
  }, [route, selected, debounced, mode]);

  useEffect(() => {
    fetch(`${API}/api/deals`)
      .then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then((d) => { setDeals(d); setDealId((cur) => cur ?? d[0]?.id ?? null); })
      .catch(() => setClError("Deals unavailable — backend unreachable"));
  }, []);

  const [sectors, setSectors] = useState([]);
  useEffect(() => {
    fetch(`${API}/api/sectors`).then((r) => (r.ok ? r.json() : [])).then(setSectors).catch(() => {});
  }, []);

  const currentDeal = deals.find((d) => d.id === dealId);
  const dealRoute = currentDeal?.route;
  const dealSectors = currentDeal?.sectors ?? null; // null = all regulators
  const sectorsKey = dealSectors === null ? "ALL" : dealSectors.join(",");
  const toggleSector = (s) => {
    const cur = dealSectors ?? [];
    updateDeal({ sectors: cur.includes(s) ? cur.filter((x) => x !== s) : [...cur, s] });
  };

  useEffect(() => {
    if (!dealId) { setChecklist([]); return; }
    const ctrl = new AbortController();
    fetch(`${API}/api/deals/${dealId}/checklist`, { signal: ctrl.signal })
      .then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then((items) => { setChecklist(items); setClError(""); })
      .catch((e) => { if (e.name !== "AbortError") setClError("Checklist unavailable"); });
    return () => ctrl.abort();
  }, [dealId, dealRoute, sectorsKey]);

  const createDeal = async (e) => {
    e.preventDefault();
    const name = newDeal.trim();
    if (!name) return;
    try {
      const r = await fetch(`${API}/api/deals`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, route }),
      });
      if (r.status === 409) { setClError("A deal with that name already exists"); return; }
      if (!r.ok) throw new Error(r.status);
      const d = await r.json();
      setDeals((ds) => [d, ...ds]); setDealId(d.id); setNewDeal(""); setClError("");
    } catch { setClError("Could not create deal"); }
  };

  const updateDeal = async (changes) => {
    try {
      const r = await fetch(`${API}/api/deals/${dealId}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(changes),
      });
      if (r.status === 409) { setClError("A deal with that name already exists"); return; }
      if (!r.ok) throw new Error(r.status);
      const updated = await r.json();
      setDeals((ds) => ds.map((d) => (d.id === updated.id ? updated : d))); setClError("");
    } catch { setClError("Could not update deal"); }
  };

  const renameDeal = () => {
    const deal = deals.find((d) => d.id === dealId);
    if (!deal) return;
    const name = window.prompt("Rename deal", deal.name)?.trim();
    if (name && name !== deal.name) updateDeal({ name });
  };

  const deleteDeal = async () => {
    const deal = deals.find((d) => d.id === dealId);
    if (!deal || !window.confirm(`Delete deal "${deal.name}" and all its checklist progress?`)) return;
    try {
      const r = await fetch(`${API}/api/deals/${deal.id}`, { method: "DELETE" });
      if (!r.ok && r.status !== 404) throw new Error(r.status);
      const rest = deals.filter((d) => d.id !== deal.id);
      setDeals(rest); setDealId(rest[0]?.id ?? null); setClError("");
    } catch { setClError("Could not delete deal"); }
  };

  // Optimistic update; rolls back the changed items if the API call fails.
  const setComplete = async (ids, value) => {
    const prev = checklist;
    setChecklist((cl) => cl.map((i) => (ids.includes(i.id) ? { ...i, is_complete: value } : i)));
    try {
      const saved = await Promise.all(ids.map((id) => patchItem(dealId, id, { is_complete: value })));
      const byId = Object.fromEntries(saved.map((s) => [s.id, s]));
      setChecklist((cl) => cl.map((i) => byId[i.id] || i));
      setClError("");
    } catch {
      setChecklist(prev);
      setClError("Save failed — change reverted");
    }
  };

  const saveNote = async () => {
    if (noteCancelled.current) { noteCancelled.current = false; return; }
    if (!editingNote) return;
    const { id, text } = editingNote;
    setEditingNote(null);
    const item = checklist.find((i) => i.id === id);
    if (!item || (item.notes || "") === text.trim()) return;
    try {
      const saved = await patchItem(dealId, id, { notes: text });
      setChecklist((cl) => cl.map((i) => (i.id === id ? saved : i))); setClError("");
    } catch { setClError("Note not saved"); }
  };

  const toggleReg = (code) => setSelected((s) => { const n = new Set(s); n.has(code) ? n.delete(code) : n.add(code); return n; });
  const completed = useMemo(() => checklist.filter((i) => i.is_complete).length, [checklist]);
  const pct = checklist.length ? Math.round((completed / checklist.length) * 100) : 0;

  return (
    <div className="min-h-screen bg-[#0a0d12] text-zinc-200 font-sans">
      <header className="border-b border-zinc-800 bg-[#0d1117] px-6 py-3 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className="h-2.5 w-2.5 rounded-full bg-emerald-400 animate-pulse" />
          <h1 className="text-sm font-semibold tracking-[0.2em] uppercase">India Capital RegHub</h1>
          <span className="text-[10px] font-mono text-zinc-500">FDI · FPI · FVCI COMPLIANCE TERMINAL</span>
        </div>
        <div className="font-mono text-xs text-zinc-400">
          FEED: <span className={status === "live" ? "text-emerald-400" : status === "error" ? "text-red-400" : "text-amber-300"}>
            {status.toUpperCase()}</span> · {total} RECORDS
        </div>
      </header>

      {/* Global route filter */}
      <div className="px-6 py-3 border-b border-zinc-800 flex flex-wrap gap-3 items-center">
        <div className="flex rounded border border-zinc-700 overflow-hidden">
          {ROUTES.map((r) => (
            <button key={r} onClick={() => setRoute(r)}
              className={`px-4 py-1.5 font-mono text-xs tracking-wider transition ${route === r ? "bg-emerald-500 text-black font-bold" : "hover:bg-zinc-800 text-zinc-400"}`}>
              {r}
            </button>
          ))}
        </div>
        <div className="flex rounded border border-zinc-700 overflow-hidden">
          {["keyword", "semantic"].map((m) => (
            <button key={m} onClick={() => setMode(m)}
              className={`px-3 py-1.5 font-mono text-xs tracking-wider uppercase transition ${mode === m ? "bg-sky-500 text-black font-bold" : "hover:bg-zinc-800 text-zinc-400"}`}>
              {m}
            </button>
          ))}
        </div>
        <input value={search} onChange={(e) => setSearch(e.target.value)}
          placeholder={mode === "semantic" ? "DESCRIBE: e.g. pricing rules for share transfers to non-residents…" : "SEARCH: reference no, title, keyword…"}
          className="flex-1 min-w-[220px] bg-[#0d1117] border border-zinc-700 rounded px-3 py-1.5 font-mono text-xs focus:outline-none focus:border-emerald-500" />
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-[280px_1fr_320px] gap-px bg-zinc-800">
        {/* Regulators grid */}
        <aside className="bg-[#0a0d12] p-4">
          <div className="flex justify-between items-center mb-3">
            <h2 className="text-[11px] font-semibold uppercase tracking-widest text-zinc-400">Regulators ({selected.size || "ALL"})</h2>
            {selected.size > 0 && <button onClick={() => setSelected(new Set())} className="text-[10px] font-mono text-emerald-400 hover:underline">CLEAR</button>}
          </div>
          <div className="grid grid-cols-3 xl:grid-cols-2 gap-1.5">
            {REGULATORS.map(([code, sector]) => (
              <button key={code} onClick={() => toggleReg(code)} title={sector}
                className={`text-left px-2 py-1.5 rounded border font-mono text-[11px] transition ${selected.has(code) ? "border-emerald-500 bg-emerald-500/10 text-emerald-300" : "border-zinc-800 hover:border-zinc-600 text-zinc-300"}`}>
                <div className="font-bold">{code}</div>
                <div className="text-[9px] text-zinc-500 truncate">{sector}</div>
              </button>
            ))}
          </div>
        </aside>

        {/* Live table */}
        <main className="bg-[#0a0d12] overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-[#0d1117] text-[10px] uppercase tracking-wider text-zinc-500">
              <tr>{["Regulator", "Route", "Ref No.", "Title / Summary", "Date", "Impact", "Src"].map((h) =>
                <th key={h} className="text-left px-3 py-2 font-medium border-b border-zinc-800">{h}</th>)}</tr>
            </thead>
            <tbody>
              {status === "error" && <tr><td colSpan={7} className="p-6 text-center font-mono text-red-400">Backend unreachable at {API}</td></tr>}
              {status === "live" && rows.length === 0 && <tr><td colSpan={7} className="p-6 text-center font-mono text-zinc-500">No regulations match filters</td></tr>}
              {rows.map((r) => (
                <tr key={r.id} className="border-b border-zinc-900 hover:bg-zinc-900/60 align-top">
                  <td className="px-3 py-2"><span className="px-1.5 py-0.5 rounded bg-zinc-800 font-mono font-bold text-[10px] text-zinc-100">{r.regulator}</span></td>
                  <td className="px-3 py-2 space-x-1 whitespace-nowrap">{r.routes.map((t) =>
                    <span key={t} className={`px-1 py-0.5 border rounded font-mono text-[9px] ${ROUTE_STYLE[t] || ROUTE_STYLE.GENERAL}`}>{t}</span>)}</td>
                  <td className="px-3 py-2 font-mono text-[10px] text-zinc-400 max-w-[160px] break-all">{r.reference_no}</td>
                  <td className="px-3 py-2 max-w-[520px]">
                    <div className="text-zinc-100 font-medium leading-snug">{r.title}</div>
                    {r.summary && <div className="text-zinc-500 mt-0.5 line-clamp-2">{r.summary}</div>}
                  </td>
                  <td className="px-3 py-2 font-mono text-[11px] text-zinc-400 whitespace-nowrap">{r.issue_date}</td>
                  <td className="px-3 py-2"><span className={`px-1.5 py-0.5 border rounded font-mono text-[9px] ${IMPACT_STYLE[r.impact]}`}>{r.impact}</span></td>
                  <td className="px-3 py-2 font-mono text-[10px] whitespace-nowrap">
                    <a href={r.source_url} target="_blank" rel="noreferrer" className="text-emerald-400 hover:underline">LINK</a>
                    {r.pdf_url && <> · <a href={r.pdf_url} target="_blank" rel="noreferrer" className="text-sky-400 hover:underline">PDF</a></>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </main>

        {/* Due diligence checklist */}
        <aside className="bg-[#0a0d12] p-4">
          <h2 className="text-[11px] font-semibold uppercase tracking-widest text-zinc-400">Due Diligence Tracker</h2>
          <div className="mt-3 flex gap-1">
          <select value={dealId ?? ""} onChange={(e) => setDealId(Number(e.target.value) || null)}
            className="flex-1 min-w-0 bg-[#0d1117] border border-zinc-700 rounded px-2 py-1.5 font-mono text-xs focus:outline-none focus:border-emerald-500">
            {deals.length === 0 && <option value="">— no deals yet —</option>}
            {deals.map((d) => <option key={d.id} value={d.id}>{d.name} [{d.route}]</option>)}
          </select>
          <button onClick={renameDeal} disabled={!dealId} title="Rename deal"
            className="px-2 border border-zinc-700 rounded font-mono text-[10px] text-zinc-400 hover:border-sky-500 hover:text-sky-400 disabled:opacity-40 disabled:pointer-events-none">REN</button>
          <button onClick={deleteDeal} disabled={!dealId} title="Delete deal"
            className="px-2 border border-zinc-700 rounded font-mono text-[10px] text-zinc-400 hover:border-red-500 hover:text-red-400 disabled:opacity-40 disabled:pointer-events-none">DEL</button>
          </div>
          {dealId && (
            <div className="mt-2 flex rounded border border-zinc-700 overflow-hidden">
              {["GENERAL", "FDI", "FPI", "FVCI"].map((r) => (
                <button key={r} onClick={() => r !== dealRoute && updateDeal({ route: r })}
                  className={`flex-1 py-1 font-mono text-[10px] transition ${dealRoute === r ? "bg-emerald-500 text-black font-bold" : "hover:bg-zinc-800 text-zinc-400"}`}>
                  {r}
                </button>
              ))}
            </div>
          )}
          {dealId && sectors.length > 0 && (
            <div className="mt-2">
              <div className="mb-1 font-mono text-[9px] uppercase tracking-wider text-zinc-500">
                Sectors · {dealSectors === null ? "all regulators" : dealSectors.length ? "core + selected" : "core only"}
              </div>
              <div className="flex flex-wrap gap-1">
                <button onClick={() => dealSectors !== null && updateDeal({ sectors: null })}
                  className={`px-1.5 py-0.5 border rounded font-mono text-[9px] ${dealSectors === null ? "border-emerald-500 bg-emerald-500/10 text-emerald-300" : "border-zinc-700 text-zinc-400 hover:border-zinc-500"}`}>
                  ALL
                </button>
                {sectors.map((s) => {
                  const on = dealSectors?.includes(s.sector);
                  return (
                    <button key={s.sector} onClick={() => toggleSector(s.sector)} title={s.regulators.join(", ")}
                      className={`px-1.5 py-0.5 border rounded font-mono text-[9px] ${on ? "border-sky-500 bg-sky-500/10 text-sky-300" : "border-zinc-700 text-zinc-400 hover:border-zinc-500"}`}>
                      {s.label}
                    </button>
                  );
                })}
              </div>
            </div>
          )}
          <form onSubmit={createDeal} className="mt-2 flex gap-1">
            <input value={newDeal} onChange={(e) => setNewDeal(e.target.value)} placeholder={`NEW DEAL (${route === "ALL" ? "GENERAL" : route})`}
              className="flex-1 min-w-0 bg-[#0d1117] border border-zinc-700 rounded px-2 py-1 font-mono text-[11px] focus:outline-none focus:border-emerald-500" />
            <button type="submit" className="px-2 border border-emerald-600 rounded font-mono text-[10px] text-emerald-400 hover:bg-emerald-500/10">ADD</button>
          </form>
          <div className="mt-3 mb-1 flex justify-between font-mono text-xs">
            <span className="text-zinc-400">{completed}/{checklist.length} CLEARED</span>
            <span className="text-emerald-400 font-bold">{pct}%</span>
          </div>
          <div className="h-1.5 bg-zinc-800 rounded overflow-hidden mb-4">
            <div className="h-full bg-gradient-to-r from-emerald-600 to-emerald-400 transition-all" style={{ width: `${pct}%` }} />
          </div>
          {clError && <div className="mb-2 font-mono text-[10px] text-red-400">{clError}</div>}
          <ul className="space-y-1 max-h-[70vh] overflow-y-auto pr-1">
            {checklist.map((i) => (
              <li key={i.id} className="group rounded hover:bg-zinc-900">
                <div className="flex items-start">
                <label className={`flex-1 flex gap-2 items-start p-1.5 cursor-pointer ${i.is_complete ? "opacity-60" : ""}`}>
                  <input type="checkbox" checked={i.is_complete} onChange={() => setComplete([i.id], !i.is_complete)}
                    className="mt-0.5 accent-emerald-500" />
                  <span className="text-[11px] leading-tight">
                    <span className="font-mono font-bold text-zinc-100 mr-1">{i.regulator}</span>
                    {i.route !== "GENERAL" && (
                      <span className={`mr-1 px-1 border rounded font-mono text-[9px] ${ROUTE_STYLE[i.route]}`}>{i.route}</span>
                    )}
                    <span className={i.is_complete ? "line-through text-zinc-500" : "text-zinc-400"}>{i.item}</span>
                  </span>
                </label>
                <button onClick={() => setEditingNote({ id: i.id, text: i.notes || "" })} title={i.notes ? "Edit note" : "Add note"}
                  className={`p-1.5 font-mono text-[10px] ${i.notes ? "text-sky-400" : "text-zinc-600 opacity-0 group-hover:opacity-100 focus:opacity-100"} hover:text-sky-300`}>
                  NOTE
                </button>
                </div>
                {editingNote?.id === i.id ? (
                  <textarea autoFocus value={editingNote.text} maxLength={2000} rows={3}
                    onChange={(e) => setEditingNote({ id: i.id, text: e.target.value })}
                    onBlur={saveNote}
                    onKeyDown={(e) => { if (e.key === "Escape") { noteCancelled.current = true; setEditingNote(null); } }}
                    placeholder="Add a note… (saves when you click away; Esc cancels)"
                    className="mx-1.5 mb-1.5 w-[calc(100%-0.75rem)] bg-[#0d1117] border border-zinc-700 rounded px-2 py-1 text-[11px] focus:outline-none focus:border-sky-500" />
                ) : i.notes && (
                  <div className="px-1.5 pb-1.5 pl-7 text-[10px] text-zinc-500 whitespace-pre-wrap break-words">{i.notes}</div>
                )}
              </li>
            ))}
          </ul>
          <button onClick={() => setComplete(checklist.filter((i) => i.is_complete).map((i) => i.id), false)} className="mt-3 w-full border border-zinc-700 rounded py-1 font-mono text-[10px] text-zinc-400 hover:border-red-500 hover:text-red-400">RESET</button>
        </aside>
      </div>
    </div>
  );
}

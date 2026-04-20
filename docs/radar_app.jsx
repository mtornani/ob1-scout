// OB1 Radar — main app
const { useState, useEffect, useMemo, useRef } = React;

const TWEAKS_DEFAULTS = /*EDITMODE-BEGIN*/{
  "ghostIntensity": 1.0,
  "density": "comfortable",
  "showSweep": true,
  "sortBy": "score"
}/*EDITMODE-END*/;

function Shell() {
  const [data, setData] = useState([]);
  const [query, setQuery] = useState("");
  const [region, setRegion] = useState(null);
  const [ghostOnly, setGhostOnly] = useState(false);
  const [selectedId, setSelectedId] = useState(null);
  const [sortBy, setSortBy] = useState(TWEAKS_DEFAULTS.sortBy);
  const [tweaksOpen, setTweaksOpen] = useState(false);
  const [tweaks, setTweaks] = useState(TWEAKS_DEFAULTS);

  // load data
  useEffect(() => {
    fetch(`data/anomalies.json?t=${Date.now()}`)
      .then(r => r.json())
      .then(d => {
        const enriched = d.map(a => ({
          ...a,
          history: parseHistory(a.score_history),
          narrative: cleanNarrative(a.raw_content),
          stats: parseStats(a.stats_summary)
        }));
        setData(enriched);
        if (enriched.length && selectedId == null) setSelectedId(enriched[0].id);
      })
      .catch(() => setData([]));
  }, []);

  // tweaks edit mode
  useEffect(() => {
    const handler = (e) => {
      if (e.data?.type === "__activate_edit_mode") setTweaksOpen(true);
      if (e.data?.type === "__deactivate_edit_mode") setTweaksOpen(false);
    };
    window.addEventListener("message", handler);
    window.parent.postMessage({ type: "__edit_mode_available" }, "*");
    return () => window.removeEventListener("message", handler);
  }, []);

  const updateTweak = (key, val) => {
    setTweaks(t => {
      const next = { ...t, [key]: val };
      window.parent.postMessage({ type: "__edit_mode_set_keys", edits: { [key]: val } }, "*");
      return next;
    });
  };

  // regions
  const regions = useMemo(() => {
    const map = new Map();
    data.forEach(a => {
      const r = a.region || "UNASSIGNED";
      map.set(r, (map.get(r) || 0) + 1);
    });
    return [...map.entries()].sort((a, b) => b[1] - a[1]);
  }, [data]);

  // filtered
  const filtered = useMemo(() => {
    let out = data;
    if (query) {
      const q = query.toLowerCase();
      out = out.filter(a => [a.player_name, a.club, a.league, a.region, a.position].filter(Boolean).join(" ").toLowerCase().includes(q));
    }
    if (region) out = out.filter(a => (a.region || "UNASSIGNED") === region);
    if (ghostOnly) out = out.filter(a => a.is_ghost);
    out = [...out].sort((a, b) => {
      if (sortBy === "lead") return (b.lead_time_days || 0) - (a.lead_time_days || 0);
      if (sortBy === "recent") return new Date(b.detection_date) - new Date(a.detection_date);
      return b.score - a.score;
    });
    return out;
  }, [data, query, region, ghostOnly, sortBy]);

  // stats
  const stats = useMemo(() => {
    if (!filtered.length) return { count: 0, maxScore: 0, avgLead: null };
    const max = Math.max(...filtered.map(a => a.score));
    const leads = filtered.filter(a => a.lead_time_days && a.lead_time_days > 0).map(a => a.lead_time_days);
    const avg = leads.length ? Math.round(leads.reduce((s, v) => s + v, 0) / leads.length) : null;
    return { count: filtered.length, maxScore: Math.round(max), avgLead: avg, ghosts: filtered.filter(a => a.is_ghost).length };
  }, [filtered]);

  // kpi sparklines (fake time series derived from filtered history)
  const kpiSparks = useMemo(() => {
    const all = filtered.flatMap(a => a.history);
    if (all.length < 4) return { anomalies: [], max: [], lead: [] };
    // bucket by day
    const byDay = new Map();
    all.forEach(h => {
      const d = h.date?.slice(0, 10);
      if (!d) return;
      if (!byDay.has(d)) byDay.set(d, []);
      byDay.get(d).push(h.score);
    });
    const days = [...byDay.keys()].sort();
    const anomalies = days.map(d => byDay.get(d).length);
    const max = days.map(d => Math.max(...byDay.get(d)));
    const avg = days.map(d => byDay.get(d).reduce((s, v) => s + v, 0) / byDay.get(d).length);
    return { anomalies, max, lead: avg };
  }, [filtered]);

  const selected = filtered.find(a => a.id === selectedId) || filtered[0];

  const now = new Date();
  const nowStr = `${String(now.getFullYear()).slice(2)}${String(now.getMonth()+1).padStart(2,"0")}${String(now.getDate()).padStart(2,"0")}.${String(now.getHours()).padStart(2,"0")}${String(now.getMinutes()).padStart(2,"0")}Z`;

  return (
    <div className="shell" data-screen-label="01 Radar Console">
      {/* =============== TOPBAR =============== */}
      <header className="topbar">
        <div className="brand">
          <OuroGlyph />
          OB1 <span className="brand-sep">//</span> GLOBAL <span className="brand-sub">RADAR</span>
        </div>
        <div style={{ color: "var(--text-lo)", letterSpacing: "0.2em" }}>
          OUROBOROS PROTOCOL · v0.9.0 · COGNITIVE
        </div>
        <div className="topbar-meta">
          <div className="topbar-meta-item"><span>UTC</span><b>{nowStr}</b></div>
          <div className="topbar-meta-item"><span>SIGNALS</span><b>{data.length}</b></div>
          <div className="topbar-meta-item"><span>STREAM</span><b style={{ color: "var(--sig-primary)" }}>LIVE</b><span className="live-dot"/></div>
        </div>
      </header>

      {/* =============== LEFT RAIL =============== */}
      <aside className="rail rail-left">
        <div className="rail-section">
          <div className="rail-h">Search Intercept</div>
          <input
            className="filter-search"
            placeholder=">_ player / club / league"
            value={query}
            onChange={e => setQuery(e.target.value)}
          />
        </div>

        <div className="rail-section">
          <div className="rail-h">Signal Type</div>
          <div className="chip-row">
            <button className={`chip ghost ${ghostOnly ? "active" : ""}`} onClick={() => setGhostOnly(g => !g)}>
              ◎ Ghost Protocol
            </button>
          </div>
        </div>

        <div className="rail-section">
          <div className="rail-h">Sectors</div>
          <div className="region-list">
            <div className={`region-row ${!region ? "active" : ""}`} onClick={() => setRegion(null)}>
              <span>ALL SECTORS</span><b>{data.length}</b>
              <div className="region-bar"><div className="region-bar-fill" style={{ width: "100%" }}/></div>
            </div>
            {regions.map(([r, n]) => (
              <div key={r} className={`region-row ${region === r ? "active" : ""}`} onClick={() => setRegion(region === r ? null : r)}>
                <span>{r.toUpperCase()}</span><b>{n}</b>
                <div className="region-bar"><div className="region-bar-fill" style={{ width: `${(n / data.length) * 100}%` }}/></div>
              </div>
            ))}
          </div>
        </div>

        <div className="rail-section">
          <div className="rail-h">Detection Log</div>
          <div className="log-list">
            {data.slice(0, 8).sort((a, b) => new Date(b.detection_date) - new Date(a.detection_date)).map(a => (
              <div key={a.id} className={`log-row ${a.is_ghost ? "ghost" : ""}`} onClick={() => setSelectedId(a.id)}>
                <span className="log-ts">{fmtTime(a.detection_date)}</span>
                <span className="log-name">{a.player_name.slice(0, 18)}{a.player_name.length > 18 ? "…" : ""}</span>
              </div>
            ))}
          </div>
        </div>
      </aside>

      {/* =============== CENTER =============== */}
      <main className="center">
        {/* KPI INSTRUMENT CLUSTER */}
        <KpiCluster stats={stats} sparks={kpiSparks} />

        {/* Feed bar */}
        <div className="feed-bar">
          <h3>◎ Priority Signals</h3>
          <span className="feed-bar-count">{filtered.length.toString().padStart(3, "0")}</span>
          <span style={{ color: "var(--text-lo)" }}>· SORTED BY</span>
          <div className="feed-bar-sort">
            <button className={`sort-chip ${sortBy === "score" ? "active" : ""}`} onClick={() => setSortBy("score")}>ASYMMETRY</button>
            <button className={`sort-chip ${sortBy === "lead" ? "active" : ""}`} onClick={() => setSortBy("lead")}>LEAD TIME</button>
            <button className={`sort-chip ${sortBy === "recent" ? "active" : ""}`} onClick={() => setSortBy("recent")}>RECENT</button>
          </div>
        </div>

        {/* Signal list */}
        <div className="signal-list">
          {filtered.map(a => (
            <SignalCard
              key={a.id}
              a={a}
              ghostIntensity={tweaks.ghostIntensity}
              selected={a.id === selectedId}
              onSelect={() => setSelectedId(a.id)}
            />
          ))}
          {!filtered.length && (
            <div style={{ padding: "60px 0", textAlign: "center", color: "var(--text-lo)", fontFamily: "var(--font-mono)", fontSize: 11, letterSpacing: "0.15em", textTransform: "uppercase" }}>
              — NO SIGNAL INTERCEPTED —
            </div>
          )}
        </div>
      </main>

      {/* =============== RIGHT RAIL (DOSSIER) =============== */}
      <aside className="rail rail-right">
        {selected ? <Dossier a={selected} /> : (
          <div className="dossier-empty">
            <div className="pulse-dot"/>
            Awaiting target
          </div>
        )}
      </aside>

      {tweaksOpen && (
        <TweaksPanel
          tweaks={tweaks}
          onChange={updateTweak}
          onClose={() => setTweaksOpen(false)}
        />
      )}
    </div>
  );
}

/* =============== KPI CLUSTER =============== */
function KpiCluster({ stats, sparks }) {
  return (
    <section className="kpi-cluster" data-om-validate="kpi">
      <div className="kpi kpi-anomalies">
        <div className="kpi-label">
          <span>◎ Active Anomalies</span>
          <span className="kpi-label-id">KPI-01</span>
        </div>
        <div className="kpi-value">
          <span className="kpi-value-num">{String(stats.count).padStart(2, "0")}</span>
        </div>
        <div className="kpi-sub">
          <Sparkline data={sparks.anomalies.length ? sparks.anomalies : [1,2,3,2,4,5,4,6]} width={72} height={12} color="#ff2a3f" fillOpacity={0.15}/>
          <span>{stats.ghosts || 0} GHOST · {Math.max(0, (stats.count || 0) - (stats.ghosts || 0))} TRACKED</span>
        </div>
      </div>

      <div className="kpi kpi-max">
        <div className="kpi-label">
          <span className="kpi-label-id">KPI-02</span>
          <span>◉ Max Asymmetry</span>
          <span className="kpi-label-id">/100</span>
        </div>
        <div className="kpi-value">
          <span className="kpi-value-num">{String(stats.maxScore || 0).padStart(2, "0")}</span>
        </div>
        <div className="kpi-sub">
          <Sparkline data={sparks.max.length ? sparks.max : [78,82,90,85,95,100,97,100]} width={72} height={12} color="#b8fff0" fillOpacity={0.12}/>
          <span>PEAK GHOST SIGNAL</span>
        </div>
      </div>

      <div className="kpi kpi-lead">
        <div className="kpi-label">
          <span className="kpi-label-id">KPI-03</span>
          <span>◈ Avg Lead Time</span>
        </div>
        <div className="kpi-value">
          <span className="kpi-value-prefix">+</span>
          <span className="kpi-value-num">{stats.avgLead != null ? stats.avgLead : "—"}</span>
          <span className="kpi-value-unit">d</span>
        </div>
        <div className="kpi-sub">
          <span>AHEAD OF MAINSTREAM</span>
          <Sparkline data={sparks.lead.length ? sparks.lead : [70,78,85,80,88,92,90,95]} width={72} height={12} color="#e8ff4a" fillOpacity={0.12}/>
        </div>
      </div>
    </section>
  );
}

/* =============== SIGNAL CARD =============== */
function SignalCard({ a, ghostIntensity, selected, onSelect }) {
  // asymmetry drives ghost effect (0..1); clamp and apply intensity
  const rawAsym = Math.max(0, Math.min(1, (a.score - 60) / 40));
  const asym = rawAsym * ghostIntensity;
  const subParts = [];
  if (a.age) subParts.push({ cls: "tag-age", txt: `${a.age}Y` });
  if (a.position) subParts.push({ txt: a.position.split(/[\s(,/]/)[0].toUpperCase() });
  if (a.club) {
    let c = a.club.split(/[(\[]/)[0].trim();
    if (c.length > 26) c = c.slice(0, 24) + "…";
    subParts.push({ txt: c });
  }
  if (a.league) {
    let l = a.league.split(/[/]/)[0].trim();
    if (l.length > 22) l = l.slice(0, 20) + "…";
    subParts.push({ txt: l });
  }

  return (
    <article
      className={`signal ${a.is_ghost ? "ghost" : ""} ${selected ? "selected" : ""}`}
      style={{ "--asym": asym.toFixed(3), "--ghost-mix": a.is_ghost ? 1 : 0 }}
      onClick={onSelect}
      data-screen-label={`Signal ${a.id}`}
    >
      {/* LEFT: asymmetry block */}
      <div className="signal-score">
        <svg className="signal-score-ring" width="56" height="56">
          <circle cx="28" cy="28" r="24" fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth="1.5"/>
          <circle cx="28" cy="28" r="24" fill="none"
            stroke={a.is_ghost ? "var(--sig-ghost)" : "var(--sig-primary)"} strokeWidth="1.5"
            strokeDasharray={`${(a.score / 100) * (2 * Math.PI * 24)} ${2 * Math.PI * 24}`}
            strokeLinecap="round"
            style={{ filter: `drop-shadow(0 0 4px ${a.is_ghost ? "var(--sig-ghost-glow)" : "var(--sig-primary-glow)"})` }}
          />
        </svg>
        <div className="signal-score-num" data-n={Math.round(a.score)} style={{ marginTop: 14 }}>
          {Math.round(a.score)}<span className="unit">/100</span>
        </div>
        <div className="signal-score-label">ASYM</div>
      </div>

      {/* CENTER: player */}
      <div className="signal-body">
        <div className="signal-name">{a.player_name}</div>
        <div className="signal-sub">
          {subParts.map((s, i) => (
            <span key={i} className={s.cls || ""}>{s.txt}</span>
          ))}
        </div>
        <div className="signal-badges">
          {a.is_ghost ? <span className="badge badge-ghost">◎ GHOST PROTOCOL</span> : null}
          {(a.detection_count || 1) > 1 && <span className="badge badge-track">◈ TRACKED ×{a.detection_count}</span>}
          <span className="badge badge-region">◉ {(a.region || "UNASSIGNED").toUpperCase()}</span>
        </div>
        <div className="signal-reason">{a.narrative || "Analyzing information asymmetry…"}</div>
      </div>

      {/* RIGHT: lead time hero + trace */}
      <div className="signal-lead">
        <div>
          <div className="signal-lead-label">
            <span>LEAD TIME</span>
            <b>{a.lead_time_days > 0 ? "CONFIRMED" : "TRACKING"}</b>
          </div>
          {a.lead_time_days > 0 ? (
            <div className="signal-lead-big">
              <span className="signal-lead-prefix">+</span>
              <span className="signal-lead-num">{a.lead_time_days}</span>
              <span className="signal-lead-unit">d</span>
            </div>
          ) : (
            <div className="signal-lead-big no-lead">
              <span className="signal-lead-num">TRACKING</span>
            </div>
          )}
        </div>
        <div className="signal-trace">
          <div className="signal-trace-meta">
            <span>SIG-{String(a.id).padStart(3, "0")}</span>
            <span>{a.history.length || 1} DETECT</span>
          </div>
          <MiniTrace history={a.history} />
          <div className="signal-trace-meta">
            <span>{fmtDate(a.first_detected || a.detection_date)}</span>
            <span>→ {fmtDate(a.last_seen || a.detection_date)}</span>
          </div>
        </div>
      </div>
    </article>
  );
}

/* =============== DOSSIER =============== */
function Dossier({ a }) {
  const subParts = [a.age && `${a.age}y`, a.position, a.club, a.league].filter(Boolean).join(" · ");
  return (
    <div className="dossier">
      <div className="dossier-header">
        <div className="dossier-meta">
          <span>DOSSIER · SIG-{String(a.id).padStart(3, "0")}</span>
          <b>{a.is_ghost ? "GHOST" : "TRACKED"}</b>
        </div>
        <div className="dossier-name">{a.player_name}</div>
        <div className="dossier-sub">{subParts || "— profile fragmentary —"}</div>
      </div>

      <div className="dossier-grid">
        <div className="dossier-cell primary">
          <div className="dossier-cell-label">Asymmetry</div>
          <div className="dossier-cell-val">{Math.round(a.score)}<span style={{ fontSize: 10, color: "var(--text-lo)" }}>/100</span></div>
        </div>
        <div className="dossier-cell lead">
          <div className="dossier-cell-label">Lead Time</div>
          <div className="dossier-cell-val">{a.lead_time_days > 0 ? `+${a.lead_time_days}d` : "—"}</div>
        </div>
        <div className="dossier-cell">
          <div className="dossier-cell-label">Detections</div>
          <div className="dossier-cell-val">{a.detection_count || 1}</div>
        </div>
        <div className="dossier-cell ghost">
          <div className="dossier-cell-label">Protocol</div>
          <div className="dossier-cell-val">{a.is_ghost ? "GHOST" : "TRACKED"}</div>
        </div>
      </div>

      <div className="dossier-section">
        <div className="dossier-section-h">Signal Trace · Asymmetry History</div>
        <SignalTrace history={a.history} color={a.is_ghost ? "#b8fff0" : "#ff2a3f"} />
        <div style={{ marginTop: 6, display: "flex", justifyContent: "space-between", fontSize: 9, color: "var(--text-lo)", fontFamily: "var(--font-mono)", letterSpacing: "0.1em" }}>
          <span>{fmtDT(a.first_detected || a.detection_date)}</span>
          <span>→ {fmtDT(a.last_seen || a.detection_date)}</span>
        </div>
      </div>

      <div className="dossier-section">
        <div className="dossier-section-h">Field Coordinates</div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, fontSize: 10.5 }}>
          <div><span style={{ color: "var(--text-lo)" }}>REGION</span><div style={{ color: "var(--text-hi)" }}>{(a.region || "UNASSIGNED").toUpperCase()}</div></div>
          <div><span style={{ color: "var(--text-lo)" }}>CLUB</span><div style={{ color: "var(--text-hi)" }}>{(a.club || "—").slice(0, 26)}</div></div>
          <div><span style={{ color: "var(--text-lo)" }}>LEAGUE</span><div style={{ color: "var(--text-hi)" }}>{(a.league || "—").slice(0, 26)}</div></div>
          <div><span style={{ color: "var(--text-lo)" }}>COORD</span><div style={{ color: "var(--sig-ghost)" }}>{coordLabel(a.id)}</div></div>
        </div>
      </div>

      <div className="dossier-section">
        <div className="dossier-section-h">Analyst Intercept</div>
        <div className="dossier-narrative">{a.narrative || "Analyzing information asymmetry — intelligence pending."}</div>
      </div>

      {a.stats.length > 0 && (
        <div className="dossier-section">
          <div className="dossier-section-h">Telemetry · Performance</div>
          <div style={{ display: "grid", gridTemplateColumns: `repeat(${a.stats.length}, 1fr)`, gap: 1, background: "var(--line-weak)", border: "1px solid var(--line-weak)" }}>
            {a.stats.map(([k, v]) => (
              <div key={k} style={{ background: "var(--bg-deep)", padding: "8px 10px", textAlign: "center" }}>
                <div style={{ fontSize: 8.5, color: "var(--text-lo)", letterSpacing: "0.2em" }}>{k}</div>
                <div style={{ fontFamily: "var(--font-mono)", fontSize: 14, color: "var(--text-hi)", marginTop: 2 }}>{v}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {a.sources?.length > 0 && (
        <div className="dossier-section">
          <div className="dossier-section-h">Origin · Sources</div>
          <div className="source-links">
            {a.sources.map((s, i) => {
              let host = s;
              try { host = new URL(s).hostname.replace("www.", ""); } catch {}
              return (
                <a key={i} className="source-link" href={s.startsWith("http") ? s : "#"} target="_blank" rel="noreferrer">
                  <span>{host.slice(0, 28)}</span>
                  <span style={{ color: "var(--text-lo)" }}>S{String(i + 1).padStart(2, "0")}</span>
                </a>
              );
            })}
          </div>
        </div>
      )}

      {a.mainstream_source && (
        <div className="dossier-section">
          <div className="dossier-section-h" style={{ color: "var(--sig-lead)" }}>Mainstream Surface</div>
          <a className="source-link" href={a.mainstream_source} target="_blank" rel="noreferrer" style={{ borderColor: "rgba(232,255,74,0.2)" }}>
            <span>{(() => { try { return new URL(a.mainstream_source).hostname.replace("www.", ""); } catch { return "source"; } })()}</span>
            <span style={{ color: "var(--sig-lead)" }}>DETECTED</span>
          </a>
        </div>
      )}
    </div>
  );
}

/* =============== TWEAKS =============== */
function TweaksPanel({ tweaks, onChange, onClose }) {
  return (
    <div className="tweaks">
      <div className="tweaks-h">
        <span>◎ TWEAKS</span>
        <span style={{ color: "var(--text-lo)", cursor: "pointer" }} onClick={onClose}>✕</span>
      </div>
      <div className="tweaks-body">
        <div className="tweak-row">
          <label>GHOST EFFECT INTENSITY <b>{Math.round(tweaks.ghostIntensity * 100)}%</b></label>
          <input type="range" min="0" max="1.5" step="0.05"
            value={tweaks.ghostIntensity}
            onChange={e => onChange("ghostIntensity", parseFloat(e.target.value))}/>
          <div style={{ fontSize: 9, color: "var(--text-lo)", lineHeight: 1.4 }}>
            Higher asymmetry = more invisible card. Pull to 0 for legibility audit.
          </div>
        </div>

        <div className="tweak-row">
          <label>RADAR SWEEP</label>
          <div className="tweak-toggle">
            <button className={tweaks.showSweep ? "active" : ""} onClick={() => {
              onChange("showSweep", true);
              document.documentElement.style.setProperty("--sweep-display", "block");
            }}>ON</button>
            <button className={!tweaks.showSweep ? "active" : ""} onClick={() => {
              onChange("showSweep", false);
              document.documentElement.style.setProperty("--sweep-display", "none");
            }}>OFF</button>
          </div>
        </div>

        <div className="tweak-row">
          <label>DENSITY</label>
          <div className="tweak-toggle">
            <button className={tweaks.density === "comfortable" ? "active" : ""} onClick={() => {
              onChange("density", "comfortable");
              document.documentElement.style.setProperty("--card-pad-y", "16px");
            }}>COMFORT</button>
            <button className={tweaks.density === "dense" ? "active" : ""} onClick={() => {
              onChange("density", "dense");
              document.documentElement.style.setProperty("--card-pad-y", "10px");
            }}>DENSE</button>
          </div>
        </div>
      </div>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<Shell/>);

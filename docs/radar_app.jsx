// OB1 Radar — main app
const { useState, useEffect, useMemo } = React;

function Shell() {
  const [data, setData] = useState([]);
  const [query, setQuery] = useState("");
  const [region, setRegion] = useState(null);
  const [ghostOnly, setGhostOnly] = useState(false);
  const [selectedId, setSelectedId] = useState(null);
  const [sortBy, setSortBy] = useState("score");
  const [mobileDrawerOpen, setMobileDrawerOpen] = useState(false);

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

  const regions = useMemo(() => {
    const map = new Map();
    data.forEach(a => {
      const r = a.region || "Sconosciuta";
      map.set(r, (map.get(r) || 0) + 1);
    });
    return [...map.entries()].sort((a, b) => b[1] - a[1]);
  }, [data]);

  const filtered = useMemo(() => {
    let out = data;
    if (query) {
      const q = query.toLowerCase();
      out = out.filter(a => [a.player_name, a.club, a.league, a.region, a.position].filter(Boolean).join(" ").toLowerCase().includes(q));
    }
    if (region) out = out.filter(a => (a.region || "Sconosciuta") === region);
    if (ghostOnly) out = out.filter(a => a.is_ghost);
    out = [...out].sort((a, b) => {
      if (sortBy === "lead") return (b.lead_time_days || 0) - (a.lead_time_days || 0);
      if (sortBy === "recent") return new Date(b.detection_date) - new Date(a.detection_date);
      return b.score - a.score;
    });
    return out;
  }, [data, query, region, ghostOnly, sortBy]);

  const stats = useMemo(() => {
    if (!filtered.length) return { count: 0, maxScore: 0, avgLead: null, ghosts: 0 };
    const max = Math.max(...filtered.map(a => a.score));
    const leads = filtered.filter(a => a.lead_time_days && a.lead_time_days > 0).map(a => a.lead_time_days);
    const avg = leads.length ? Math.round(leads.reduce((s, v) => s + v, 0) / leads.length) : null;
    return { count: filtered.length, maxScore: Math.round(max), avgLead: avg, ghosts: filtered.filter(a => a.is_ghost).length };
  }, [filtered]);

  const selected = filtered.find(a => a.id === selectedId) || filtered[0];

  const now = new Date();
  const nowStr = now.toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit", timeZone: "UTC" }) + " UTC";

  return (
    <div className="shell" data-screen-label="01 Radar Console">
      {/* TOPBAR */}
      <header className="topbar">
        <div className="brand">
          <OuroGlyph />
          OB1 <span className="brand-sep">//</span> GLOBAL <span className="brand-sub">RADAR</span>
        </div>
        <div className="topbar-meta">
          <div className="topbar-meta-item"><span>Aggiornato</span><b>{nowStr}</b></div>
          <div className="topbar-meta-item"><span>Giocatori</span><b>{data.length}</b></div>
        </div>
      </header>

      {/* LEFT RAIL */}
      <aside className="rail rail-left">
        <div className="rail-section">
          <input
            className="filter-search"
            placeholder="Cerca giocatore, club, lega..."
            value={query}
            onChange={e => setQuery(e.target.value)}
          />
        </div>

        <div className="rail-section">
          <div className="chip-row">
            <button className={`chip ghost ${ghostOnly ? "active" : ""}`} onClick={() => setGhostOnly(g => !g)}>
              ◎ Solo sconosciuti ai grandi club
            </button>
          </div>
        </div>

        <div className="rail-section">
          <div className="rail-h">Regione</div>
          <div className="region-list">
            <div className={`region-row ${!region ? "active" : ""}`} onClick={() => setRegion(null)}>
              <span>TUTTE</span><b>{data.length}</b>
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
          <div className="rail-h">Ultimi rilevati</div>
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

      {/* CENTER */}
      <main className="center">
        <div className="mobile-search-bar">
          <input
            className="filter-search"
            placeholder="Cerca giocatore, club, lega..."
            value={query}
            onChange={e => setQuery(e.target.value)}
          />
          <button className={`chip ghost ${ghostOnly ? "active" : ""}`} onClick={() => setGhostOnly(g => !g)}>
            ◎ Sconosciuti
          </button>
        </div>

        <TelegramCta />

        <KpiCluster stats={stats} />

        <div className="feed-bar">
          <h3>◎ Giocatori nel Radar</h3>
          <span className="feed-bar-count">{filtered.length.toString().padStart(3, "0")}</span>
          <span style={{ color: "var(--text-lo)" }}>· Ordina per</span>
          <div className="feed-bar-sort">
            <button className={`sort-chip ${sortBy === "score" ? "active" : ""}`} onClick={() => setSortBy("score")}>SCORE</button>
            <button className={`sort-chip ${sortBy === "lead" ? "active" : ""}`} onClick={() => setSortBy("lead")}>VANTAGGIO</button>
            <button className={`sort-chip ${sortBy === "recent" ? "active" : ""}`} onClick={() => setSortBy("recent")}>RECENTI</button>
          </div>
        </div>

        <div className="signal-list">
          {filtered.map(a => (
            <SignalCard
              key={a.id}
              a={a}
              selected={a.id === selectedId}
              onSelect={() => { setSelectedId(a.id); setMobileDrawerOpen(true); }}
            />
          ))}
          {!filtered.length && (
            <div style={{ padding: "60px 0", textAlign: "center", color: "var(--text-lo)", fontFamily: "var(--font-mono)", fontSize: 11, letterSpacing: "0.15em", textTransform: "uppercase" }}>
              — Nessun giocatore trovato —
            </div>
          )}
        </div>
      </main>

      {/* RIGHT RAIL (SCHEDA) */}
      <aside className="rail rail-right">
        {selected ? <Dossier a={selected} /> : (
          <div className="dossier-empty">
            <div className="pulse-dot"/>
            Seleziona un giocatore
          </div>
        )}
      </aside>

      {/* MOBILE DRAWER */}
      {mobileDrawerOpen && selected && (
        <div className="mobile-drawer">
          <div className="mobile-drawer-backdrop" onClick={() => setMobileDrawerOpen(false)} />
          <div className="mobile-drawer-panel">
            <div className="mobile-drawer-handle">
              <div className="mobile-drawer-grip" />
              <button className="mobile-drawer-close" onClick={() => setMobileDrawerOpen(false)}>✕ CHIUDI</button>
            </div>
            <div className="mobile-drawer-content">
              <Dossier a={selected} />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/* KPI CLUSTER */
function KpiCluster({ stats }) {
  return (
    <section className="kpi-cluster" data-om-validate="kpi">
      <div className="kpi kpi-anomalies">
        <div className="kpi-label">
          <span>Giocatori nel radar</span>
        </div>
        <div className="kpi-value">
          <span className="kpi-value-num">{String(stats.count).padStart(2, "0")}</span>
        </div>
        <div className="kpi-sub">
          <span>{stats.ghosts || 0} sconosciuti ai grandi · {Math.max(0, (stats.count || 0) - (stats.ghosts || 0))} già emersi</span>
        </div>
      </div>

      <div className="kpi kpi-max">
        <div className="kpi-label">
          <span>Scout Score massimo</span>
          <span className="kpi-label-id">/100</span>
        </div>
        <div className="kpi-value">
          <span className="kpi-value-num">{String(stats.maxScore || 0).padStart(2, "0")}</span>
        </div>
        <div className="kpi-sub">
          <span>Talento alto, visibilità bassa = opportunità</span>
        </div>
      </div>

      <div className="kpi kpi-lead">
        <div className="kpi-label">
          <span>Vantaggio medio sui media</span>
        </div>
        <div className="kpi-value">
          <span className="kpi-value-prefix">+</span>
          <span className="kpi-value-num">{stats.avgLead != null ? stats.avgLead : "—"}</span>
          <span className="kpi-value-unit">giorni</span>
        </div>
        <div className="kpi-sub">
          <span>Giorni tra il nostro rilevamento e la prima uscita sulla stampa</span>
        </div>
      </div>
    </section>
  );
}

/* SIGNAL CARD */
function SignalCard({ a, selected, onSelect }) {
  const rawAsym = Math.max(0, Math.min(1, (a.score - 60) / 40));
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
      style={{ "--asym": rawAsym.toFixed(3), "--ghost-mix": a.is_ghost ? 1 : 0 }}
      onClick={onSelect}
    >
      {/* LEFT: score */}
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
        <div className="signal-score-label">SCORE</div>
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
          {a.is_ghost ? <span className="badge badge-ghost">◎ Non su Transfermarkt</span> : null}
          {(a.detection_count || 1) > 1 && <span className="badge badge-track">◈ Rilevato {a.detection_count}×</span>}
          <span className="badge badge-region">◉ {(a.region || "Sconosciuta").toUpperCase()}</span>
        </div>
        <div className="signal-reason">{a.narrative || "Analisi in corso…"}</div>
      </div>

      {/* RIGHT: vantaggio */}
      <div className="signal-lead">
        <div>
          <div className="signal-lead-label">
            <span>VANTAGGIO</span>
            <b>{a.lead_time_days > 0 ? "CONFERMATO" : "IN ATTESA"}</b>
          </div>
          {a.lead_time_days > 0 ? (
            <div className="signal-lead-big" title={`OB1 ha rilevato questo giocatore ${a.lead_time_days} giorni prima che comparisse sui media mainstream`}>
              <span className="signal-lead-prefix">+</span>
              <span className="signal-lead-num">{a.lead_time_days}</span>
              <span className="signal-lead-unit">gg</span>
            </div>
          ) : (
            <div className="signal-lead-big no-lead" title="Non ancora comparso sui media mainstream — il vantaggio è ancora aperto">
              <span className="signal-lead-num" style={{ fontSize: 11 }}>In attesa</span>
            </div>
          )}
        </div>
        <div className="signal-trace">
          <div className="signal-trace-meta">
            <span>{a.history.length || 1} rilevamenti</span>
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

/* SCHEDA GIOCATORE */
function Dossier({ a }) {
  const subParts = [a.age && `${a.age}y`, a.position, a.club, a.league].filter(Boolean).join(" · ");
  return (
    <div className="dossier">
      <div className="dossier-header">
        <div className="dossier-meta">
          <span>Scheda Scout</span>
          <b>{a.is_ghost ? "Non su Transfermarkt" : "Già emerso"}</b>
        </div>
        <div className="dossier-name">{a.player_name}</div>
        <div className="dossier-sub">{subParts || "— profilo incompleto —"}</div>
      </div>

      <div className="dossier-grid">
        <div className="dossier-cell primary">
          <div className="dossier-cell-label">Scout Score</div>
          <div className="dossier-cell-val">{Math.round(a.score)}<span style={{ fontSize: 10, color: "var(--text-lo)" }}>/100</span></div>
        </div>
        <div className="dossier-cell lead" title="Giorni tra il rilevamento OB1 e la prima apparizione sui media mainstream (Transfermarkt, stampa, social). 'In attesa' = non ancora emerso pubblicamente.">
          <div className="dossier-cell-label">Vantaggio sui media</div>
          <div className="dossier-cell-val">{a.lead_time_days > 0 ? `+${a.lead_time_days}gg` : "In attesa"}</div>
        </div>
        <div className="dossier-cell">
          <div className="dossier-cell-label">Rilevamenti</div>
          <div className="dossier-cell-val">{a.detection_count || 1}</div>
        </div>
        <div className="dossier-cell ghost">
          <div className="dossier-cell-label">Transfermarkt</div>
          <div className="dossier-cell-val">{a.is_ghost ? "Non presente" : "Presente"}</div>
        </div>
      </div>

      <div className="dossier-section">
        <div className="dossier-section-h">Storico rilevamenti</div>
        <SignalTrace history={a.history} color={a.is_ghost ? "#b8fff0" : "#ff2a3f"} />
        <div style={{ marginTop: 6, display: "flex", justifyContent: "space-between", fontSize: 9, color: "var(--text-lo)", fontFamily: "var(--font-mono)", letterSpacing: "0.1em" }}>
          <span>{fmtDT(a.first_detected || a.detection_date)}</span>
          <span>→ {fmtDT(a.last_seen || a.detection_date)}</span>
        </div>
      </div>

      <div className="dossier-section">
        <div className="dossier-section-h">Informazioni</div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, fontSize: 10.5 }}>
          <div><span style={{ color: "var(--text-lo)" }}>REGIONE</span><div style={{ color: "var(--text-hi)" }}>{(a.region || "—").toUpperCase()}</div></div>
          <div><span style={{ color: "var(--text-lo)" }}>CLUB</span><div style={{ color: "var(--text-hi)" }}>{(a.club || "—").slice(0, 26)}</div></div>
          <div><span style={{ color: "var(--text-lo)" }}>LEGA</span><div style={{ color: "var(--text-hi)" }}>{(a.league || "—").slice(0, 26)}</div></div>
          <div><span style={{ color: "var(--text-lo)" }}>PRIMO RILEVAMENTO</span><div style={{ color: "var(--text-hi)" }}>{fmtDate(a.first_detected || a.detection_date)}</div></div>
        </div>
      </div>

      <div className="dossier-section">
        <div className="dossier-section-h">Perché scouting</div>
        <div className="dossier-narrative">{a.narrative || "Analisi in corso."}</div>
      </div>

      {a.stats.length > 0 && (
        <div className="dossier-section">
          <div className="dossier-section-h">Statistiche</div>
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
          <div className="dossier-section-h">Fonti</div>
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
          <div className="dossier-section-h" style={{ color: "var(--sig-lead)" }}>Apparso sui media mainstream</div>
          <a className="source-link" href={a.mainstream_source} target="_blank" rel="noreferrer" style={{ borderColor: "rgba(232,255,74,0.2)" }}>
            <span>{(() => { try { return new URL(a.mainstream_source).hostname.replace("www.", ""); } catch { return "fonte"; } })()}</span>
            <span style={{ color: "var(--sig-lead)" }}>RILEVATO</span>
          </a>
        </div>
      )}
    </div>
  );
}

/* TELEGRAM CTA */
function TelegramCta() {
  return (
    <a
      href="https://t.me/WorldOuroboros"
      target="_blank"
      rel="noreferrer"
      className="tg-cta"
    >
      <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
        <path d="M12 0C5.373 0 0 5.373 0 12s5.373 12 12 12 12-5.373 12-12S18.627 0 12 0zm5.894 8.221-1.97 9.28c-.145.658-.537.818-1.084.508l-3-2.21-1.447 1.394c-.16.16-.295.295-.605.295l.213-3.053 5.56-5.023c.242-.213-.054-.333-.373-.12L8.32 13.617l-2.96-.924c-.643-.204-.657-.643.136-.953l11.57-4.461c.537-.194 1.006.131.828.942z"/>
      </svg>
      <div className="tg-cta-text">
        <strong>Ricevi i segnali in tempo reale</strong>
        <span>Ogni nuovo talento rilevato arriva direttamente su Telegram — prima che lo scopra qualcun altro. Unisciti al canale.</span>
      </div>
      <span className="tg-cta-arrow">→</span>
    </a>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<Shell/>);

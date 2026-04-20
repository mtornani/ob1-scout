// OB1 Radar — shared primitives (icons, sparkline, helpers)

const OuroGlyph = () => (
  <svg className="brand-glyph" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 2a10 10 0 1 1-7 17"/>
    <path d="M11 2h2"/>
    <path d="M5 5 L3 5 L3 3"/>
    <circle cx="12" cy="12" r="3"/>
  </svg>
);

const CrossHair = () => (
  <svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" strokeWidth="1">
    <circle cx="5" cy="5" r="3.5"/>
    <path d="M5 0v10M0 5h10"/>
  </svg>
);

// small sparkline
function Sparkline({ data, width = 80, height = 14, color = "currentColor", fillOpacity = 0 }) {
  if (!data || data.length < 2) return null;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const pad = 1;
  const pts = data.map((v, i) => {
    const x = pad + (i / (data.length - 1)) * (width - pad * 2);
    const y = pad + (1 - (v - min) / range) * (height - pad * 2);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const area = `M${pts[0]} L${pts.join(" L")} L${width - pad},${height - pad} L${pad},${height - pad} Z`;
  return (
    <svg className="kpi-sparkline" width={width} height={height} viewBox={`0 0 ${width} ${height}`}>
      {fillOpacity > 0 && <path d={area} fill={color} fillOpacity={fillOpacity}/>}
      <polyline points={pts.join(" ")} fill="none" stroke={color} strokeWidth="1" strokeLinecap="round" strokeLinejoin="round"/>
      <circle cx={pts[pts.length-1].split(",")[0]} cy={pts[pts.length-1].split(",")[1]} r="1.3" fill={color}/>
    </svg>
  );
}

// bigger signal trace inside dossier
function SignalTrace({ history, width = 288, height = 80, color = "#ff2a3f" }) {
  if (!history || history.length < 2) {
    return (
      <div className="signal-trace-big" style={{
        display: "flex", alignItems: "center", justifyContent: "center",
        fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--text-lo)",
        letterSpacing: "0.15em", textTransform: "uppercase"
      }}>— Insufficient data —</div>
    );
  }
  const scores = history.map(h => h.score);
  const min = 60; // radar floor
  const max = 100;
  const pad = 8;
  const pts = scores.map((s, i) => {
    const x = pad + (i / (scores.length - 1)) * (width - pad * 2);
    const y = pad + (1 - (s - min) / (max - min)) * (height - pad * 2);
    return [x, y];
  });
  const poly = pts.map(p => p.join(",")).join(" ");
  // gridlines
  const gridLines = [70, 80, 90, 100].map(v => {
    const y = pad + (1 - (v - min) / (max - min)) * (height - pad * 2);
    return { y, label: v };
  });
  return (
    <svg className="signal-trace-big" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none">
      {gridLines.map(g => (
        <g key={g.label}>
          <line x1={pad} y1={g.y} x2={width - pad} y2={g.y} stroke="rgba(255,255,255,0.04)" strokeWidth="1" strokeDasharray="2 3"/>
          <text x={width - pad + 1} y={g.y + 3} fontSize="7" fontFamily="JetBrains Mono" fill="#4a505b" textAnchor="end">{g.label}</text>
        </g>
      ))}
      <polyline points={poly} fill="none" stroke={color} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" style={{ filter: `drop-shadow(0 0 4px ${color}88)` }}/>
      {pts.map(([x, y], i) => (
        <circle key={i} cx={x} cy={y} r={i === pts.length - 1 ? 2.5 : 1.2} fill={color}/>
      ))}
    </svg>
  );
}

// right-column mini trace
function MiniTrace({ history, width = 188, height = 22 }) {
  if (!history || history.length < 2) {
    return <div style={{ fontFamily: "var(--font-mono)", fontSize: 9, color: "var(--text-lo)", letterSpacing: "0.1em" }}>— SINGLE DETECTION —</div>;
  }
  const scores = history.map(h => h.score);
  const min = Math.min(...scores, 60);
  const max = Math.max(...scores, 100);
  const range = max - min || 1;
  const pad = 2;
  const pts = scores.map((s, i) => {
    const x = pad + (i / (scores.length - 1)) * (width - pad * 2);
    const y = pad + (1 - (s - min) / range) * (height - pad * 2);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const last = scores[scores.length - 1];
  const first = scores[0];
  const color = last >= first ? "#e8ff4a" : "#ff2a3f";
  return (
    <svg className="signal-trace-svg" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none">
      <polyline points={pts.join(" ")} fill="none" stroke={color} strokeWidth="1" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  );
}

// parse sqlite date strings
function parseHistory(raw) {
  try {
    const h = typeof raw === "string" ? JSON.parse(raw) : raw;
    if (!Array.isArray(h)) return [];
    return h.map(d => ({ score: +d.score, date: d.date }));
  } catch { return []; }
}

// tactical coordinate label from region/id
function coordLabel(id) {
  const x = ((id * 37) % 360 - 180).toFixed(2);
  const y = ((id * 53) % 180 - 90).toFixed(2);
  const sx = x >= 0 ? "E" : "W";
  const sy = y >= 0 ? "N" : "S";
  return `${Math.abs(y)}°${sy} ${Math.abs(x)}°${sx}`;
}

function fmtTime(iso) {
  if (!iso) return "--:--";
  const d = new Date(iso);
  return `${String(d.getHours()).padStart(2,"0")}:${String(d.getMinutes()).padStart(2,"0")}`;
}
function fmtDate(iso) {
  if (!iso) return "--/--";
  const d = new Date(iso);
  return `${String(d.getDate()).padStart(2,"0")}/${String(d.getMonth()+1).padStart(2,"0")}`;
}
function fmtDT(iso) {
  if (!iso) return "—";
  return `${fmtDate(iso)} · ${fmtTime(iso)}`;
}

function cleanNarrative(raw) {
  if (!raw) return "";
  const idx = raw.indexOf("\n\nStats:");
  return idx > 0 ? raw.substring(0, idx) : raw;
}

function parseStats(s) {
  if (!s) return [];
  const parts = [];
  const mp = s.match(/(?:MP|Appearances)[.\s:]*(\d+)/i); if (mp) parts.push(["MP", mp[1]]);
  const g = s.match(/(?:Gls|Goals)[.\s:]*(\d+)/i); if (g) parts.push(["GL", g[1]]);
  const a = s.match(/(?:Ast|Assists)[.\s:]*(\d+)/i); if (a) parts.push(["AS", a[1]]);
  const min = s.match(/(?:Min|Minutes)[.\s:]*(\d[\d,']*)/i); if (min) parts.push(["MIN", min[1]]);
  return parts;
}

Object.assign(window, {
  OuroGlyph, CrossHair,
  Sparkline, SignalTrace, MiniTrace,
  parseHistory, coordLabel, fmtTime, fmtDate, fmtDT,
  cleanNarrative, parseStats
});

// OB1 Dashboard Tracker
// Sends anonymous usage pings to a proxy endpoint (Pipedream or similar).
// Configure TRACK_URL via the data-track-url attribute on the script tag,
// or set window.OB1_TRACK_URL before this script loads.
// No personal data collected — device/browser/timezone only.

(function () {
  const TRACK_URL = (
    document.currentScript?.dataset?.trackUrl ||
    window.OB1_TRACK_URL ||
    ""
  ).trim();

  if (!TRACK_URL) return;

  let activeSeconds = 0;
  let lastVisible = Date.now();
  let playersSeen = [];

  // ── Device fingerprint ───────────────────────────────────────────────────
  function deviceInfo() {
    const ua = navigator.userAgent;
    const mobile = /Mobi|Android|iPhone|iPad/i.test(ua);
    let browser = "Unknown";
    if (/Edg\//i.test(ua)) browser = "Edge";
    else if (/Chrome/i.test(ua)) browser = "Chrome";
    else if (/Safari/i.test(ua)) browser = "Safari";
    else if (/Firefox/i.test(ua)) browser = "Firefox";
    let os = "Unknown";
    if (/iPhone|iPad/i.test(ua)) os = "iOS";
    else if (/Android/i.test(ua)) os = "Android";
    else if (/Windows/i.test(ua)) os = "Windows";
    else if (/Mac/i.test(ua)) os = "macOS";
    else if (/Linux/i.test(ua)) os = "Linux";
    return {
      mobile,
      browser,
      os,
      screen: `${window.screen.width}×${window.screen.height}`,
      lang: navigator.language || "?",
      tz: Intl.DateTimeFormat().resolvedOptions().timeZone || "?",
    };
  }

  // ── Send ping ────────────────────────────────────────────────────────────
  function ping(event, extra = {}) {
    const d = deviceInfo();
    const payload = {
      event,
      ts: new Date().toISOString(),
      device: d.mobile ? "📱 Mobile" : "🖥 Desktop",
      browser: d.browser,
      os: d.os,
      screen: d.screen,
      lang: d.lang,
      tz: d.tz,
      ref: (() => { try { return document.referrer ? new URL(document.referrer).hostname || "diretto" : "diretto"; } catch (_) { return "diretto"; } })(),
      ...extra,
    };
    try {
      navigator.sendBeacon(TRACK_URL, JSON.stringify(payload));
    } catch (_) {}
  }

  // ── Track active time ────────────────────────────────────────────────────
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      activeSeconds += Math.round((Date.now() - lastVisible) / 1000);
    } else {
      lastVisible = Date.now();
    }
  });

  // ── Page open ────────────────────────────────────────────────────────────
  ping("open");

  // ── Page close ───────────────────────────────────────────────────────────
  window.addEventListener("pagehide", () => {
    if (!document.hidden) {
      activeSeconds += Math.round((Date.now() - lastVisible) / 1000);
    }
    ping("close", {
      active_sec: activeSeconds,
      players_seen: playersSeen.length,
      players: playersSeen.slice(0, 5).join(", "),
    });
  });

  // ── Public API (optional hooks from page scripts) ────────────────────────
  window.OB1Track = {
    playerView(name, score, is_ghost) {
      if (!playersSeen.includes(name)) playersSeen.push(name);
      ping("player_view", { player: name, score, ghost: is_ghost ? 1 : 0 });
    },
    filterRegion(region) {
      ping("filter_region", { region: region || "tutte" });
    },
    filterGhost(active) {
      ping("filter_ghost", { active: active ? 1 : 0 });
    },
    sortChange(by) {
      ping("sort", { by });
    },
  };
})();

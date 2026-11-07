(() => {
  "use strict";

  /* ========================================================================
     Constantes
     ======================================================================== */

  const TAU = Math.PI * 2;
  const DEG = Math.PI / 180;
  const SWEEP_PERIOD_MS = 4000;
  const FETCH_TIMEOUT_MS = 10000;
  const ECHO_HISTORY_LENGTH = 3;
  const RANGE_STEPS = [5, 10, 25, 50, 75, 100, 125, 150, 200, 250];
  const RANGE_STORAGE_KEY = "radardesk.rangeNm";
  const PANEL_MODE_KEY = "radardesk.panel";
  const WEATHER_REFRESH_MS = 5 * 60 * 1000;
  const WEATHER_TILE_FRAME_MS = 900;
  const WEATHER_COLORS = {
    VFR: "#49e58b",
    MVFR: "#4da3ff",
    IFR: "#ff5464",
    LIFR: "#d36bff",
    UNKNOWN: "#7c8b9a",
  };
  const RADAR_THEMES = {
    phosphor: {
      rgb: "0, 255, 102",
      color: "#00ff66",
      label: "#c4ffd8",
      detail: "#51c77b",
      bright: "#eaffef",
      focus: "#c4ffd8",
    },
    mono: {
      rgb: "214, 230, 220",
      color: "#d6e6dc",
      label: "#ffffff",
      detail: "#83998c",
      bright: "#ffffff",
      focus: "#ffffff",
    },
    atc: {
      rgb: "95, 180, 255",
      color: "#5fb4ff",
      label: "#edf7ff",
      detail: "#89b4d5",
      bright: "#ffffff",
      focus: "#b7dcff",
      background: "#071018",
      atc: true,
    },
  };
  let currentRadarStyle = "phosphor";
  let currentRadarTheme = RADAR_THEMES.phosphor;
  const radarColor = (alpha) => `rgba(${currentRadarTheme.rgb}, ${alpha})`;
  const FONT_STACK = '"DejaVu Sans Mono", "Liberation Mono", "Courier New", monospace';
  const MONTHS = ["JAN", "FEB", "MAR", "AVR", "MAI", "JUIN", "JUIL", "AOUT", "SEP", "OCT", "NOV", "DEC"];
  const DEFAULT_CONFIG = Object.freeze({
    lat: 46.8687,
    lon: 6.9416,
    distNm: 50,
    maxDistNm: 150,
    refreshMs: 3000,
    radarStyle: "phosphor",
    locationMode: "coords",
    airportIcao: null,
  });

  const EMERGENCY_SQUAWKS = {
    "7500": { badge: "7500", color: "#ff4d5e", labelColor: "#ffd0d4", detailColor: "#d9606c" },
    "7600": { badge: "7600", color: "#ff4d5e", labelColor: "#ffd0d4", detailColor: "#d9606c" },
    "7700": { badge: "7700", color: "#ff4d5e", labelColor: "#ffd0d4", detailColor: "#d9606c" },
  };

  const MILITARY_STYLE = {
    badge: "MIL",
    color: "#ffc247",
    labelColor: "#ffe0a0",
    detailColor: "#c59a42",
  };

  const NORMAL_STYLE = {
    badge: "",
    color: "#00ff66",
    labelColor: "#c4ffd8",
    detailColor: "#51c77b",
  };

  // Préfixes d'indicatifs militaires connus. Le backend ne fournit pas
  // toujours le flag militaire ; cet indice purement visuel suffit au scope.
  const MILITARY_CALLSIGN = /^(?:RCH|REACH|ASCOT|RRR|GAF|IAM|CTM|FAF|COTAM|ARM|NAVY|USAF|PAT|DUKE|HUNT|MAGMA|VIV|CFC|RCAF|PLF|BAF|SUI|MIL|FNY|AAC|RRF|TYPH|HAWK|FALCON)/;

  /* ========================================================================
     DOM
     ======================================================================== */

  const dom = {
    scope: document.getElementById("scope"),
    canvas: document.getElementById("radarCanvas"),
    fullscreenBtn: document.getElementById("fullscreenBtn"),
    utcTime: document.getElementById("utcTime"),
    utcDate: document.getElementById("utcDate"),
    targetCount: document.getElementById("targetCount"),
    targetScope: document.getElementById("targetScope"),
    streamMetric: document.getElementById("streamMetric"),
    streamState: document.getElementById("streamState"),
    streamDetail: document.getElementById("streamDetail"),
    contactList: document.getElementById("contactList"),
    contactCount: document.getElementById("contactCount"),
    contactTitle: document.getElementById("contactsTitle"),
    panelToggle: document.getElementById("panelToggle"),
    nearestAirport: document.getElementById("nearestAirport"),
    nearestCode: document.getElementById("nearestCode"),
    nearestName: document.getElementById("nearestName"),
    nearestDistance: document.getElementById("nearestDistance"),
    nearestCategory: document.getElementById("nearestCategory"),
    nearestWind: document.getElementById("nearestWind"),
    nearestVisibility: document.getElementById("nearestVisibility"),
    nearestCeiling: document.getElementById("nearestCeiling"),
    nearestTemp: document.getElementById("nearestTemp"),
    nearestRaw: document.getElementById("nearestRaw"),
    nearestWeatherBtn: document.getElementById("nearestWeatherBtn"),
    sourceName: document.getElementById("sourceName"),
    sourceMode: document.getElementById("sourceMode"),
    lastUpdate: document.getElementById("lastUpdate"),
    hudCoords: document.getElementById("hudCoords"),
    hudLink: document.getElementById("hudLink"),
    hudLocation: document.getElementById("hudLocation"),
    wxState: document.getElementById("wxState"),
    weatherCard: document.getElementById("weatherCard"),
    weatherClose: document.getElementById("weatherClose"),
    weatherCategory: document.getElementById("weatherCategory"),
    weatherCode: document.getElementById("weatherCode"),
    weatherName: document.getElementById("weatherName"),
    weatherWind: document.getElementById("weatherWind"),
    weatherVisibility: document.getElementById("weatherVisibility"),
    weatherCeiling: document.getElementById("weatherCeiling"),
    weatherTemp: document.getElementById("weatherTemp"),
    weatherQnh: document.getElementById("weatherQnh"),
    weatherObserved: document.getElementById("weatherObserved"),
    weatherRaw: document.getElementById("weatherRaw"),
  };

  const ctx = dom.canvas.getContext("2d", { alpha: false, desynchronized: true });
  if (!ctx) {
    return;
  }

  /* ========================================================================
     État applicatif et géométrie du scope
     ======================================================================== */

  const state = {
    config: { ...DEFAULT_CONFIG },
    rawAircraft: [],
    airports: [],
    visibleAirports: [],
    runways: new Map(),
    airportWeather: new Map(),
    nearestAirport: null,
    panelMode: "airport",
    weatherSignature: "",
    weatherFetchedAt: 0,
    focusedAirport: null,
    targets: [],
    byHex: new Map(),
    focusedHex: null,
    streamStatus: "connecting",
    source: null,
    fallback: false,
    stale: false,
    failures: 0,
    polling: false,
    pollTimer: 0,
    frameId: 0,
    sweepOrigin: 0,
    lastClockSecond: -1,
  };

  const view = {
    width: 0,
    height: 0,
    dpr: 1,
    cx: 0,
    cy: 0,
    radius: 0,
    ready: false,
    labelFont: 10,
    detailFont: 8.5,
  };

  let backgroundCanvas = null;
  let sweepCanvas = null;
  let sweepCanvasSize = 0;
  let resizeObserver = null;
  let wakeLock = null;

  const weatherRadar = {
    host: null,
    frames: [],
    signature: "",
    index: 0,
    zoom: null,
    tiles: new Map(),
    canvas: null,
    dirty: true,
    lastFrameAt: 0,
  };

  /* ========================================================================
     Utilitaires
     ======================================================================== */

  function setText(node, value) {
    if (node && node.textContent !== value) {
      node.textContent = value;
    }
  }

  // PRNG déterministe : le grain phosphore reste stable entre deux frames.
  function mulberry32(seed) {
    let value = seed;
    return () => {
      value |= 0;
      value = (value + 0x6d2b79f5) | 0;
      let result = Math.imul(value ^ (value >>> 15), 1 | value);
      result = (result + Math.imul(result ^ (result >>> 7), 61 | result)) ^ result;
      return ((result ^ (result >>> 14)) >>> 0) / 4294967296;
    };
  }

  function toFiniteNumber(value) {
    const number = typeof value === "number" ? value : Number.parseFloat(value);
    return Number.isFinite(number) ? number : null;
  }

  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  function cleanCallsign(value) {
    if (value === null || value === undefined) {
      return null;
    }
    const text = String(value).replace(/\s+/g, "").trim().toUpperCase();
    return text ? text.slice(0, 16) : null;
  }

  function cleanType(value) {
    if (value === null || value === undefined) {
      return null;
    }
    const text = String(value).trim().toUpperCase();
    return text ? text.slice(0, 16) : null;
  }

  function cleanSquawk(value) {
    if (value === null || value === undefined) {
      return null;
    }
    const text = String(value).trim();
    return /^\d{1,4}$/.test(text) ? text.padStart(4, "0") : null;
  }

  function pad2(value) {
    return String(value).padStart(2, "0");
  }

  function formatAltitude(altitude, onGround) {
    if (onGround) {
      return "GND";
    }
    if (altitude === null) {
      return "---";
    }
    if (altitude >= 18000) {
      return `FL${String(Math.round(altitude / 100)).padStart(3, "0")}`;
    }
    return `${Math.round(altitude)}ft`;
  }

  function formatSpeed(speed) {
    return speed === null ? "---" : `${Math.round(speed)}kt`;
  }

  function formatCoordinate(value, positive, negative, integerWidth) {
    const suffix = value >= 0 ? positive : negative;
    const absolute = Math.abs(value);
    const integer = String(Math.floor(absolute)).padStart(integerWidth, "0");
    const decimals = absolute.toFixed(5).split(".")[1];
    return `${integer}.${decimals}${suffix}`;
  }

  function formatUtcStamp(value) {
    const date = value instanceof Date ? value : new Date(value);
    if (Number.isNaN(date.getTime())) {
      return "--:--:--Z";
    }
    return `${pad2(date.getUTCHours())}:${pad2(date.getUTCMinutes())}:${pad2(date.getUTCSeconds())}Z`;
  }

  function distanceNm(lat1, lon1, lat2, lon2) {
    const radiusNm = 3440.065;
    const phi1 = lat1 * DEG;
    const phi2 = lat2 * DEG;
    const dPhi = (lat2 - lat1) * DEG;
    const dLambda = (lon2 - lon1) * DEG;
    const a =
      Math.sin(dPhi / 2) ** 2 +
      Math.cos(phi1) * Math.cos(phi2) * Math.sin(dLambda / 2) ** 2;
    return radiusNm * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(Math.max(0, 1 - a)));
  }

  function bearingDeg(lat1, lon1, lat2, lon2) {
    const phi1 = lat1 * DEG;
    const phi2 = lat2 * DEG;
    const dLambda = (lon2 - lon1) * DEG;
    const y = Math.sin(dLambda) * Math.cos(phi2);
    const x =
      Math.cos(phi1) * Math.sin(phi2) -
      Math.sin(phi1) * Math.cos(phi2) * Math.cos(dLambda);
    return (Math.atan2(y, x) / DEG + 360) % 360;
  }

  function styleForTarget(callsign, squawk) {
    if (squawk && EMERGENCY_SQUAWKS[squawk]) {
      return { ...EMERGENCY_SQUAWKS[squawk], priority: 0, kind: "emergency" };
    }
    if (callsign && MILITARY_CALLSIGN.test(callsign)) {
      return { ...MILITARY_STYLE, priority: 1, kind: "military" };
    }
    return {
      ...NORMAL_STYLE,
      color: currentRadarTheme.color,
      labelColor: currentRadarTheme.label,
      detailColor: currentRadarTheme.detail,
      priority: 2,
      kind: "normal",
    };
  }

  /* ========================================================================
     Conversion des données ADS-B
     ======================================================================== */

  function buildTarget(raw) {
    if (!raw || typeof raw !== "object") {
      return null;
    }

    const hex = String(raw.hex || raw.icao || "").trim().toLowerCase();
    const lat = toFiniteNumber(raw.lat);
    const lon = toFiniteNumber(raw.lon);
    if (!hex || lat === null || lon === null) {
      return null;
    }
    if (lat < -90 || lat > 90 || lon < -180 || lon > 180) {
      return null;
    }

    const rangeNm = state.config.distNm;
    const distance = distanceNm(state.config.lat, state.config.lon, lat, lon);
    if (!Number.isFinite(distance) || distance > rangeNm) {
      return null;
    }

    const bearing = bearingDeg(state.config.lat, state.config.lon, lat, lon);
    const rawCallsign = cleanCallsign(raw.callsign);
    const callsign = rawCallsign || hex.toUpperCase();
    const type = cleanType(raw.type);
    const squawk = cleanSquawk(raw.squawk);
    const altitude = toFiniteNumber(raw.alt_baro);
    const onGround = Boolean(raw.on_ground) || raw.alt_baro === "ground";
    const speed = toFiniteNumber(raw.gs);
    const track = toFiniteNumber(raw.track);
    const style = styleForTarget(rawCallsign, squawk);
    const altLabel = formatAltitude(altitude, onGround);
    const speedLabel = formatSpeed(speed);

    return {
      hex,
      callsign,
      rawCallsign,
      type,
      squawk,
      lat,
      lon,
      altitude,
      speed,
      track,
      onGround,
      distance,
      bearing,
      bearingRad: bearing * DEG,
      trackRad: (track === null ? bearing : track) * DEG,
      ratio: clamp(distance / rangeNm, 0, 1),
      style,
      altLabel,
      speedLabel,
      detailLabel: `${altLabel} / ${speedLabel}`,
      labelCallsign: callsign,
    };
  }

  function applyAircraftList(list, addHistory = true) {
    if (addHistory) {
      state.rawAircraft = Array.isArray(list) ? list : [];
    }

    const source = state.rawAircraft;
    const next = [];
    const previousByHex = state.byHex;

    for (const raw of source) {
      const target = buildTarget(raw);
      if (!target) {
        continue;
      }

      // Conserve les échos des positions précédentes pour dessiner une
      // traînée de rémanence réaliste derrière chaque piste. Lors d'un simple
      // changement de portée, on conserve l'historique sans ajouter une
      // nouvelle position.
      const previous = previousByHex.get(target.hex);
      if (addHistory) {
        target.echoes = previous
          ? [
              { distance: previous.distance, bearingRad: previous.bearingRad },
              ...(previous.echoes || []),
            ].slice(0, ECHO_HISTORY_LENGTH)
          : [];
      } else {
        target.echoes = previous ? [...(previous.echoes || [])] : [];
      }

      next.push(target);
    }

    state.targets = next;
    state.byHex = new Map(next.map((target) => [target.hex, target]));
    if (state.focusedHex && !state.byHex.has(state.focusedHex)) {
      state.focusedHex = null;
    }

    layoutTargets();
    updateCounts();
    renderContacts();
  }

  function updateCounts() {
    setText(dom.targetCount, String(state.targets.length).padStart(2, "0"));
  }

  /* ========================================================================
     Panneau contacts
     ======================================================================== */

  function renderContacts() {
    if (!state.targets.length) {
      const empty = document.createElement("li");
      empty.className = "contacts__empty";
      empty.textContent = state.streamStatus === "offline" ? "AUCUN CONTACT / NO LINK" : "AUCUN CONTACT DANS LA BULLE";
      dom.contactList.replaceChildren(empty);
      setText(dom.contactCount, "00/00");
      return;
    }

    const nearest = state.targets
      .slice()
      .sort((a, b) => a.style.priority - b.style.priority || a.distance - b.distance)
      .slice(0, 5);

    setText(
      dom.contactCount,
      `${String(nearest.length).padStart(2, "0")}/${String(state.targets.length).padStart(2, "0")}`,
    );

    const fragment = document.createDocumentFragment();
    nearest.forEach((target, index) => {
      const item = document.createElement("li");
      item.className = "contact-item";
      const button = document.createElement("button");
      button.type = "button";
      button.className = "contact";
      button.dataset.hex = target.hex;
      button.setAttribute("aria-pressed", String(state.focusedHex === target.hex));
      button.title = `${target.callsign} — ${target.type || "type inconnu"} — ${target.distance.toFixed(1)} NM`;
      if (target.style.kind === "emergency") {
        button.classList.add("contact--emergency");
      } else if (target.style.kind === "military") {
        button.classList.add("contact--military");
      }

      const rank = document.createElement("span");
      rank.className = "contact__rank";
      rank.textContent = pad2(index + 1);

      const main = document.createElement("span");
      main.className = "contact__main";

      const top = document.createElement("span");
      top.className = "contact__top";

      const callSign = document.createElement("span");
      callSign.className = "contact__callsign";
      callSign.textContent = target.callsign;
      top.appendChild(callSign);

      if (target.style.badge) {
        const badge = document.createElement("span");
        badge.className = "contact__badge";
        badge.textContent = target.style.badge;
        top.appendChild(badge);
      }

      const sub = document.createElement("span");
      sub.className = "contact__sub";
      sub.textContent = `${target.type || "---"} · ${target.distance.toFixed(1)} NM`;

      main.append(top, sub);

      const altitude = document.createElement("span");
      altitude.className = "contact__alt";
      altitude.textContent = target.altLabel;

      button.append(rank, main, altitude);
      item.appendChild(button);
      fragment.appendChild(item);
    });

    dom.contactList.replaceChildren(fragment);
  }

  function setFocusedTarget(hex) {
    state.focusedHex = hex || null;
    if (state.focusedHex) {
      hideWeatherCard();
    }
    const buttons = dom.contactList.querySelectorAll(".contact");
    for (const button of buttons) {
      button.setAttribute("aria-pressed", String(button.dataset.hex === state.focusedHex));
    }
    if (view.ready) {
      layoutTargets();
    }
  }

  dom.contactList.addEventListener("click", (event) => {
    const button = event.target.closest(".contact");
    if (!button) {
      return;
    }
    const hex = button.dataset.hex;
    setFocusedTarget(state.focusedHex === hex ? null : hex);
  });

  /* ========================================================================
     Statut du flux / HUD
     ======================================================================== */

  function setScopeState(status) {
    dom.scope.dataset.state = status;
    const labels = {
      connecting: "WAIT",
      live: "LIVE",
      stale: "STALE",
      offline: "NO LINK",
    };
    setText(dom.hudLink, labels[status] || "WAIT");
  }

  function setStreamState(status, detail) {
    state.streamStatus = status;
    dom.streamMetric.dataset.state = status;
    const labels = {
      connecting: "CONNEXION",
      live: "LIVE",
      stale: "DIFFÉRÉ",
      offline: "HORS LIGNE",
    };
    setText(dom.streamState, labels[status] || "CONNEXION");
    setText(dom.streamDetail, detail);
    setScopeState(status);
  }

  function updateSource(source, fallback) {
    if (source) {
      state.source = source;
    }
    state.fallback = Boolean(fallback);
    setText(dom.sourceName, state.source ? state.source.toUpperCase() : "EN ATTENTE");
    setText(dom.sourceMode, state.fallback ? "SECOURS" : "");
  }

  function updateHud() {
    const { lat, lon, distNm } = state.config;
    setText(
      dom.hudCoords,
      `${formatCoordinate(lat, "N", "S", 2)} ${formatCoordinate(lon, "E", "W", 3)}`,
    );
    for (const value of document.querySelectorAll(".range-value")) {
      setText(value, String(Math.round(distNm)));
    }
    setText(dom.targetScope, `DANS ${Math.round(distNm)} NM`);
    setText(
      dom.hudLocation,
      state.config.locationMode === "airport" && state.config.airportIcao
        ? ` · ${state.config.airportIcao}`
        : "",
    );
  }

  function getRangeSteps() {
    const max = Math.max(1, state.config.maxDistNm);
    const steps = RANGE_STEPS.filter((value) => value <= max);
    if (!steps.length || steps[steps.length - 1] !== max) {
      steps.push(max);
    }
    return [...new Set(steps)].sort((a, b) => a - b);
  }

  function clampRange(value) {
    return Math.max(1, Math.min(state.config.maxDistNm, Math.round(value)));
  }

  function updateRangeButtons() {
    const steps = getRangeSteps();
    const current = state.config.distNm;
    const canDown = steps.some((value) => value < current);
    const canUp = steps.some((value) => value > current);
    for (const button of document.querySelectorAll('[data-range-action="down"]')) {
      button.disabled = !canDown;
    }
    for (const button of document.querySelectorAll('[data-range-action="up"]')) {
      button.disabled = !canUp;
    }
  }

  function setRange(value) {
    const next = clampRange(value);
    if (next === state.config.distNm) {
      updateRangeButtons();
      return;
    }

    state.config.distNm = next;
    try {
      window.localStorage.setItem(RANGE_STORAGE_KEY, String(next));
    } catch {
      // Stockage local indisponible : la portée reste valable pour la session.
    }

    updateHud();
    updateRangeButtons();
    if (view.ready) {
      rebuildBackground();
    }
    if (state.focusedAirport) {
      const focused = state.visibleAirports.find(
        (airport) => airport.icao === state.focusedAirport,
      );
      if (!focused || focused.distance > next) {
        hideWeatherCard();
      }
    }
    // Les données jusqu'à la portée maximale sont déjà dans state.rawAircraft.
    applyAircraftList(null, false);
    refreshAirportWeather();
    weatherRadar.dirty = true;
  }

  function shiftRange(direction) {
    const steps = getRangeSteps();
    const current = state.config.distNm;
    const target =
      direction > 0
        ? steps.find((value) => value > current)
        : [...steps].reverse().find((value) => value < current);
    if (target !== undefined) {
      setRange(target);
    }
  }

  function applyRadarStyle(style) {
    const normalized = RADAR_THEMES[style] ? style : "phosphor";
    if (
      normalized === currentRadarStyle &&
      document.documentElement.dataset.theme === normalized
    ) {
      return;
    }

    currentRadarStyle = normalized;
    currentRadarTheme = RADAR_THEMES[normalized];
    state.config.radarStyle = normalized;
    document.documentElement.dataset.theme = normalized;

    for (const target of state.targets) {
      target.style = styleForTarget(target.rawCallsign, target.squawk);
    }

    if (view.ready) {
      rebuildBackground();
      rebuildSweep();
    }
  }

  function applyServerMeta(meta) {
    let changed = false;
    let rangeConfigChanged = false;

    if (meta.radarStyle) {
      applyRadarStyle(meta.radarStyle);
    }

    if (meta.lat !== null && meta.lat !== state.config.lat) {
      state.config.lat = meta.lat;
      changed = true;
    }
    if (meta.lon !== null && meta.lon !== state.config.lon) {
      state.config.lon = meta.lon;
      changed = true;
    }
    if (
      meta.maxDistNm !== null &&
      meta.maxDistNm >= 1 &&
      meta.maxDistNm !== state.config.maxDistNm
    ) {
      state.config.maxDistNm = meta.maxDistNm;
      rangeConfigChanged = true;
      if (state.config.distNm > state.config.maxDistNm) {
        state.config.distNm = state.config.maxDistNm;
        changed = true;
      }
    }
    if (meta.refreshMs !== null && meta.refreshMs >= 500 && meta.refreshMs !== state.config.refreshMs) {
      state.config.refreshMs = meta.refreshMs;
    }
    if (meta.locationMode && meta.locationMode !== state.config.locationMode) {
      state.config.locationMode = meta.locationMode;
      changed = true;
    }
    if (meta.airportIcao !== state.config.airportIcao) {
      state.config.airportIcao = meta.airportIcao;
      changed = true;
    }

    if (rangeConfigChanged) {
      updateRangeButtons();
    }
    if (changed || rangeConfigChanged) {
      updateHud();
      rebuildAirportGeometry();
      weatherRadar.dirty = true;
      if (view.ready) {
        rebuildBackground();
        layoutTargets();
      }
    }
  }

  /* ========================================================================
     Rendu Canvas : fond statique mis en cache
     ======================================================================== */

  function makeLayer(width, height) {
    const layer = document.createElement("canvas");
    layer.width = Math.max(1, Math.round(width * view.dpr));
    layer.height = Math.max(1, Math.round(height * view.dpr));
    const layerCtx = layer.getContext("2d");
    layerCtx.setTransform(view.dpr, 0, 0, view.dpr, 0, 0);
    return { layer, layerCtx };
  }

  function rebuildBackground() {
    if (!view.ready) {
      return;
    }

    const { width, height, cx, cy, radius: R } = view;
    const { layer, layerCtx: g } = makeLayer(width, height);
    const rangeNm = state.config.distNm;

    // Halo de phosphore très léger à l'intérieur du scope.
    const halo = g.createRadialGradient(cx, cy, R * 0.04, cx, cy, R * 1.01);
    halo.addColorStop(0, radarColor(0.034));
    halo.addColorStop(0.6, radarColor(0.011));
    halo.addColorStop(1, "rgba(0, 0, 0, 0)");
    g.fillStyle = halo;
    g.beginPath();
    g.arc(cx, cy, R * 1.01, 0, TAU);
    g.fill();

    // Axes N/S et E/O.
    g.save();
    g.strokeStyle = radarColor(0.085);
    g.lineWidth = 0.8;
    g.setLineDash([2, 6]);
    g.beginPath();
    g.moveTo(cx, cy - R);
    g.lineTo(cx, cy + R);
    g.moveTo(cx - R, cy);
    g.lineTo(cx + R, cy);
    g.stroke();
    g.restore();

    // Cercles de distance et libellés.
    g.textAlign = "center";
    g.textBaseline = "middle";
    for (let index = 1; index <= 5; index += 1) {
      const ringRadius = (R * index) / 5;
      const isOuter = index === 5;
      g.beginPath();
      g.strokeStyle = isOuter
        ? radarColor(0.31)
        : radarColor(0.135);
      g.lineWidth = isOuter ? 1.25 : 0.85;
      g.arc(cx, cy, ringRadius, 0, TAU);
      g.stroke();

      const value = (rangeNm * index) / 5;
      const rangeLabel = Number.isInteger(value) ? String(value) : value.toFixed(1);
      const label = isOuter ? `${rangeLabel} NM` : rangeLabel;
      const labelAngle = 40 * DEG;
      const lx = cx + Math.sin(labelAngle) * ringRadius;
      const ly = cy - Math.cos(labelAngle) * ringRadius;
      const labelWidth = isOuter ? 38 : 20;

      g.fillStyle = "rgba(0, 0, 0, 0.75)";
      g.fillRect(lx - labelWidth / 2, ly - 6, labelWidth, 12);
      g.font = `${Math.max(7, view.detailFont - 1)}px ${FONT_STACK}`;
      g.fillStyle = isOuter ? radarColor(0.58) : radarColor(0.42);
      g.fillText(label, lx, ly);
    }

    // Couronne extérieure et graduations de boussole.
    g.beginPath();
    g.strokeStyle = radarColor(0.1);
    g.lineWidth = 0.8;
    g.arc(cx, cy, R + 12, 0, TAU);
    g.stroke();

    for (let degrees = 0; degrees < 360; degrees += 5) {
      const radians = degrees * DEG;
      const isMajor = degrees % 30 === 0;
      const inner = R + 2;
      const outer = R + (isMajor ? 9 : 4);
      g.beginPath();
      g.strokeStyle = isMajor
        ? radarColor(0.46)
        : radarColor(0.2);
      g.lineWidth = isMajor ? 1 : 0.7;
      g.moveTo(cx + Math.sin(radians) * inner, cy - Math.cos(radians) * inner);
      g.lineTo(cx + Math.sin(radians) * outer, cy - Math.cos(radians) * outer);
      g.stroke();
    }

    // Libellés de cap tous les 30°, cardinaux exclus.
    g.textAlign = "center";
    g.textBaseline = "middle";
    g.font = `${Math.max(6, view.detailFont - 2.5)}px ${FONT_STACK}`;
    g.fillStyle = radarColor(0.34);
    for (let degrees = 30; degrees < 360; degrees += 30) {
      if (degrees % 90 === 0) {
        continue;
      }
      const radians = degrees * DEG;
      g.fillText(
        String(degrees).padStart(3, "0"),
        cx + Math.sin(radians) * (R + 20),
        cy - Math.cos(radians) * (R + 20),
      );
    }

    // Cardinales.
    const cardinals = [
      [0, "N"],
      [90, "E"],
      [180, "S"],
      [270, "W"],
    ];
    g.font = `700 ${Math.max(10, view.labelFont + 0.5)}px ${FONT_STACK}`;
    g.fillStyle = "rgba(180, 255, 205, 0.82)";
    for (const [degrees, label] of cardinals) {
      const radians = degrees * DEG;
      g.fillText(
        label,
        cx + Math.sin(radians) * (R + 25),
        cy - Math.cos(radians) * (R + 25),
      );
    }

    // Réticule central.
    g.save();
    g.strokeStyle = radarColor(0.55);
    g.lineWidth = 1;
    g.beginPath();
    g.moveTo(cx - 8, cy);
    g.lineTo(cx - 2, cy);
    g.moveTo(cx + 2, cy);
    g.lineTo(cx + 8, cy);
    g.moveTo(cx, cy - 8);
    g.lineTo(cx, cy - 2);
    g.moveTo(cx, cy + 2);
    g.lineTo(cx, cy + 8);
    g.stroke();
    g.beginPath();
    g.fillStyle = currentRadarTheme.label;
    g.arc(cx, cy, 1.7, 0, TAU);
    g.fill();
    g.restore();

    // Anneau extérieur lumineux (statique, donc shadowBlur acceptable).
    g.save();
    g.strokeStyle = radarColor(0.15);
    g.lineWidth = 5;
    g.arc(cx, cy, R, 0, TAU);
    g.stroke();
    g.strokeStyle = radarColor(0.52);
    g.lineWidth = 1;
    g.arc(cx, cy, R, 0, TAU);
    g.stroke();
    g.restore();

    // Grain phosphore statique et légère réflexion de verre. Ces détails sont
    // cuits une seule fois dans le fond pour ne pas coûter à chaque frame.
    g.save();
    g.beginPath();
    g.arc(cx, cy, R - 1, 0, TAU);
    g.clip();

    const glare = g.createRadialGradient(
      cx - R * 0.42,
      cy - R * 0.5,
      0,
      cx - R * 0.42,
      cy - R * 0.5,
      R * 1.55,
    );
    glare.addColorStop(0, "rgba(185, 255, 210, 0.038)");
    glare.addColorStop(0.32, "rgba(130, 255, 180, 0.012)");
    glare.addColorStop(1, "rgba(0, 0, 0, 0)");
    g.fillStyle = glare;
    g.fillRect(cx - R, cy - R, R * 2, R * 2);

    const random = mulberry32(0x5a17c0de);
    for (let index = 0; index < 520; index += 1) {
      const angle = random() * TAU;
      const distance = Math.sqrt(random()) * (R - 2);
      const x = cx + Math.sin(angle) * distance;
      const y = cy - Math.cos(angle) * distance;
      const bright = random();
      const alpha = 0.008 + bright * 0.028;
      const size = bright > 0.94 ? 1.1 : 0.55;
      g.fillStyle = `rgba(80, 255, 145, ${alpha})`;
      g.fillRect(x, y, size, size);
    }

    // Quelques impulsions de bruit plus marquées, très rares.
    for (let index = 0; index < 16; index += 1) {
      const angle = random() * TAU;
      const distance = Math.sqrt(random()) * (R - 5);
      const x = cx + Math.sin(angle) * distance;
      const y = cy - Math.cos(angle) * distance;
      g.fillStyle = "rgba(160, 255, 195, 0.055)";
      g.fillRect(x, y, 1.1, 1.1);
    }
    g.restore();

    backgroundCanvas = layer;
  }

  function rebuildSweep() {
    if (!view.ready) {
      return;
    }

    const R = view.radius;
    const cssSize = Math.ceil(R * 2 + 8);
    const { layer, layerCtx: g } = makeLayer(cssSize, cssSize);
    const center = cssSize / 2;
    const trailAngle = 90 * DEG;
    const steps = 72;
    const trailStrength = currentRadarTheme.atc ? 0.075 : 0.17;

    // Rémanence angulaire : 90° derrière le faisceau, plus dense près de la ligne.
    for (let index = 0; index < steps; index += 1) {
      const start = -trailAngle + (trailAngle * index) / steps;
      const end = -trailAngle + (trailAngle * (index + 1)) / steps + 0.0005;
      const progress = (index + 1) / steps;
      const alpha = trailStrength * Math.pow(progress, 2.4);
      g.beginPath();
      g.moveTo(center, center);
      g.arc(center, center, R, start, end);
      g.closePath();
      g.fillStyle = radarColor(alpha);
      g.fill();
    }

    // Ligne de balayage et glow.
    g.save();
    g.globalCompositeOperation = "lighter";
    const beam = g.createLinearGradient(center, center, center + R, center);
    beam.addColorStop(0, radarColor(0.035));
    beam.addColorStop(0.72, radarColor(0.38));
    beam.addColorStop(1, radarColor(0.95));
    g.strokeStyle = beam;
    g.lineWidth = 1.25;
    g.beginPath();
    g.moveTo(center, center);
    g.lineTo(center + R, center);
    g.stroke();

    g.strokeStyle = radarColor(0.13);
    g.lineWidth = 4;
    g.beginPath();
    g.moveTo(center, center);
    g.lineTo(center + R, center);
    g.stroke();

    // Point chaud au bord du scope, à l'endroit où le faisceau balaie la couronne.
    g.fillStyle = "rgba(190, 255, 210, 0.35)";
    g.beginPath();
    g.arc(center + R - 1, center, 1.7, 0, TAU);
    g.fill();
    g.restore();

    sweepCanvas = layer;
    sweepCanvasSize = cssSize;
  }

  /* ========================================================================
     Rendu Canvas : aéroports
     ======================================================================== */

  function rebuildAirportGeometry() {
    if (!state.airports.length) {
      state.visibleAirports = [];
      return;
    }

    const maxRange = Math.max(1, state.config.maxDistNm);
    const visible = [];

    for (const airport of state.airports) {
      const distance = distanceNm(
        state.config.lat,
        state.config.lon,
        airport.lat,
        airport.lon,
      );
      if (!Number.isFinite(distance) || distance > maxRange) {
        continue;
      }
      const bearing = bearingDeg(
        state.config.lat,
        state.config.lon,
        airport.lat,
        airport.lon,
      );
      visible.push({
        ...airport,
        distance,
        bearingRad: bearing * DEG,
      });
    }

    visible.sort((a, b) => {
      if (a.type !== b.type) {
        return a.type === "large_airport" ? -1 : 1;
      }
      return a.distance - b.distance;
    });
    state.visibleAirports = visible;
    updateNearestAirport();

    if (
      state.focusedAirport &&
      !visible.some(
        (airport) =>
          airport.icao === state.focusedAirport &&
          airport.distance <= state.config.distNm,
      )
    ) {
      hideWeatherCard();
    }
    refreshAirportWeather();
  }

  function weatherColor(category) {
    return WEATHER_COLORS[category] || WEATHER_COLORS.UNKNOWN;
  }

  function formatMetarTime(value) {
    if (!value) {
      return "—";
    }
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return String(value);
    }
    return `${pad2(date.getUTCHours())}:${pad2(date.getUTCMinutes())}Z`;
  }

  function ceilingLabel(metar) {
    for (const cloud of metar.clouds || []) {
      if (
        ["BKN", "OVC", "VV"].includes(cloud.cover) &&
        cloud.base_ft !== null &&
        cloud.base_ft !== undefined
      ) {
        return `${cloud.cover} ${cloud.base_ft} ft`;
      }
    }
    return "—";
  }

  function windLabel(metar) {
    const direction =
      metar.wind_direction === "VRB"
        ? "VRB"
        : metar.wind_direction !== null && metar.wind_direction !== undefined
          ? `${metar.wind_direction}°`
          : "—";
    const speed =
      metar.wind_speed_kt !== null && metar.wind_speed_kt !== undefined
        ? `${metar.wind_speed_kt} kt`
        : "—";
    const gust = metar.wind_gust_kt ? ` G${metar.wind_gust_kt}` : "";
    return `${direction} / ${speed}${gust}`;
  }

  function updateNearestAirport() {
    if (!state.airports.length) {
      state.nearestAirport = null;
      renderNearestAirport();
      return;
    }

    let nearest = null;
    let nearestDistance = Number.POSITIVE_INFINITY;
    for (const airport of state.airports) {
      const distance = distanceNm(
        state.config.lat,
        state.config.lon,
        airport.lat,
        airport.lon,
      );
      if (Number.isFinite(distance) && distance < nearestDistance) {
        nearestDistance = distance;
        nearest = airport;
      }
    }

    state.nearestAirport = nearest
      ? { ...nearest, distance: nearestDistance }
      : null;
    renderNearestAirport();
  }

  function renderNearestAirport() {
    const airport = state.nearestAirport;
    if (!airport) {
      setText(dom.nearestCode, "—");
      setText(dom.nearestName, "Aucun aéroport disponible");
      for (const node of [
        dom.nearestDistance,
        dom.nearestCategory,
        dom.nearestWind,
        dom.nearestVisibility,
        dom.nearestCeiling,
        dom.nearestTemp,
      ]) {
        setText(node, "—");
      }
      setText(dom.nearestRaw, "");
      dom.nearestWeatherBtn.disabled = true;
      return;
    }

    const metar = state.airportWeather.get(airport.icao);
    const category = metar?.flight_category || "N/A";
    const categoryColor = metar
      ? weatherColor(metar.flight_category)
      : WEATHER_COLORS.UNKNOWN;

    setText(
      dom.nearestCode,
      airport.iata ? `${airport.iata}/${airport.icao}` : airport.icao,
    );
    setText(dom.nearestName, airport.name || "");
    setText(dom.nearestDistance, `${airport.distance.toFixed(1)} NM`);
    setText(dom.nearestCategory, category);
    dom.nearestCategory.style.color = categoryColor;

    if (metar) {
      setText(dom.nearestWind, windLabel(metar));
      setText(
        dom.nearestVisibility,
        metar.visibility_sm !== null && metar.visibility_sm !== undefined
          ? `${metar.visibility_sm} SM`
          : "—",
      );
      setText(dom.nearestCeiling, ceilingLabel(metar));
      setText(
        dom.nearestTemp,
        metar.temperature_c !== null && metar.temperature_c !== undefined
          ? `${metar.temperature_c}° / ${metar.dewpoint_c ?? "—"}°`
          : "—",
      );
      setText(dom.nearestRaw, metar.raw || "");
    } else {
      for (const node of [
        dom.nearestWind,
        dom.nearestVisibility,
        dom.nearestCeiling,
        dom.nearestTemp,
      ]) {
        setText(node, "—");
      }
      setText(dom.nearestRaw, "METAR indisponible pour cette station.");
    }
    dom.nearestWeatherBtn.disabled = false;
  }

  function setPanelMode(mode) {
    state.panelMode = mode === "contacts" ? "contacts" : "airport";
    try {
      window.localStorage.setItem(PANEL_MODE_KEY, state.panelMode);
    } catch {
      // Mode valable pour la session uniquement.
    }

    const airportMode = state.panelMode === "airport";
    dom.contactList.hidden = airportMode;
    dom.nearestAirport.hidden = !airportMode;
    dom.contactCount.hidden = airportMode;
    setText(
      dom.contactTitle,
      airportMode ? "AÉROPORT LE PLUS PROCHE" : "CONTACTS PRIORITAIRES",
    );
    setText(dom.panelToggle, airportMode ? "CONTACTS" : "AÉROPORT");
    dom.panelToggle.setAttribute(
      "aria-label",
      airportMode ? "Afficher les contacts" : "Afficher l'aéroport le plus proche",
    );
    renderNearestAirport();
  }

  async function refreshAirportWeather(force = false) {
    const rangeNm = state.config.distNm;
    const ids = [
      ...new Set(
        [
          ...state.visibleAirports
            .filter((airport) => airport.distance <= rangeNm && airport.icao)
            .map((airport) => airport.icao),
          ...(state.nearestAirport?.icao ? [state.nearestAirport.icao] : []),
        ],
      ),
    ].sort();
    const signature = ids.join(",");
    const now = Date.now();

    if (
      !force &&
      signature === state.weatherSignature &&
      now - state.weatherFetchedAt < WEATHER_REFRESH_MS
    ) {
      return;
    }

    state.weatherSignature = signature;
    if (!ids.length) {
      state.airportWeather.clear();
      state.weatherFetchedAt = now;
      return;
    }

    try {
      const response = await fetch(
        `/api/weather/metar?ids=${encodeURIComponent(signature)}`,
        {
          cache: "no-store",
          headers: { Accept: "application/json" },
        },
      );
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const payload = await response.json();
      if (payload && payload.stations && typeof payload.stations === "object") {
        state.airportWeather = new Map(Object.entries(payload.stations));
      }
    } catch {
      // La météo aéroport est optionnelle : le scope reste opérationnel.
    } finally {
      state.weatherFetchedAt = Date.now();
      renderNearestAirport();
    }
  }

  function hideWeatherCard() {
    state.focusedAirport = null;
    if (dom.weatherCard) {
      dom.weatherCard.hidden = true;
    }
  }

  function showWeatherCard(airport) {
    const metar = state.airportWeather.get(airport.icao);
    state.focusedAirport = airport.icao;
    const category = metar?.flight_category || "N/A";
    const color = metar ? weatherColor(metar.flight_category) : WEATHER_COLORS.UNKNOWN;

    setText(dom.weatherCode, airport.iata ? `${airport.iata}/${airport.icao}` : airport.icao);
    setText(dom.weatherName, airport.name || "");
    setText(dom.weatherCategory, category);
    dom.weatherCategory.style.color = color;

    if (metar) {
      setText(dom.weatherWind, windLabel(metar));
      setText(
        dom.weatherVisibility,
        metar.visibility_sm !== null && metar.visibility_sm !== undefined
          ? `${metar.visibility_sm} SM`
          : "—",
      );
      setText(dom.weatherCeiling, ceilingLabel(metar));
      setText(
        dom.weatherTemp,
        metar.temperature_c !== null && metar.temperature_c !== undefined
          ? `${metar.temperature_c}° / ${metar.dewpoint_c ?? "—"}°`
          : "—",
      );
      setText(
        dom.weatherQnh,
        metar.altimeter_hpa !== null && metar.altimeter_hpa !== undefined
          ? `${metar.altimeter_hpa} hPa`
          : "—",
      );
      setText(dom.weatherObserved, formatMetarTime(metar.observed));
      setText(dom.weatherRaw, metar.raw || "—");
    } else {
      for (const node of [
        dom.weatherWind,
        dom.weatherVisibility,
        dom.weatherCeiling,
        dom.weatherTemp,
        dom.weatherQnh,
        dom.weatherObserved,
      ]) {
        setText(node, "—");
      }
      setText(dom.weatherRaw, "METAR indisponible pour cette station.");
    }

    dom.weatherCard.hidden = false;
  }

  function airportAtPointer(clientX, clientY) {
    if (!state.visibleAirports.length) {
      return null;
    }
    const rect = dom.canvas.getBoundingClientRect();
    const px = clientX - rect.left;
    const py = clientY - rect.top;
    const thresholdSq = 18 * 18;
    const rangeNm = Math.max(1, state.config.distNm);
    let best = null;
    let bestDistanceSq = thresholdSq;

    for (const airport of state.visibleAirports) {
      if (airport.distance > rangeNm) {
        continue;
      }
      const distancePx = (airport.distance / rangeNm) * view.radius;
      const x = view.cx + Math.sin(airport.bearingRad) * distancePx;
      const y = view.cy - Math.cos(airport.bearingRad) * distancePx;
      const dx = x - px;
      const dy = y - py;
      const distanceSq = dx * dx + dy * dy;
      if (distanceSq < bestDistanceSq) {
        bestDistanceSq = distanceSq;
        best = airport;
      }
    }
    return best;
  }

  function drawAirports() {
    if (!state.visibleAirports.length) {
      return;
    }

    const rangeNm = Math.max(1, state.config.distNm);
    const { cx, cy, radius: R, width } = view;
    ctx.textBaseline = "middle";

    for (const airport of state.visibleAirports) {
      if (airport.distance > rangeNm) {
        continue;
      }

      const distancePx = (airport.distance / rangeNm) * R;
      const x = cx + Math.sin(airport.bearingRad) * distancePx;
      const y = cy - Math.cos(airport.bearingRad) * distancePx;
      const isLarge = airport.type === "large_airport";
      const size = isLarge ? 3.4 : 2.7;
      const alpha = isLarge ? 0.64 : 0.4;
      const metar = state.airportWeather.get(airport.icao);
      const categoryColor = metar ? weatherColor(metar.flight_category) : null;

      // Pistes : lignes géoréférencées par leurs deux seuils réels.
      const airportRunways = state.runways.get(airport.icao) || [];
      for (const runway of airportRunways) {
        if (!Array.isArray(runway) || runway.length < 6) {
          continue;
        }
        const [leLat, leLon, heLat, heLon, lengthFt, widthFt] = runway;
        const leDistance = distanceNm(
          state.config.lat,
          state.config.lon,
          Number(leLat),
          Number(leLon),
        );
        const heDistance = distanceNm(
          state.config.lat,
          state.config.lon,
          Number(heLat),
          Number(heLon),
        );
        if (leDistance > rangeNm && heDistance > rangeNm) {
          continue;
        }
        const leBearing = bearingDeg(
          state.config.lat,
          state.config.lon,
          Number(leLat),
          Number(leLon),
        );
        const heBearing = bearingDeg(
          state.config.lat,
          state.config.lon,
          Number(heLat),
          Number(heLon),
        );
        const leRadius = clamp(leDistance / rangeNm, 0, 1) * R;
        const heRadius = clamp(heDistance / rangeNm, 0, 1) * R;
        const x1 = cx + Math.sin(leBearing * DEG) * leRadius;
        const y1 = cy - Math.cos(leBearing * DEG) * leRadius;
        const x2 = cx + Math.sin(heBearing * DEG) * heRadius;
        const y2 = cy - Math.cos(heBearing * DEG) * heRadius;
        const runwayWidth = clamp(
          ((Number(widthFt) || 100) / 6076.12 / rangeNm) * R,
          1.5,
          4,
        );
        ctx.lineCap = "round";
        ctx.strokeStyle = "rgba(4, 12, 18, 0.9)";
        ctx.lineWidth = runwayWidth + 1.6;
        ctx.beginPath();
        ctx.moveTo(x1, y1);
        ctx.lineTo(x2, y2);
        ctx.stroke();
        ctx.strokeStyle = "rgba(135, 235, 248, 0.72)";
        ctx.lineWidth = runwayWidth;
        ctx.beginPath();
        ctx.moveTo(x1, y1);
        ctx.lineTo(x2, y2);
        ctx.stroke();

        if (rangeNm <= 10 && runway[6] && runway[7]) {
          const midX = (x1 + x2) / 2;
          const midY = (y1 + y2) / 2;
          ctx.globalAlpha = 0.58;
          ctx.fillStyle = "rgba(155, 245, 255, 0.85)";
          ctx.font = `600 ${Math.max(6.5, view.detailFont - 1.4)}px ${FONT_STACK}`;
          ctx.textAlign = "left";
          ctx.fillText(`${runway[6]}/${runway[7]}`, midX + 4, midY - 5);
          ctx.globalAlpha = 1;
        }
      }

      ctx.strokeStyle = `rgba(86, 224, 240, ${alpha})`;
      ctx.lineWidth = 0.8;
      ctx.strokeRect(x - size, y - size, size * 2, size * 2);
      ctx.beginPath();
      ctx.moveTo(x - 1.2, y);
      ctx.lineTo(x + 1.2, y);
      ctx.moveTo(x, y - 1.2);
      ctx.lineTo(x, y + 1.2);
      ctx.stroke();

      if (categoryColor) {
        ctx.fillStyle = categoryColor;
        ctx.fillRect(x + size - 1.5, y - size - 1.5, 3, 3);
      }

      const showLabel = isLarge || rangeNm <= 100 || airport.distance <= 40;
      if (!showLabel) {
        continue;
      }

      const label = airport.iata
        ? `${airport.iata}/${airport.icao}`
        : airport.icao;
      const labelWidth = label.length * view.detailFont * 0.63;
      let align = x < cx ? "left" : "right";
      if (align === "left" && x + size + 6 + labelWidth > width - 4) {
        align = "right";
      } else if (align === "right" && x - size - 6 - labelWidth < 4) {
        align = "left";
      }
      const tx = x + (align === "left" ? size + 5 : -size - 5);

      ctx.textAlign = align;
      ctx.font = `700 ${Math.max(7.4, view.detailFont - 0.2)}px ${FONT_STACK}`;
      if (categoryColor) {
        ctx.globalAlpha = isLarge ? 0.9 : 0.72;
        ctx.fillStyle = categoryColor;
      } else {
        ctx.globalAlpha = 1;
        ctx.fillStyle = isLarge
          ? "rgba(150, 250, 255, 0.78)"
          : "rgba(104, 232, 243, 0.55)";
      }
      ctx.fillText(label, tx, y - 0.5);
    }

    if (state.focusedAirport) {
      const focused = state.visibleAirports.find(
        (airport) =>
          airport.icao === state.focusedAirport &&
          airport.distance <= rangeNm,
      );
      if (focused) {
        const distancePx = (focused.distance / rangeNm) * R;
        const x = cx + Math.sin(focused.bearingRad) * distancePx;
        const y = cy - Math.cos(focused.bearingRad) * distancePx;
        const size = 11;
        ctx.save();
        ctx.globalAlpha = 0.95;
        ctx.strokeStyle = "#8cf5ff";
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(x - size, y - size + 4);
        ctx.lineTo(x - size, y - size);
        ctx.lineTo(x - size + 4, y - size);
        ctx.moveTo(x + size - 4, y - size);
        ctx.lineTo(x + size, y - size);
        ctx.lineTo(x + size, y - size + 4);
        ctx.moveTo(x - size, y + size - 4);
        ctx.lineTo(x - size, y + size);
        ctx.lineTo(x - size + 4, y + size);
        ctx.moveTo(x + size - 4, y + size);
        ctx.lineTo(x + size, y + size);
        ctx.lineTo(x + size, y + size - 4);
        ctx.stroke();
        ctx.restore();
      }
    }

    ctx.globalAlpha = 1;
    ctx.textAlign = "left";
  }

  /* ========================================================================
     Couche météo : précipitations RainViewer
     ======================================================================== */

  function weatherZoomForRange() {
    // RainViewer limite ses tuiles radar au niveau de zoom 7.
    return 7;
  }

  function getWeatherTile(frame, zoom, tileX, tileY) {
    const key = `${frame.time}:${zoom}:${tileX}:${tileY}`;
    const existing = weatherRadar.tiles.get(key);
    if (existing) {
      return existing;
    }

    const record = {
      image: new Image(),
      canvas: null,
      ready: false,
      failed: false,
    };
    record.image.crossOrigin = "anonymous";
    record.image.decoding = "async";
    record.image.onload = () => {
      // Supprime les faibles échos gris/bruns de la palette Universal Blue :
      // on ne garde que les précipitations suffisamment colorées.
      try {
        const processed = document.createElement("canvas");
        processed.width = 256;
        processed.height = 256;
        const procCtx = processed.getContext("2d");
        procCtx.drawImage(record.image, 0, 0, 256, 256);
        const pixels = procCtx.getImageData(0, 0, 256, 256);
        const data = pixels.data;
        for (let index = 0; index < data.length; index += 4) {
          const alpha = data[index + 3];
          const max = Math.max(data[index], data[index + 1], data[index + 2]);
          const min = Math.min(data[index], data[index + 1], data[index + 2]);
          const chroma = max - min;
          // Élimine les échos faibles beige/gris (dBZ négatifs ou très faibles)
          // tout en conservant les cellules bleues, jaunes, rouges et blanches.
          if (alpha < 40 || (alpha < 175 && chroma < 80)) {
            data[index + 3] = 0;
          }
        }
        procCtx.putImageData(pixels, 0, 0);
        record.canvas = processed;
      } catch {
        record.canvas = null;
      }
      record.ready = true;
      weatherRadar.dirty = true;
    };
    record.image.onerror = () => {
      record.failed = true;
    };
    record.image.src = `${weatherRadar.host}${frame.path}/256/${zoom}/${tileX}/${tileY}/2/1_0.png`;
    weatherRadar.tiles.set(key, record);
    return record;
  }

  function renderWeatherLayer() {
    if (!view.ready || !weatherRadar.host || !weatherRadar.frames.length) {
      weatherRadar.dirty = false;
      return;
    }

    const frame =
      weatherRadar.frames[
        weatherRadar.index % weatherRadar.frames.length
      ];
    if (!frame) {
      weatherRadar.dirty = false;
      return;
    }

    const zoom = weatherZoomForRange();
    if (weatherRadar.zoom !== zoom) {
      weatherRadar.tiles.clear();
      weatherRadar.zoom = zoom;
    }

    if (
      !weatherRadar.canvas ||
      weatherRadar.canvas.width !== view.width ||
      weatherRadar.canvas.height !== view.height
    ) {
      weatherRadar.canvas = document.createElement("canvas");
      weatherRadar.canvas.width = Math.max(1, view.width);
      weatherRadar.canvas.height = Math.max(1, view.height);
    }

    const weatherCtx = weatherRadar.canvas.getContext("2d");
    weatherCtx.clearRect(0, 0, view.width, view.height);

    const latRad = state.config.lat * DEG;
    const worldSize = 256 * Math.pow(2, zoom);
    const metersPerWorldPixel =
      (156543.03392 * Math.cos(latRad)) / Math.pow(2, zoom);
    const rangeMeters = Math.max(1, state.config.distNm) * 1852;
    const worldRange = rangeMeters / metersPerWorldPixel;
    const scale = view.radius / worldRange;
    const centerWorldX = ((state.config.lon + 180) / 360) * worldSize;
    const centerWorldY =
      ((1 -
        Math.log(Math.tan(Math.PI / 4 + latRad / 2)) / Math.PI) /
        2) *
      worldSize;

    const minTileX = Math.floor((centerWorldX - worldRange) / 256);
    const maxTileX = Math.floor((centerWorldX + worldRange) / 256);
    const minTileY = Math.floor((centerWorldY - worldRange) / 256);
    const maxTileY = Math.floor((centerWorldY + worldRange) / 256);
    const tileSize = 256 * scale;

    weatherCtx.save();
    weatherCtx.beginPath();
    weatherCtx.arc(view.cx, view.cy, view.radius, 0, TAU);
    weatherCtx.clip();
    weatherCtx.globalAlpha = 0.56;

    for (let tileX = minTileX; tileX <= maxTileX; tileX += 1) {
      for (let tileY = minTileY; tileY <= maxTileY; tileY += 1) {
        const tile = getWeatherTile(frame, zoom, tileX, tileY);
        if (!tile.ready) {
          continue;
        }
        const x =
          view.cx + (tileX * 256 - centerWorldX) * scale;
        const y =
          view.cy + (tileY * 256 - centerWorldY) * scale;
        weatherCtx.drawImage(tile.canvas || tile.image, x, y, tileSize, tileSize);
      }
    }
    weatherCtx.restore();
    weatherRadar.dirty = false;
  }

  function updateWeatherAnimation(timestamp) {
    if (weatherRadar.frames.length > 1) {
      if (timestamp - weatherRadar.lastFrameAt >= WEATHER_TILE_FRAME_MS) {
        weatherRadar.index =
          (weatherRadar.index + 1) % weatherRadar.frames.length;
        weatherRadar.lastFrameAt = timestamp;
        weatherRadar.dirty = true;
      }
    }
    if (weatherRadar.dirty) {
      renderWeatherLayer();
    }
  }

  async function loadWeatherRadar() {
    try {
      const response = await fetch("/api/weather/radar", {
        cache: "no-store",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const payload = await response.json();
      const frames = Array.isArray(payload.frames) ? payload.frames : [];
      if (!payload.host || !frames.length) {
        weatherRadar.frames = [];
        setText(dom.wxState, "OFF");
        return;
      }

      const signature = frames.map((frame) => frame.time).join(",");
      if (signature !== weatherRadar.signature) {
        weatherRadar.signature = signature;
        weatherRadar.frames = frames;
        weatherRadar.index = 0;
        weatherRadar.tiles.clear();
        weatherRadar.zoom = null;
        weatherRadar.lastFrameAt = performance.now();
      }
      weatherRadar.host = payload.host;
      weatherRadar.dirty = true;
      setText(dom.wxState, "ON");
    } catch {
      setText(dom.wxState, "OFF");
    }
  }

  /* ========================================================================
     Rendu Canvas : cibles
     ======================================================================== */

  function targetPosition(target) {
    const distancePx = target.ratio * view.radius;
    return [
      view.cx + Math.sin(target.bearingRad) * distancePx,
      view.cy - Math.cos(target.bearingRad) * distancePx,
    ];
  }

  function rectanglesOverlap(a, b, padding) {
    return !(
      a.right + padding <= b.left ||
      a.left >= b.right + padding ||
      a.bottom + padding <= b.top ||
      a.top >= b.bottom + padding
    );
  }

  /**
   * Calcule une fois par mise à jour les positions pixels et place les
   * étiquettes sans chevauchement. Les positions ne dépendent pas du balayage :
   * seules les intensités lumineuses sont recalculées à chaque frame.
   */
  function layoutTargets() {
    if (!view.ready || !state.targets.length) {
      return;
    }

    const placed = [];
    const ordered = state.targets
      .slice()
      .sort((a, b) => a.style.priority - b.style.priority || a.distance - b.distance);
    const areaBudget = Math.max(
      10,
      Math.min(22, Math.floor((view.width * view.height) / 26000)),
    );
    const labelBudget = state.targets.length > 35 ? areaBudget : state.targets.length;

    for (const target of state.targets) {
      target.ratio = clamp(target.distance / Math.max(1, state.config.distNm), 0, 1);
      const [x, y] = targetPosition(target);
      target._x = x;
      target._y = y;
    }

    // Les marqueurs voisins sont aussi des obstacles pour les étiquettes.
    const obstacles = state.targets.map((target) => ({
      target,
      left: target._x - 7,
      right: target._x + 7,
      top: target._y - 7,
      bottom: target._y + 7,
    }));

    for (let orderIndex = 0; orderIndex < ordered.length; orderIndex += 1) {
      const target = ordered[orderIndex];
      const showLabel =
        orderIndex < labelBudget ||
        target.style.priority < 2 ||
        target.hex === state.focusedHex;
      target._showLabel = showLabel;
      if (!showLabel) {
        continue;
      }

      const x = target._x;
      const y = target._y;
      const callWidth = target.labelCallsign.length * view.labelFont * 0.66;
      const detailWidth = target.detailLabel.length * view.detailFont * 0.63;
      const width = Math.max(callWidth, detailWidth) + 5;
      const blockHeight = view.labelFont + view.detailFont + 3;
      const preferred = x < view.cx ? "left" : "right";
      const opposite = preferred === "left" ? "right" : "left";
      const verticalOffsets = [
        -4,
        view.labelFont + 4,
        -(blockHeight + 7),
        blockHeight + 10,
        -(blockHeight * 2 + 7),
        blockHeight * 2 + 10,
        -(blockHeight * 3 + 7),
        blockHeight * 3 + 10,
      ];
      const horizontalGaps = [10, 22, 36, 52, 70];
      let best = null;
      let bestScore = Number.POSITIVE_INFINITY;

      // Plusieurs positions candidates sont testées : côté préféré, côté
      // opposé, plusieurs décalages verticaux et horizontaux. Cela évite que
      // les étiquettes se superposent dans les zones denses.
      for (const align of [preferred, opposite]) {
        for (let gapIndex = 0; gapIndex < horizontalGaps.length; gapIndex += 1) {
          for (let offsetIndex = 0; offsetIndex < verticalOffsets.length; offsetIndex += 1) {
            const labelY = y + verticalOffsets[offsetIndex];
            const gap = horizontalGaps[gapIndex];
            const tx = x + (align === "left" ? gap : -gap);
            const left = align === "left" ? tx : tx - width;
            const right = align === "left" ? tx + width : tx;
            const top = labelY - view.labelFont * 0.84;
            const bottom = labelY + view.labelFont + 1 + view.detailFont * 0.84;
            const rect = { left, right, top, bottom };

            let score = offsetIndex * 1.4 + gapIndex * 2.5;
            if (
              left < 3 ||
              right > view.width - 3 ||
              top < 16 ||
              bottom > view.height - 26
            ) {
              score += 1000;
            }
            for (const other of placed) {
              if (rectanglesOverlap(rect, other, 2)) {
                score += 100;
              }
            }
            for (const obstacle of obstacles) {
              if (obstacle.target !== target && rectanglesOverlap(rect, obstacle, 2)) {
                score += 45;
              }
            }

            if (score < bestScore) {
              bestScore = score;
              best = {
                align,
                labelX: tx,
                labelY,
                gap,
                rect,
                leader:
                  gap > 12 ||
                  Math.abs(labelY - (y - 4)) > view.labelFont + view.detailFont + 3,
              };
            }
          }
        }
      }

      // Si aucune position sans chevauchement n'existe, on masque l'étiquette
      // plutôt que de superposer du texte illisible. Les alertes et la cible
      // sélectionnée restent toujours étiquetées.
      if (
        best &&
        bestScore >= 100 &&
        target.style.priority === 2 &&
        target.hex !== state.focusedHex
      ) {
        target._showLabel = false;
        continue;
      }

      if (best) {
        target._labelX = best.labelX;
        target._labelY = best.labelY;
        target._labelAlign = best.align;
        target._labelLeader = best.leader;
        placed.push(best.rect);
      } else {
        target._labelX = x + (preferred === "left" ? 10 : -10);
        target._labelY = y - 4;
        target._labelAlign = preferred;
        target._labelLeader = false;
      }
    }
  }

  function drawTargets(phase) {
    if (!state.targets.length) {
      return;
    }

    // Première passe : halos + symboles orientés.
    for (const target of state.targets) {
      const x = target._x;
      const y = target._y;
      const age = (phase - target.bearingRad + TAU) % TAU;
      const freshness = 1 - age / TAU;
      let intensity = currentRadarTheme.atc
        ? 0.94
        : 0.2 + 0.8 * Math.pow(freshness, 2.4);
      if (state.focusedHex === target.hex) {
        intensity = Math.max(intensity, 0.9);
      }

      target._intensity = intensity;

      // Échos des positions précédentes : traînée de piste et points de
      // rémanence, comme les retours successifs d'un vrai PPI.
      const echoes = target.echoes || [];
      const rangeDenominator = Math.max(1, state.config.distNm);
      if (echoes.length) {
        ctx.globalAlpha = 0.055 + 0.15 * intensity;
        ctx.strokeStyle = target.style.detailColor;
        ctx.lineWidth = 0.65;
        ctx.beginPath();
        ctx.moveTo(x, y);
        for (const echo of echoes) {
          const echoRatio = clamp(echo.distance / rangeDenominator, 0, 1);
          ctx.lineTo(
            view.cx + Math.sin(echo.bearingRad) * echoRatio * view.radius,
            view.cy - Math.cos(echo.bearingRad) * echoRatio * view.radius,
          );
        }
        ctx.stroke();
      }

      for (let index = echoes.length - 1; index >= 0; index -= 1) {
        const echo = echoes[index];
        const echoRatio = clamp(echo.distance / rangeDenominator, 0, 1);
        const echoX = view.cx + Math.sin(echo.bearingRad) * echoRatio * view.radius;
        const echoY = view.cy - Math.cos(echo.bearingRad) * echoRatio * view.radius;
        const echoWeight = (echoes.length - index) / (echoes.length + 1);
        ctx.globalAlpha = (0.045 + echoWeight * 0.17) * (0.4 + 0.6 * intensity);
        ctx.fillStyle = target.style.color;
        ctx.beginPath();
        ctx.arc(echoX, echoY, 1 + echoWeight * 1.5, 0, TAU);
        ctx.fill();
      }

      // Vecteur vitesse sur 30 secondes, comme sur un vrai scope de poursuite.
      if (target.speed !== null && target.speed > 20) {
        const vectorNm = (target.speed * 30) / 3600;
        const vectorLength = clamp(
          (vectorNm / rangeDenominator) * view.radius,
          5,
          28,
        );
        const vectorX = Math.sin(target.trackRad) * vectorLength;
        const vectorY = -Math.cos(target.trackRad) * vectorLength;
        ctx.globalAlpha = 0.13 + 0.24 * intensity;
        ctx.strokeStyle = target.style.detailColor;
        ctx.lineWidth = 0.9;
        ctx.beginPath();
        ctx.moveTo(x, y);
        ctx.lineTo(x + vectorX, y + vectorY);
        ctx.stroke();

        ctx.globalAlpha = 0.28 + 0.4 * intensity;
        ctx.fillStyle = target.style.labelColor;
        ctx.beginPath();
        ctx.arc(x + vectorX, y + vectorY, 1, 0, TAU);
        ctx.fill();
      }

      const haloScale = currentRadarTheme.atc ? 0.55 : 1;
      ctx.globalAlpha = (0.035 + 0.16 * intensity) * (currentRadarTheme.atc ? 0.7 : 1);
      ctx.fillStyle = target.style.color;
      ctx.beginPath();
      ctx.arc(x, y, (5.5 + 6 * intensity) * haloScale, 0, TAU);
      ctx.fill();

      ctx.globalAlpha = (0.12 + 0.2 * intensity) * (currentRadarTheme.atc ? 0.7 : 1);
      ctx.beginPath();
      ctx.arc(x, y, (3.2 + 2.4 * intensity) * haloScale, 0, TAU);
      ctx.fill();

      ctx.save();
      ctx.translate(x, y);
      ctx.rotate(target.trackRad);
      ctx.globalAlpha = 0.35 + 0.65 * intensity;
      ctx.fillStyle = target.style.color;
      ctx.beginPath();
      ctx.moveTo(0, -7.2);
      ctx.lineTo(5.2, 5.2);
      ctx.lineTo(0, 2.3);
      ctx.lineTo(-5.2, 5.2);
      ctx.closePath();
      ctx.fill();
      ctx.restore();

      ctx.globalAlpha = 0.55 + 0.45 * intensity;
      ctx.fillStyle = currentRadarTheme.bright;
      ctx.beginPath();
      ctx.arc(x, y, 1.1, 0, TAU);
      ctx.fill();
    }

    // Traits de rappel discrets pour les étiquettes déplacées.
    ctx.lineWidth = 0.7;
    for (const target of state.targets) {
      if (!target._showLabel || !target._labelLeader) {
        continue;
      }
      const edgeX =
        target._labelAlign === "left" ? target._labelX - 2 : target._labelX + 2;
      const labelMidY = target._labelY + view.labelFont * 0.55;
      ctx.globalAlpha = 0.18 + 0.12 * target._intensity;
      ctx.strokeStyle = target.style.detailColor;
      ctx.beginPath();
      ctx.moveTo(target._x, target._y);
      ctx.lineTo(edgeX, labelMidY);
      ctx.stroke();
    }

    // Deuxième passe : indicatifs.
    ctx.font = `700 ${view.labelFont}px ${FONT_STACK}`;
    ctx.textBaseline = "middle";
    for (const target of state.targets) {
      if (!target._showLabel) {
        continue;
      }
      ctx.textAlign = target._labelAlign;
      ctx.globalAlpha = 0.52 + 0.48 * target._intensity;
      ctx.fillStyle = target.style.labelColor;
      ctx.fillText(target.labelCallsign, target._labelX, target._labelY);
    }

    // Troisième passe : altitude / vitesse.
    ctx.font = `${view.detailFont}px ${FONT_STACK}`;
    ctx.textBaseline = "middle";
    for (const target of state.targets) {
      if (!target._showLabel) {
        continue;
      }
      ctx.textAlign = target._labelAlign;
      ctx.globalAlpha = currentRadarTheme.atc
        ? 0.85
        : 0.42 + 0.42 * target._intensity;
      ctx.fillStyle = target.style.detailColor;
      ctx.fillText(target.detailLabel, target._labelX, target._labelY + view.labelFont + 1);
    }

    ctx.globalAlpha = 1;
    ctx.textAlign = "left";

    // Quatrième passe : marqueur de focus.
    if (state.focusedHex) {
      const focused = state.byHex.get(state.focusedHex);
      if (focused) {
        const x = focused._x;
        const y = focused._y;
        const size = 10;
        ctx.save();
        ctx.strokeStyle = "rgba(223, 255, 232, 0.92)";
        ctx.lineWidth = 1.1;
        ctx.beginPath();
        ctx.moveTo(x - size, y - size + 4);
        ctx.lineTo(x - size, y - size);
        ctx.lineTo(x - size + 4, y - size);
        ctx.moveTo(x + size - 4, y - size);
        ctx.lineTo(x + size, y - size);
        ctx.lineTo(x + size, y - size + 4);
        ctx.moveTo(x - size, y + size - 4);
        ctx.lineTo(x - size, y + size);
        ctx.lineTo(x - size + 4, y + size);
        ctx.moveTo(x + size - 4, y + size);
        ctx.lineTo(x + size, y + size);
        ctx.lineTo(x + size, y + size - 4);
        ctx.stroke();
        ctx.globalAlpha = 0.34;
        ctx.strokeStyle = currentRadarTheme.focus;
        ctx.beginPath();
        ctx.arc(x, y, size + 5, 0, TAU);
        ctx.stroke();
        ctx.restore();
      }
    }
  }

  /* ========================================================================
     Boucle de rendu
     ======================================================================== */

  function drawFrame(phase) {
    const { width, height, dpr, cx, cy, radius: R } = view;

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.globalCompositeOperation = "source-over";
    ctx.globalAlpha = 1;
    ctx.fillStyle = currentRadarTheme.background || "#000000";
    ctx.fillRect(0, 0, width, height);

    if (weatherRadar.canvas) {
      ctx.drawImage(weatherRadar.canvas, 0, 0, width, height);
    }

    if (backgroundCanvas) {
      ctx.drawImage(backgroundCanvas, 0, 0, width, height);
    }

    if (sweepCanvas) {
      ctx.save();
      ctx.beginPath();
      ctx.arc(cx, cy, R, 0, TAU);
      ctx.clip();
      ctx.translate(cx, cy);
      ctx.rotate(phase - Math.PI / 2);
      ctx.globalCompositeOperation = "lighter";
      ctx.drawImage(
        sweepCanvas,
        -sweepCanvasSize / 2,
        -sweepCanvasSize / 2,
        sweepCanvasSize,
        sweepCanvasSize,
      );
      ctx.restore();
    }

    drawAirports();
    drawTargets(phase);
  }

  function updateClock() {
    const now = new Date();
    const second = now.getUTCSeconds();
    if (second === state.lastClockSecond) {
      return;
    }
    state.lastClockSecond = second;

    const time = `${pad2(now.getUTCHours())}:${pad2(now.getUTCMinutes())}:${pad2(second)}`;
    setText(dom.utcTime, time);
    dom.utcTime.dateTime = now.toISOString();
    setText(
      dom.utcDate,
      `${pad2(now.getUTCDate())} ${MONTHS[now.getUTCMonth()]} ${now.getUTCFullYear()}`,
    );
  }

  function frame(timestamp) {
    if (document.hidden) {
      state.frameId = 0;
      return;
    }

    if (!state.sweepOrigin) {
      state.sweepOrigin = timestamp;
    }

    const elapsed = (timestamp - state.sweepOrigin) % SWEEP_PERIOD_MS;
    const phase = (elapsed / SWEEP_PERIOD_MS) * TAU;

    updateClock();
    updateWeatherAnimation(timestamp);
    if (view.ready) {
      drawFrame(phase);
    }

    state.frameId = requestAnimationFrame(frame);
  }

  function startFrameLoop() {
    if (!state.frameId) {
      state.frameId = requestAnimationFrame(frame);
    }
  }

  function stopFrameLoop() {
    if (state.frameId) {
      cancelAnimationFrame(state.frameId);
      state.frameId = 0;
    }
  }

  /* ========================================================================
     Redimensionnement / DPR
     ======================================================================== */

  function resizeCanvas() {
    const rect = dom.scope.getBoundingClientRect();
    const width = Math.floor(rect.width);
    const height = Math.floor(rect.height);
    if (width < 20 || height < 20) {
      return;
    }

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    if (width === view.width && height === view.height && dpr === view.dpr) {
      return;
    }

    view.width = width;
    view.height = height;
    view.dpr = dpr;
    view.cx = width / 2;
    view.cy = height / 2;
    const smallest = Math.min(width, height);
    view.radius = Math.max(40, smallest * 0.5 - (smallest < 380 ? 33 : 37));
    view.labelFont = clamp(view.radius * 0.045, 9.2, 12.5);
    view.detailFont = Math.max(7.5, view.labelFont - 1.6);
    view.ready = true;

    dom.canvas.width = Math.max(1, Math.round(width * dpr));
    dom.canvas.height = Math.max(1, Math.round(height * dpr));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    rebuildBackground();
    rebuildSweep();
    layoutTargets();
    updateHud();
    weatherRadar.canvas = null;
    weatherRadar.dirty = true;
  }

  function setupResizeHandling() {
    let resizeQueued = false;
    const scheduleResize = () => {
      if (resizeQueued) {
        return;
      }
      resizeQueued = true;
      requestAnimationFrame(() => {
        resizeQueued = false;
        resizeCanvas();
      });
    };

    if (typeof ResizeObserver !== "undefined") {
      resizeObserver = new ResizeObserver(scheduleResize);
      resizeObserver.observe(dom.scope);
    }
    window.addEventListener("resize", scheduleResize, { passive: true });
    window.addEventListener("orientationchange", () => {
      window.setTimeout(scheduleResize, 80);
    });
    if (document.fonts && document.fonts.ready) {
      document.fonts.ready.then(scheduleResize).catch(() => {});
    }
  }

  /* ========================================================================
     Interactions Canvas
     ======================================================================== */

  function targetAtPointer(clientX, clientY) {
    const rect = dom.canvas.getBoundingClientRect();
    const x = clientX - rect.left;
    const y = clientY - rect.top;
    const thresholdSq = 22 * 22;
    let best = null;
    let bestDistanceSq = thresholdSq;

    for (const target of state.targets) {
      const dx = target._x - x;
      const dy = target._y - y;
      const distanceSq = dx * dx + dy * dy;
      if (distanceSq < bestDistanceSq) {
        bestDistanceSq = distanceSq;
        best = target;
      }
    }
    return best;
  }

  dom.canvas.addEventListener("pointerdown", (event) => {
    const airport = airportAtPointer(event.clientX, event.clientY);
    if (airport) {
      setFocusedTarget(null);
      showWeatherCard(airport);
      return;
    }
    hideWeatherCard();
    const target = targetAtPointer(event.clientX, event.clientY);
    setFocusedTarget(target ? target.hex : null);
  });

  dom.weatherClose.addEventListener("click", hideWeatherCard);
  dom.panelToggle.addEventListener("click", () => {
    setPanelMode(state.panelMode === "airport" ? "contacts" : "airport");
  });
  dom.nearestWeatherBtn.addEventListener("click", () => {
    if (state.nearestAirport) {
      showWeatherCard(state.nearestAirport);
    }
  });

  /* ========================================================================
     API / polling
     ======================================================================== */

  function parseAircraftHeaders(response) {
    const numberOrNull = (value) => {
      const number = Number.parseFloat(value || "");
      return Number.isFinite(number) ? number : null;
    };

    return {
      stale: response.headers.get("X-Data-Stale") === "1",
      upstream: response.headers.get("X-Upstream"),
      fallback: response.headers.get("X-Fallback") === "1",
      cacheAge: numberOrNull(response.headers.get("X-Cache-Age")),
      lastSuccess: response.headers.get("X-Last-Success"),
      refreshMs: numberOrNull(response.headers.get("X-Refresh-Ms")),
      maxDistNm: numberOrNull(response.headers.get("X-Max-Range-NM")),
      radarStyle: response.headers.get("X-Radar-Style"),
      locationMode: response.headers.get("X-Location-Mode"),
      airportIcao: (response.headers.get("X-Airport-ICAO") || "").toUpperCase() || null,
      lat: numberOrNull(response.headers.get("X-Center-Lat")),
      lon: numberOrNull(response.headers.get("X-Center-Lon")),
    };
  }

  async function poll() {
    if (state.polling || document.hidden) {
      return;
    }

    state.polling = true;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);

    try {
      const requestedRange = Math.round(state.config.maxDistNm);
      const response = await fetch(`/api/aircraft?radius_nm=${requestedRange}`, {
        cache: "no-store",
        headers: { Accept: "application/json" },
        signal: controller.signal,
      });
      const meta = parseAircraftHeaders(response);

      if (!response.ok) {
        let detail = `HTTP ${response.status}`;
        try {
          const payload = await response.json();
          if (payload && typeof payload.detail === "string") {
            detail = payload.detail;
          }
        } catch {
          // La réponse d'erreur n'est pas forcément du JSON.
        }
        throw new Error(detail);
      }

      const payload = await response.json();
      if (!Array.isArray(payload)) {
        throw new Error("Format de flux invalide");
      }

      applyServerMeta(meta);
      applyAircraftList(payload);

      state.failures = 0;
      state.stale = meta.stale;
      updateSource(meta.upstream, meta.fallback);
      setText(dom.lastUpdate, formatUtcStamp(meta.lastSuccess));

      const detailParts = [];
      if (meta.fallback) {
        detailParts.push("SECOURS");
      }
      if (meta.cacheAge !== null) {
        detailParts.push(
          meta.fallback ? `${meta.cacheAge.toFixed(1)}S` : `MAJ ${meta.cacheAge.toFixed(1)}S`,
        );
      }
      setStreamState(
        meta.stale ? "stale" : "live",
        detailParts.length ? detailParts.join(" · ") : "FLUX ACTIF",
      );
    } catch (error) {
      state.failures += 1;
      const timedOut = error && error.name === "AbortError";
      const detail = timedOut
        ? "TIMEOUT API"
        : state.failures > 1
          ? `ÉCHEC ×${state.failures}`
          : "API INJOIGNABLE";
      setStreamState("offline", detail);
      setText(dom.sourceMode, "NO LINK");
      renderContacts();
      if (window.console && typeof window.console.warn === "function") {
        window.console.warn("[radar] flux indisponible:", error);
      }
    } finally {
      window.clearTimeout(timeout);
      state.polling = false;
      schedulePoll();
    }
  }

  function schedulePoll() {
    window.clearTimeout(state.pollTimer);
    if (document.hidden) {
      return;
    }
    state.pollTimer = window.setTimeout(poll, state.config.refreshMs);
  }

  async function loadAirports() {
    try {
      const response = await fetch("/static/data/airports.json", {
        cache: "force-cache",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        return;
      }
      const rows = await response.json();
      if (!Array.isArray(rows)) {
        return;
      }
      state.airports = rows
        .filter(
          (row) =>
            Array.isArray(row) &&
            row.length >= 6 &&
            typeof row[0] === "string" &&
            Number.isFinite(Number(row[4])) &&
            Number.isFinite(Number(row[5])),
        )
        .map((row) => ({
          icao: row[0],
          iata: row[1] || "",
          name: row[2] || "",
          type: row[3] === "large_airport" ? "large_airport" : "medium_airport",
          lat: Number(row[4]),
          lon: Number(row[5]),
        }));
      rebuildAirportGeometry();
    } catch {
      // Les aéroports sont un décor optionnel : le radar reste fonctionnel.
      state.airports = [];
      state.visibleAirports = [];
    }
  }

  async function loadRunways() {
    try {
      const response = await fetch("/static/data/runways.json", {
        cache: "force-cache",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        return;
      }
      const payload = await response.json();
      if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
        return;
      }
      state.runways = new Map(Object.entries(payload));
    } catch {
      state.runways = new Map();
    }
  }

  async function loadConfig() {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 4000);
    try {
      const response = await fetch("/api/config", {
        cache: "no-store",
        headers: { Accept: "application/json" },
        signal: controller.signal,
      });
      if (!response.ok) {
        return;
      }
      const config = await response.json();
      const lat = toFiniteNumber(config.lat);
      const lon = toFiniteNumber(config.lon);
      const distNm = toFiniteNumber(config.dist_nm);
      const maxDistNm = toFiniteNumber(config.max_radius_nm);
      const refreshMs = toFiniteNumber(config.refresh_ms);
      if (typeof config.radar_style === "string") {
        applyRadarStyle(config.radar_style);
      }

      if (lat !== null) {
        state.config.lat = lat;
      }
      if (lon !== null) {
        state.config.lon = lon;
      }
      if (maxDistNm !== null && maxDistNm >= 1) {
        state.config.maxDistNm = Math.min(250, Math.round(maxDistNm));
      }
      if (distNm !== null && distNm >= 1) {
        state.config.distNm = distNm;
      }

      let storedRange = null;
      try {
        storedRange = toFiniteNumber(window.localStorage.getItem(RANGE_STORAGE_KEY));
      } catch {
        storedRange = null;
      }
      if (storedRange !== null) {
        state.config.distNm = storedRange;
      }
      state.config.distNm = clampRange(state.config.distNm);

      if (refreshMs !== null && refreshMs >= 500) {
        state.config.refreshMs = refreshMs;
      }
      if (config.location_mode === "airport" || config.location_mode === "coords") {
        state.config.locationMode = config.location_mode;
      }
      state.config.airportIcao = config.airport_icao
        ? String(config.airport_icao).toUpperCase()
        : null;

      updateHud();
      updateRangeButtons();
      rebuildAirportGeometry();
      weatherRadar.dirty = true;
      if (view.ready) {
        rebuildBackground();
        layoutTargets();
      }
    } catch {
      // Les valeurs par défaut intégrées suffisent si /api/config est absent.
    } finally {
      window.clearTimeout(timeout);
    }
  }

  /* ========================================================================
     Plein écran / maintien de l'écran
     ======================================================================== */

  async function toggleFullscreen() {
    try {
      if (!document.fullscreenElement) {
        try {
          await document.documentElement.requestFullscreen({ navigationUI: "hide" });
        } catch {
          await document.documentElement.requestFullscreen();
        }
      } else {
        await document.exitFullscreen();
      }
    } catch {
      // Morph Browser / QtWebEngine peut ne pas exposer l'API Fullscreen.
    }
  }

  async function requestWakeLock() {
    if (!("wakeLock" in navigator) || wakeLock) {
      return;
    }
    try {
      wakeLock = await navigator.wakeLock.request("screen");
      wakeLock.addEventListener("release", () => {
        wakeLock = null;
      });
    } catch {
      wakeLock = null;
    }
  }

  dom.fullscreenBtn.addEventListener("click", toggleFullscreen);
  for (const button of document.querySelectorAll("[data-range-action]")) {
    button.addEventListener("click", () => {
      shiftRange(button.dataset.rangeAction === "up" ? 1 : -1);
    });
  }
  document.addEventListener("fullscreenchange", () => {
    window.setTimeout(resizeCanvas, 80);
  });
  document.addEventListener("pointerdown", requestWakeLock, { once: true });
  document.addEventListener("contextmenu", (event) => event.preventDefault());

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      window.clearTimeout(state.pollTimer);
      stopFrameLoop();
      return;
    }
    resizeCanvas();
    if (!wakeLock) {
      requestWakeLock();
    }
    startFrameLoop();
    poll();
  });

  /* ========================================================================
     Démarrage
     ======================================================================== */

  setupResizeHandling();
  applyRadarStyle(DEFAULT_CONFIG.radarStyle);
  resizeCanvas();
  updateHud();
  updateRangeButtons();
  let initialPanel = "airport";
  try {
    const storedPanel = window.localStorage.getItem(PANEL_MODE_KEY);
    if (storedPanel === "contacts" || storedPanel === "airport") {
      initialPanel = storedPanel;
    }
  } catch {
    // Mode par défaut.
  }
  setPanelMode(initialPanel);
  setStreamState("connecting", "ACQUISITION");
  startFrameLoop();
  loadAirports();
  loadRunways();
  loadWeatherRadar();
  loadConfig().finally(poll);
  window.setInterval(() => {
    refreshAirportWeather(true);
    loadWeatherRadar();
  }, WEATHER_REFRESH_MS);
})();

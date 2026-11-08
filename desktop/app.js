(() => {
  "use strict";

  const DEG = Math.PI / 180;
  const RANGE_STEPS = [5, 10, 25, 50, 75, 100, 125, 150, 200, 250];
  const POLL_MS = 3000;
  const WEATHER_REFRESH_MS = 5 * 60 * 1000;
  const STATS_REFRESH_MS = 10000;
  const REDUCED_MOTION =
    window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches ?? false;
  const DISTANCE_BANDS = [
    { lower: 0, upper: 5, label: "0–5" },
    { lower: 5, upper: 10, label: "5–10" },
    { lower: 10, upper: 25, label: "10–25" },
    { lower: 25, upper: 50, label: "25–50" },
    { lower: 50, upper: 100, label: "50–100" },
    { lower: 100, upper: Infinity, label: "100+" },
  ];
  const ALTITUDE_BANDS = [
    { key: "ground", label: "SOL" },
    { key: "0-5", label: "0–5k", max: 5000 },
    { key: "5-10", label: "5–10k", max: 10000 },
    { key: "10-20", label: "10–20k", max: 20000 },
    { key: "20-30", label: "20–30k", max: 30000 },
    { key: "30-40", label: "30–40k", max: 40000 },
    { key: "40+", label: "40k+", max: Infinity },
    { key: "unknown", label: "N/C" },
  ];
  const PLANE_SVG =
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2c-.55 0-1 .45-1 1v6.1L4 13v2l7-2.1v5.3l-2 1.5V21l3-1 3 1v-1.3l-2-1.5v-5.3l7 2.1v-2l-7-3.9V3c0-.55-.45-1-1-1z"/></svg>';
  const MONTHS = ["JAN", "FEB", "MAR", "AVR", "MAI", "JUIN", "JUIL", "AOUT", "SEP", "OCT", "NOV", "DEC"];
  const EMERGENCY = new Set(["7500", "7600", "7700"]);
  const MILITARY_RE = /^(?:RCH|REACH|ASCOT|RRR|GAF|IAM|CTM|FAF|COTAM|ARM|NAVY|USAF|PAT|DUKE|HUNT|MAGMA|VIV|CFC|RCAF|PLF|BAF|SUI|MIL|FNY|AAC|RRF|TYPH|HAWK|FALCON)/;
  const DEFAULT_CONFIG = {
    lat: 46.8687,
    lon: 6.9416,
    dist_nm: 50,
    max_radius_nm: 150,
    location_mode: "coords",
    airport_icao: null,
  };
  const CATEGORY_CLASS = {
    VFR: "cat-vfr",
    MVFR: "cat-mvfr",
    IFR: "cat-ifr",
    LIFR: "cat-lifr",
  };

  const dom = {
    rangeSelect: document.getElementById("rangeSelect"),
    weatherToggle: document.getElementById("weatherToggle"),
    runwayToggle: document.getElementById("runwayToggle"),
    stream: document.getElementById("stream"),
    streamState: document.getElementById("streamState"),
    streamDetail: document.getElementById("streamDetail"),
    targetCount: document.getElementById("targetCount"),
    utcTime: document.getElementById("utcTime"),
    utcDate: document.getElementById("utcDate"),
    fullscreenBtn: document.getElementById("fullscreenBtn"),
    mapCenter: document.getElementById("mapCenter"),
    mapAirport: document.getElementById("mapAirport"),
    mapRange: document.getElementById("mapRange"),
    sourceName: document.getElementById("sourceName"),
    lastUpdate: document.getElementById("lastUpdate"),
    opsCount: document.getElementById("opsCount"),
    mapView: document.getElementById("mapView"),
    statsPage: document.getElementById("statsPage"),
    mapViewBtn: document.getElementById("mapViewBtn"),
    statsViewBtn: document.getElementById("statsViewBtn"),
    statCurrent: document.getElementById("statCurrent"),
    statBreakdown: document.getElementById("statBreakdown"),
    statUnique: document.getElementById("statUnique"),
    statSince: document.getElementById("statSince"),
    statRegistrations: document.getElementById("statRegistrations"),
    statRegistrationDetail: document.getElementById("statRegistrationDetail"),
    statPeak: document.getElementById("statPeak"),
    statObservations: document.getElementById("statObservations"),
    statPolls: document.getElementById("statPolls"),
    statAverage: document.getElementById("statAverage"),
    chartSummary: document.getElementById("chartSummary"),
    activityChart: document.getElementById("activityChart"),
    hourlyChart: document.getElementById("hourlyChart"),
    hourlySummary: document.getElementById("hourlySummary"),
    distanceBars: document.getElementById("distanceBars"),
    altitudeBars: document.getElementById("altitudeBars"),
    typeBars: document.getElementById("typeBars"),
    typesTotal: document.getElementById("typesTotal"),
    registrationBars: document.getElementById("registrationBars"),
    registrationPasses: document.getElementById("registrationPasses"),
    registrationPeriod: document.getElementById("registrationPeriod"),
    dailyBars: document.getElementById("dailyBars"),
    antennaMode: document.getElementById("antennaMode"),
    antennaCenter: document.getElementById("antennaCenter"),
    antennaRange: document.getElementById("antennaRange"),
    antennaMax: document.getElementById("antennaMax"),
    antennaArea: document.getElementById("antennaArea"),
    antennaSource: document.getElementById("antennaSource"),
    statsNote: document.getElementById("statsNote"),
    searchInput: document.getElementById("searchInput"),
    filterSelect: document.getElementById("filterSelect"),
    exportBtn: document.getElementById("exportBtn"),
    aircraftRows: document.getElementById("aircraftRows"),
    emptyState: document.getElementById("emptyState"),
    selectedCallsign: document.getElementById("selectedCallsign"),
    selectedType: document.getElementById("selectedType"),
    selectedAlt: document.getElementById("selectedAlt"),
    selectedSpeed: document.getElementById("selectedSpeed"),
    selectedTrack: document.getElementById("selectedTrack"),
    selectedSquawk: document.getElementById("selectedSquawk"),
    selectedDistance: document.getElementById("selectedDistance"),
    selectedBearing: document.getElementById("selectedBearing"),
    selectedPosition: document.getElementById("selectedPosition"),
    latencyLabel: document.getElementById("latencyLabel"),
  };

  const state = {
    center: { lat: DEFAULT_CONFIG.lat, lon: DEFAULT_CONFIG.lon },
    rangeNm: DEFAULT_CONFIG.dist_nm,
    maxRangeNm: DEFAULT_CONFIG.max_radius_nm,
    locationMode: "coords",
    airportIcao: null,
    aircraft: [],
    byHex: new Map(),
    selectedHex: null,
    mainView: "map",
    filteredCount: 0,
    statsSummary: null,
    statsFetchedAt: 0,
    statsRequestSeq: 0,
    registrationDays: 1,
    search: "",
    filter: "all",
    sortKey: "distance",
    sortDir: 1,
    airports: [],
    runways: {},
    airportMarkers: new Map(),
    aircraftMarkers: new Map(),
    metar: new Map(),
    metarSignature: "",
    metarFetchedAt: 0,
    metarController: null,
    metarLoadingSignature: "",
    weather: { host: null, path: null, layer: null },
    map: null,
    airportLayer: null,
    runwayLayer: null,
    aircraftLayer: null,
    rangeCircle: null,
    centerMarker: null,
    polling: false,
    pollTimer: 0,
    pollMs: POLL_MS,
    lastClockSecond: -1,
  };

  let wakeLock = null;

  /* ========================================================================
     Utilitaires
     ======================================================================== */

  const setText = (node, value) => {
    if (node && node.textContent !== String(value)) node.textContent = String(value);
  };

  const pad2 = (value) => String(value).padStart(2, "0");

  const escapeHtml = (value) =>
    String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");

  const numberOrNull = (value) => {
    const number = Number.parseFloat(value);
    return Number.isFinite(number) ? number : null;
  };

  async function fetchWithTimeout(url, options = {}, timeoutMs = 8000) {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
    try {
      return await fetch(url, { ...options, signal: controller.signal });
    } finally {
      window.clearTimeout(timeout);
    }
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

  function cleanCallsign(value) {
    return value ? String(value).replace(/\s+/g, "").trim().toUpperCase() : null;
  }

  function cleanType(value) {
    return value ? String(value).trim().toUpperCase() : null;
  }

  function formatCoordinate(value, positive, negative, integerWidth) {
    const suffix = value >= 0 ? positive : negative;
    const absolute = Math.abs(value);
    const integer = String(Math.floor(absolute)).padStart(integerWidth, "0");
    const decimals = absolute.toFixed(5).split(".")[1];
    return `${integer}.${decimals}${suffix}`;
  }

  function formatAltitude(altitude, onGround) {
    if (onGround) return "GND";
    if (altitude === null) return "—";
    if (altitude >= 18000) {
      return `FL${String(Math.round(altitude / 100)).padStart(3, "0")}`;
    }
    return `${Math.round(altitude).toLocaleString("fr-FR")} ft`;
  }

  function formatSpeed(speed) {
    return speed === null ? "—" : `${Math.round(speed)} kt`;
  }

  function formatUtcStamp(value) {
    const date = value instanceof Date ? value : new Date(value);
    if (Number.isNaN(date.getTime())) return "—";
    return `${pad2(date.getUTCHours())}:${pad2(date.getUTCMinutes())}:${pad2(date.getUTCSeconds())}Z`;
  }

  function formatObservation(value) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return `${pad2(date.getUTCHours())}:${pad2(date.getUTCMinutes())}Z`;
  }

  function setStream(status, detail) {
    dom.stream.dataset.state = status;
    const labels = {
      connecting: "CONNEXION",
      live: "LIVE",
      stale: "DIFFÉRÉ",
      offline: "HORS LIGNE",
    };
    setText(dom.streamState, labels[status] || "CONNEXION");
    setText(dom.streamDetail, detail);
  }

  function updateClock() {
    const now = new Date();
    if (now.getUTCSeconds() === state.lastClockSecond) return;
    state.lastClockSecond = now.getUTCSeconds();
    setText(
      dom.utcTime,
      `${pad2(now.getUTCHours())}:${pad2(now.getUTCMinutes())}:${pad2(now.getUTCSeconds())}`,
    );
    dom.utcTime.dateTime = now.toISOString();
    setText(
      dom.utcDate,
      `${pad2(now.getUTCDate())} ${MONTHS[now.getUTCMonth()]} ${now.getUTCFullYear()}`,
    );
  }

  /* ========================================================================
     Modèle cible
     ======================================================================== */

  function buildAircraft(raw) {
    const hex = String(raw.hex || raw.icao || "").trim().toLowerCase();
    const lat = numberOrNull(raw.lat);
    const lon = numberOrNull(raw.lon);
    if (!hex || lat === null || lon === null) return null;

    const distance = distanceNm(state.center.lat, state.center.lon, lat, lon);
    const bearing = bearingDeg(state.center.lat, state.center.lon, lat, lon);
    const callsign = cleanCallsign(raw.callsign) || hex.toUpperCase();
    const type = cleanType(raw.type);
    const squawk = raw.squawk ? String(raw.squawk) : null;
    const altitude = numberOrNull(raw.alt_baro);
    const onGround = Boolean(raw.on_ground) || raw.alt_baro === "ground";
    const speed = numberOrNull(raw.gs);
    const track = numberOrNull(raw.track);
    const emergency = squawk ? EMERGENCY.has(squawk) : false;
    const military = !emergency && MILITARY_RE.test(callsign);
    const kind = emergency ? "emergency" : military ? "military" : "normal";

    return {
      hex,
      callsign,
      type,
      squawk,
      lat,
      lon,
      altitude,
      onGround,
      speed,
      track,
      distance,
      bearing,
      kind,
      altLabel: formatAltitude(altitude, onGround),
      speedLabel: formatSpeed(speed),
    };
  }

  function updateAircraft(data) {
    const next = [];
    for (const raw of data) {
      if (!raw || typeof raw !== "object") continue;
      const aircraft = buildAircraft(raw);
      if (aircraft) next.push(aircraft);
    }
    state.aircraft = next;
    state.byHex = new Map(next.map((aircraft) => [aircraft.hex, aircraft]));
    if (state.selectedHex && !state.byHex.has(state.selectedHex)) {
      state.selectedHex = null;
    }
    renderAircraftMarkers();
    renderTable();
    renderSelection();
    setText(dom.targetCount, String(next.length).padStart(2, "0"));
    document
      .querySelector(".map-panel")
      .classList.toggle("hide-labels", next.length > 60);
  }

  /* ========================================================================
     Carte Leaflet
     ======================================================================== */

  function initMap() {
    const map = L.map("map", {
      zoomControl: false,
      attributionControl: true,
      preferCanvas: true,
      zoomAnimation: !REDUCED_MOTION,
      fadeAnimation: !REDUCED_MOTION,
      markerZoomAnimation: !REDUCED_MOTION,
    }).setView([state.center.lat, state.center.lon], 8);

    // Fond de carte sombre Esri, sans clé API. Le calque de référence ajoute
    // les noms de lieux au-dessus de la couche météo.
    L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}",
      {
        maxNativeZoom: 16,
        maxZoom: 18,
        zIndex: 1,
        attribution:
          'Tiles &copy; <a href="https://www.esri.com/">Esri</a> — Esri, DeLorme, NAVTEQ',
      },
    ).addTo(map);

    L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}",
      {
        maxNativeZoom: 16,
        maxZoom: 18,
        zIndex: 4,
        opacity: 0.55,
      },
    ).addTo(map);

    L.control.zoom({ position: "bottomright" }).addTo(map);
    L.control.scale({ imperial: false, position: "bottomleft" }).addTo(map);

    state.airportLayer = L.layerGroup().addTo(map);
    state.runwayLayer = L.layerGroup().addTo(map);
    state.aircraftLayer = L.layerGroup().addTo(map);

    state.rangeCircle = L.circle([state.center.lat, state.center.lon], {
      radius: state.rangeNm * 1852,
      color: "#38bdf8",
      weight: 1.2,
      opacity: 0.5,
      fill: false,
      dashArray: "5 8",
      interactive: false,
    }).addTo(map);

    state.centerMarker = L.circleMarker([state.center.lat, state.center.lon], {
      radius: 3,
      color: "#38bdf8",
      weight: 1,
      fillColor: "#38bdf8",
      fillOpacity: 0.9,
      interactive: false,
    }).addTo(map);

    state.map = map;
    map.on("click", () => {
      if (state.selectedHex) selectAircraft(null);
    });
    fitRange();
  }

  function fitRange() {
    if (!state.map || !state.rangeCircle) return;
    state.rangeCircle
      .setLatLng([state.center.lat, state.center.lon])
      .setRadius(state.rangeNm * 1852);
    state.centerMarker.setLatLng([state.center.lat, state.center.lon]);
    state.map.fitBounds(state.rangeCircle.getBounds(), {
      padding: [28, 28],
      maxZoom: 11,
      animate: !REDUCED_MOTION,
    });
  }

  function aircraftIcon(aircraft) {
    const selected = state.selectedHex === aircraft.hex;
    const kindClass =
      aircraft.kind === "emergency"
        ? "is-emergency"
        : aircraft.kind === "military"
          ? "is-military"
          : "";
    const track = aircraft.track ?? aircraft.bearing;
    return L.divIcon({
      className: `aircraft-marker ${kindClass}${selected ? " is-selected" : ""}`,
      html: `<span class="ac-symbol" style="transform:rotate(${track}deg)">${PLANE_SVG}</span><span class="ac-label">${escapeHtml(aircraft.callsign)}</span>`,
      iconSize: [0, 0],
      iconAnchor: [0, 0],
    });
  }

  function renderAircraftMarkers() {
    if (!state.map || !state.aircraftLayer) return;
    const seen = new Set();

    for (const aircraft of state.aircraft) {
      seen.add(aircraft.hex);
      // Le layerGroup Leaflet ne fournit pas d'index : on garde notre Map.
      let marker = state.aircraftMarkers.get(aircraft.hex);
      if (!marker) {
        marker = L.marker([aircraft.lat, aircraft.lon], {
          icon: aircraftIcon(aircraft),
          keyboard: false,
          riseOnHover: true,
        });
        marker.on("click", () => selectAircraft(aircraft.hex, true));
        marker.addTo(state.aircraftLayer);
        state.aircraftMarkers.set(aircraft.hex, marker);
      } else {
        marker.setLatLng([aircraft.lat, aircraft.lon]);
        marker.setIcon(aircraftIcon(aircraft));
      }
      marker.setZIndexOffset(state.selectedHex === aircraft.hex ? 1000 : 0);
    }

    for (const [hex, marker] of state.aircraftMarkers) {
      if (!seen.has(hex)) {
        state.aircraftLayer.removeLayer(marker);
        state.aircraftMarkers.delete(hex);
      }
    }
  }

  /* ========================================================================
     Aéroports, pistes et METAR
     ======================================================================== */

  function airportIcon(airport, metar) {
    const category = metar?.flight_category;
    const categoryClass = CATEGORY_CLASS[category] || "";
    return L.divIcon({
      className: `airport-marker ${categoryClass}`,
      html: `<span class="airport-dot"></span><span class="airport-label">${escapeHtml(airport.iata ? `${airport.iata}/${airport.icao}` : airport.icao)}</span>`,
      iconSize: [0, 0],
      iconAnchor: [0, 0],
    });
  }

  function airportPopup(airport, metar) {
    if (!metar) {
      return `<div class="airport-popup"><h3>${escapeHtml(airport.iata ? `${airport.iata}/${airport.icao}` : airport.icao)}</h3><span class="airport-title">${escapeHtml(airport.name)}</span><span class="airport-title">${airport.distance.toFixed(1)} NM du centre</span><span class="airport-title">METAR indisponible</span></div>`;
    }
    const clouds = (Array.isArray(metar.clouds) ? metar.clouds : [])
      .filter((cloud) => cloud.cover && numberOrNull(cloud.base_ft) !== null)
      .map((cloud) => `${cloud.cover} ${cloud.base_ft} ft`)
      .join(" · ");
    const category = metar.flight_category || "UNKNOWN";
    const color =
      category === "VFR"
        ? "#34d399"
        : category === "MVFR"
          ? "#38bdf8"
          : category === "IFR"
            ? "#f87171"
            : category === "LIFR"
              ? "#d8b4fe"
              : "#7e93a8";
    const wind =
      metar.wind_direction === "VRB"
        ? `VRB / ${metar.wind_speed_kt ?? "—"} kt`
        : metar.wind_direction === null || metar.wind_direction === undefined
          ? `— / ${metar.wind_speed_kt ?? "—"} kt`
          : `${metar.wind_direction}° / ${metar.wind_speed_kt ?? "—"} kt`;
    return `<div class="airport-popup">
      <h3>${escapeHtml(airport.iata ? `${airport.iata}/${airport.icao}` : airport.icao)}</h3>
      <span class="airport-title">${escapeHtml(airport.name)} · ${airport.distance.toFixed(1)} NM</span>
      <span class="airport-category" style="color:${color}">${escapeHtml(category)}</span>
      <dl>
        <dt>Vent</dt><dd>${escapeHtml(wind)}</dd>
        <dt>Visibilité</dt><dd>${escapeHtml(metar.visibility_sm ?? "—")} SM</dd>
        <dt>Plafond</dt><dd>${escapeHtml(clouds || "—")}</dd>
        <dt>Temp.</dt><dd>${escapeHtml(metar.temperature_c ?? "—")}° / ${escapeHtml(metar.dewpoint_c ?? "—")}°</dd>
        <dt>QNH</dt><dd>${escapeHtml(metar.altimeter_hpa ?? "—")} hPa</dd>
        <dt>Observé</dt><dd>${escapeHtml(formatObservation(metar.observed))}</dd>
      </dl>
      <p class="raw">${escapeHtml(metar.raw || "")}</p>
    </div>`;
  }

  function visibleAirports() {
    return state.airports.filter(
      (airport) => airport.distance <= state.rangeNm,
    );
  }

  function renderAirports() {
    if (!state.map || !state.airportLayer) return;
    state.airportLayer.clearLayers();
    state.runwayLayer.clearLayers();
    state.airportMarkers.clear();

    const airports = visibleAirports();
    for (const airport of airports) {
      const metar = state.metar.get(airport.icao);
      const marker = L.marker([airport.lat, airport.lon], {
        icon: airportIcon(airport, metar),
        keyboard: false,
        title: `${airport.icao} — ${airport.name}`,
      });
      marker.bindPopup(airportPopup(airport, metar), {
        maxWidth: 280,
        className: "airport-popup-wrapper",
      });
      marker.addTo(state.airportLayer);
      state.airportMarkers.set(airport.icao, marker);
    }

    if (dom.runwayToggle.checked) {
      for (const airport of airports) {
        const runways = state.runways[airport.icao] || [];
        for (const runway of runways) {
          if (!Array.isArray(runway) || runway.length < 4) continue;
          const lat1 = numberOrNull(runway[0]);
          const lon1 = numberOrNull(runway[1]);
          const lat2 = numberOrNull(runway[2]);
          const lon2 = numberOrNull(runway[3]);
          if (lat1 === null || lon1 === null || lat2 === null || lon2 === null) {
            continue;
          }
          const line = L.polyline(
            [
              [lat1, lon1],
              [lat2, lon2],
            ],
            {
              color: "#7dd3fc",
              weight: 2,
              opacity: 0.5,
              interactive: false,
            },
          );
          line.addTo(state.runwayLayer);
        }
      }
    }

    refreshMetar();
  }

  function updateAirportPopups() {
    for (const airport of visibleAirports()) {
      const marker = state.airportMarkers.get(airport.icao);
      if (!marker) continue;
      const metar = state.metar.get(airport.icao);
      marker.setIcon(airportIcon(airport, metar));
      marker.setPopupContent(airportPopup(airport, metar));
    }
  }

  async function loadAirportData() {
    try {
      const [airportsResponse, runwaysResponse] = await Promise.all([
        fetchWithTimeout("/data/airports.json", { cache: "force-cache" }, 12000),
        fetchWithTimeout("/data/runways.json", { cache: "force-cache" }, 12000),
      ]);
      if (airportsResponse.ok) {
        const rows = await airportsResponse.json();
        if (Array.isArray(rows)) {
          state.airports = rows
            .filter((row) => Array.isArray(row) && row.length >= 6)
            .map((row) => {
              const lat = numberOrNull(row[4]);
              const lon = numberOrNull(row[5]);
              if (!row[0] || lat === null || lon === null) return null;
              return {
                icao: String(row[0]).toUpperCase(),
                iata: row[1] || "",
                name: row[2] || "",
                type: row[3],
                lat,
                lon,
                distance: distanceNm(
                  state.center.lat,
                  state.center.lon,
                  lat,
                  lon,
                ),
                bearing: bearingDeg(
                  state.center.lat,
                  state.center.lon,
                  lat,
                  lon,
                ),
              };
            })
            .filter(Boolean);
        }
      }
      if (runwaysResponse.ok) {
        const payload = await runwaysResponse.json();
        if (payload && typeof payload === "object" && !Array.isArray(payload)) {
          state.runways = payload;
        }
      }
      renderAirports();
    } catch {
      // Aéroports optionnels : nouvelle tentative si le jeu de données n'a
      // jamais pu être chargé.
      window.setTimeout(() => {
        if (!state.airports.length) loadAirportData();
      }, 30000);
    }
  }

  async function refreshMetar(force = false) {
    const ids = [
      ...new Set(
        visibleAirports()
          .map((airport) => airport.icao)
          .filter(Boolean),
      ),
    ].sort();
    const signature = ids.join(",");
    if (
      !force &&
      signature === state.metarSignature &&
      Date.now() - state.metarFetchedAt < WEATHER_REFRESH_MS
    ) {
      return;
    }
    if (signature && signature === state.metarLoadingSignature) return;

    state.metarSignature = signature;
    if (!ids.length) {
      state.metarController?.abort();
      state.metarController = null;
      state.metarLoadingSignature = "";
      state.metar.clear();
      state.metarFetchedAt = Date.now();
      updateAirportPopups();
      return;
    }

    state.metarController?.abort();
    const controller = new AbortController();
    state.metarController = controller;
    state.metarLoadingSignature = signature;
    const timeout = window.setTimeout(() => controller.abort(), 8000);

    try {
      const response = await fetch(
        `/api/weather/metar?ids=${encodeURIComponent(signature)}`,
        { cache: "no-store", signal: controller.signal },
      );
      if (controller !== state.metarController) return;
      if (response.ok) {
        const payload = await response.json();
        if (controller !== state.metarController) return;
        if (payload && payload.stations) {
          state.metar = new Map(Object.entries(payload.stations));
          updateAirportPopups();
        }
      }
    } catch {
      // Le reste de la carte reste utilisable.
    } finally {
      window.clearTimeout(timeout);
      if (controller === state.metarController) {
        state.metarController = null;
        state.metarLoadingSignature = "";
        state.metarFetchedAt = Date.now();
      }
    }
  }

  /* ========================================================================
     Météo radar
     ======================================================================== */

  function latestObservedFrame(frames) {
    if (!Array.isArray(frames) || !frames.length) return null;
    const nowSeconds = Date.now() / 1000;
    let best = null;
    for (const frame of frames) {
      if (!frame?.path) continue;
      const time = numberOrNull(frame.time);
      if (time === null || time > nowSeconds + 60) continue;
      if (!best || time > best.time) best = { frame, time };
    }
    // Si toutes les frames sont des prévisions, on retombe sur la dernière.
    return best ? best.frame : frames[frames.length - 1];
  }

  async function loadWeather() {
    try {
      const response = await fetchWithTimeout(
        "/api/weather/radar",
        { cache: "no-store" },
        10000,
      );
      if (!response.ok) return;
      const payload = await response.json();
      const frame = latestObservedFrame(payload.frames);
      if (!payload.host || !frame?.path) return;
      state.weather.host = payload.host;
      state.weather.path = frame.path;
      applyWeatherLayer();
    } catch {
      // Couche météo optionnelle.
    }
  }

  function applyWeatherLayer() {
    if (!state.map) return;
    if (state.weather.layer) {
      state.map.removeLayer(state.weather.layer);
      state.weather.layer = null;
    }
    if (
      !dom.weatherToggle.checked ||
      !state.weather.host ||
      !state.weather.path
    ) {
      return;
    }
    state.weather.layer = L.tileLayer(
      `${state.weather.host}${state.weather.path}/256/{z}/{x}/{y}/2/1_0.png`,
      {
        opacity: 0.42,
        maxNativeZoom: 7,
        maxZoom: 18,
        zIndex: 3,
        attribution:
          '&copy; <a href="https://www.rainviewer.com/api.html">RainViewer</a>',
      },
    ).addTo(state.map);
  }

  /* ========================================================================
     Table
     ======================================================================== */

  function filteredAircraft() {
    const search = state.search.trim().toLowerCase();
    return state.aircraft.filter((aircraft) => {
      if (state.filter === "air" && aircraft.onGround) return false;
      if (state.filter === "ground" && !aircraft.onGround) return false;
      if (state.filter === "emergency" && aircraft.kind !== "emergency") {
        return false;
      }
      if (!search) return true;
      return (
        aircraft.callsign.toLowerCase().includes(search) ||
        (aircraft.type || "").toLowerCase().includes(search) ||
        (aircraft.squawk || "").includes(search) ||
        aircraft.hex.includes(search)
      );
    });
  }

  function sortedAircraft(aircraft) {
    const key = state.sortKey;
    const direction = state.sortDir;
    const value = (item) => {
      switch (key) {
        case "callsign":
          return item.callsign;
        case "type":
          return item.type || "";
        case "altitude":
          return item.altitude === null ? -1 : item.altitude;
        case "speed":
          return item.speed === null ? -1 : item.speed;
        case "squawk":
          return item.squawk || "";
        case "distance":
        default:
          return item.distance;
      }
    };

    return aircraft.slice().sort((left, right) => {
      const a = value(left);
      const b = value(right);
      if (typeof a === "string" || typeof b === "string") {
        return String(a).localeCompare(String(b)) * direction;
      }
      return (a - b) * direction;
    });
  }

  function renderTable() {
    const list = sortedAircraft(filteredAircraft());
    state.filteredCount = list.length;
    updateOpsCount();

    for (const header of document.querySelectorAll("[data-sort]")) {
      const active = header.dataset.sort === state.sortKey;
      header.dataset.active = String(active);
      header.dataset.dir = active && state.sortDir < 0 ? "desc" : "asc";
      header.setAttribute(
        "aria-sort",
        active ? (state.sortDir < 0 ? "descending" : "ascending") : "none",
      );
    }

    const activeRow = document.activeElement;
    const activeHex =
      activeRow && dom.aircraftRows.contains(activeRow) && activeRow.dataset.hex
        ? activeRow.dataset.hex
        : null;

    const fragment = document.createDocumentFragment();
    for (const aircraft of list) {
      const row = document.createElement("tr");
      row.dataset.hex = aircraft.hex;
      row.tabIndex = 0;
      row.setAttribute(
        "aria-selected",
        String(aircraft.hex === state.selectedHex),
      );
      if (aircraft.hex === state.selectedHex) row.classList.add("is-selected");
      if (aircraft.kind === "emergency") row.classList.add("is-emergency");
      else if (aircraft.kind === "military") row.classList.add("is-military");

      const values = [
        aircraft.callsign,
        aircraft.type || "—",
        aircraft.altLabel,
        aircraft.speedLabel,
        aircraft.squawk || "—",
        aircraft.distance.toFixed(1),
      ];
      for (const value of values) {
        const cell = document.createElement("td");
        cell.textContent = value;
        row.appendChild(cell);
      }

      const select = () => selectAircraft(row.dataset.hex, true);
      row.addEventListener("click", select);
      row.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          select();
        }
      });
      fragment.appendChild(row);
    }

    dom.aircraftRows.replaceChildren(fragment);
    dom.emptyState.classList.toggle("is-visible", list.length === 0);

    // Le tableau est reconstruit à chaque poll : on restitue le focus pour ne
    // pas interrompre la navigation clavier.
    if (activeHex) {
      const restored = [...dom.aircraftRows.children].find(
        (row) => row.dataset.hex === activeHex,
      );
      restored?.focus({ preventScroll: true });
    }
  }

  function renderSelection() {
    const aircraft = state.selectedHex
      ? state.byHex.get(state.selectedHex)
      : null;
    setText(dom.selectedCallsign, aircraft ? aircraft.callsign : "—");
    setText(dom.selectedType, aircraft ? aircraft.type || "—" : "—");
    setText(dom.selectedAlt, aircraft ? aircraft.altLabel : "—");
    setText(dom.selectedSpeed, aircraft ? aircraft.speedLabel : "—");
    setText(
      dom.selectedTrack,
      aircraft && aircraft.track !== null
        ? `${Math.round(aircraft.track)}°`
        : "—",
    );
    setText(dom.selectedSquawk, aircraft ? aircraft.squawk || "—" : "—");
    setText(
      dom.selectedDistance,
      aircraft ? `${aircraft.distance.toFixed(1)} NM` : "—",
    );
    setText(
      dom.selectedBearing,
      aircraft ? `${Math.round(aircraft.bearing)}°` : "—",
    );
    setText(
      dom.selectedPosition,
      aircraft
        ? `${aircraft.lat.toFixed(5)}, ${aircraft.lon.toFixed(5)}`
        : "—",
    );
  }

  /* ========================================================================
     Statistiques serveur de l'antenne
     ======================================================================== */

  function formatUtcTime(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "—";
    return `${pad2(date.getUTCHours())}:${pad2(date.getUTCMinutes())}`;
  }

  function dayLabel(day) {
    const parts = String(day || "").split("-");
    return parts.length === 3 ? `${parts[2]}/${parts[1]}` : String(day || "—");
  }

  function ensureActivityChart() {
    const chart = dom.activityChart;
    if (!chart || chart.getAttribute("data-ready") === "1") return;
    const ns = "http://www.w3.org/2000/svg";
    const fragment = document.createDocumentFragment();
    for (const y of [16, 32, 48]) {
      const line = document.createElementNS(ns, "line");
      line.setAttribute("class", "chart-grid");
      line.setAttribute("x1", "0");
      line.setAttribute("x2", "240");
      line.setAttribute("y1", String(y));
      line.setAttribute("y2", String(y));
      fragment.appendChild(line);
    }
    for (let index = 0; index < 60; index += 1) {
      const bar = document.createElementNS(ns, "rect");
      bar.setAttribute("class", "chart-bar");
      bar.setAttribute("x", String(index * 4));
      bar.setAttribute("y", "64");
      bar.setAttribute("width", "3");
      bar.setAttribute("height", "0");
      fragment.appendChild(bar);
    }
    chart.replaceChildren(fragment);
    chart.setAttribute("data-ready", "1");
  }

  function chartBuckets() {
    const summary = state.statsSummary;
    const currentMinute = Math.floor(Date.now() / 60000);
    const buckets = [];
    if (summary?.enabled && Array.isArray(summary.recent)) {
      const byMinute = new Map();
      for (const entry of summary.recent) {
        const minute = Math.floor(Number(entry.ts) / 60);
        if (Number.isFinite(minute)) byMinute.set(minute, entry);
      }
      for (let offset = 59; offset >= 0; offset -= 1) {
        const minute = currentMinute - offset;
        const entry = byMinute.get(minute);
        buckets.push({
          current: offset === 0,
          samples: Number.isFinite(entry?.samples) ? entry.samples : 0,
          average: Number.isFinite(entry?.average) ? entry.average : 0,
          peak: Number.isFinite(entry?.peak) ? entry.peak : 0,
        });
      }
      return buckets;
    }
    for (let offset = 59; offset >= 0; offset -= 1) {
      buckets.push({
        current: offset === 0,
        samples: 0,
        average: 0,
        peak: 0,
      });
    }
    return buckets;
  }

  function renderActivityChart() {
    ensureActivityChart();
    const chart = dom.activityChart;
    if (!chart) return;
    const bars = chart.querySelectorAll(".chart-bar");
    const buckets = chartBuckets();
    const maximum = Math.max(1, ...buckets.map((bucket) => bucket.average));
    let peak = 0;
    let activeAverage = 0;
    let activeMinutes = 0;

    buckets.forEach((bucket, index) => {
      const bar = bars[index];
      if (!bar) return;
      const height =
        bucket.average > 0
          ? Math.max(1, (bucket.average / maximum) * 58)
          : 0;
      bar.setAttribute("y", String(64 - height));
      bar.setAttribute("height", String(height));
      bar.classList.toggle("is-empty", height === 0);
      bar.classList.toggle("is-current", bucket.current);
      peak = Math.max(peak, bucket.peak);
      if (bucket.samples > 0) {
        activeAverage += bucket.average;
        activeMinutes += 1;
      }
    });

    const average = activeMinutes > 0 ? activeAverage / activeMinutes : 0;
    setText(
      dom.chartSummary,
      peak > 0 ? `PIC ${peak} · MOY ${average.toFixed(1)}` : "—",
    );
    chart.setAttribute(
      "aria-label",
      `Activité des 60 dernières minutes, pic ${peak}, moyenne ${average.toFixed(1)}`,
    );
  }

  function ensureHourlyChart() {
    const chart = dom.hourlyChart;
    if (!chart || chart.getAttribute("data-ready") === "1") return;
    const ns = "http://www.w3.org/2000/svg";
    const fragment = document.createDocumentFragment();
    for (const y of [16, 32, 48]) {
      const line = document.createElementNS(ns, "line");
      line.setAttribute("class", "chart-grid");
      line.setAttribute("x1", "0");
      line.setAttribute("x2", "240");
      line.setAttribute("y1", String(y));
      line.setAttribute("y2", String(y));
      fragment.appendChild(line);
    }
    for (let index = 0; index < 24; index += 1) {
      const bar = document.createElementNS(ns, "rect");
      bar.setAttribute("class", "chart-bar");
      bar.setAttribute("x", String(index * 10 + 1));
      bar.setAttribute("y", "64");
      bar.setAttribute("width", "8");
      bar.setAttribute("height", "0");
      fragment.appendChild(bar);
    }
    chart.replaceChildren(fragment);
    chart.setAttribute("data-ready", "1");
  }

  function renderHourlyChart() {
    ensureHourlyChart();
    const chart = dom.hourlyChart;
    if (!chart) return;
    const bars = chart.querySelectorAll(".chart-bar");
    const summary = state.statsSummary;
    const hours =
      summary?.enabled && Array.isArray(summary.hours) ? summary.hours : [];
    const maximum = Math.max(
      1,
      ...hours.map((hour) => (Number(hour.samples) > 0 ? Number(hour.average) || 0 : 0)),
    );
    const currentHour = new Date().getUTCHours();
    let peak = 0;
    let activeAverage = 0;
    let activeHours = 0;

    bars.forEach((bar, index) => {
      const hour = hours[index];
      const samples = Number(hour?.samples || 0);
      const average = samples > 0 ? Number(hour.average) || 0 : 0;
      const height = average > 0 ? Math.max(1, (average / maximum) * 58) : 0;
      bar.setAttribute("y", String(64 - height));
      bar.setAttribute("height", String(height));
      bar.classList.toggle("is-empty", height === 0);
      bar.classList.toggle("is-current", index === currentHour);
      if (samples > 0) {
        peak = Math.max(peak, Number(hour.peak) || 0);
        activeAverage += average;
        activeHours += 1;
      }
    });

    const average = activeHours > 0 ? activeAverage / activeHours : 0;
    setText(
      dom.hourlySummary,
      peak > 0 ? `PIC ${peak} · MOY ${average.toFixed(1)}` : "—",
    );
    chart.setAttribute(
      "aria-label",
      `Activité horaire du jour, pic ${peak}, moyenne ${average.toFixed(1)}`,
    );
  }

  function renderBarRows(container, rows) {
    if (!container) return;
    if (!rows.length) {
      const empty = document.createElement("p");
      empty.className = "bar-empty";
      empty.textContent = "AUCUNE DONNÉE";
      container.replaceChildren(empty);
      return;
    }
    const maximum = Math.max(1, ...rows.map((row) => row.count));
    const fragment = document.createDocumentFragment();
    for (const row of rows) {
      const element = document.createElement("div");
      element.className = "bar-row";
      const label = document.createElement("span");
      label.className = "bar-label";
      label.textContent = row.label;
      const track = document.createElement("span");
      track.className = "bar-track";
      const fill = document.createElement("span");
      fill.className = "bar-fill";
      fill.style.width = `${(row.count / maximum) * 100}%`;
      track.appendChild(fill);
      const value = document.createElement("span");
      value.className = "bar-value";
      value.textContent = String(row.count);
      element.append(label, track, value);
      fragment.appendChild(element);
    }
    container.replaceChildren(fragment);
  }

  function distanceBandRows() {
    const bands = DISTANCE_BANDS.filter(
      (band, index) => index === 0 || band.lower < state.rangeNm,
    );
    const counts = new Map(bands.map((band) => [band.label, 0]));
    for (const aircraft of state.aircraft) {
      const band =
        bands.find((candidate) => aircraft.distance <= candidate.upper) ||
        bands[bands.length - 1];
      if (band) counts.set(band.label, counts.get(band.label) + 1);
    }
    return bands.map((band) => ({
      label: band.label,
      count: counts.get(band.label) || 0,
    }));
  }

  function altitudeBandRows() {
    const counts = new Map(ALTITUDE_BANDS.map((band) => [band.label, 0]));
    for (const aircraft of state.aircraft) {
      let label = "N/C";
      if (aircraft.onGround) {
        label = "SOL";
      } else if (aircraft.altitude !== null) {
        const band = ALTITUDE_BANDS.find(
          (candidate) =>
            Number.isFinite(candidate.max) && aircraft.altitude <= candidate.max,
        );
        if (band) label = band.label;
      }
      counts.set(label, (counts.get(label) || 0) + 1);
    }
    return ALTITUDE_BANDS.map((band) => ({
      label: band.label,
      count: counts.get(band.label) || 0,
    }));
  }

  function typeBandRows() {
    const counts = new Map();
    for (const aircraft of state.aircraft) {
      const label = aircraft.type || "INCONNU";
      counts.set(label, (counts.get(label) || 0) + 1);
    }
    const entries = [...counts.entries()].sort(
      (left, right) => right[1] - left[1] || left[0].localeCompare(right[0]),
    );
    const rows = entries
      .slice(0, 5)
      .map(([label, count]) => ({ label, count }));
    if (entries.length > 5) {
      rows.push({
        label: "AUTRES",
        count: entries.slice(5).reduce((sum, entry) => sum + entry[1], 0),
      });
    }
    setText(dom.typesTotal, String(entries.length));
    return rows;
  }

  function registrationRows() {
    const summary = state.statsSummary;
    if (!summary?.enabled || !Array.isArray(summary.top_registrations)) {
      return [];
    }
    return summary.top_registrations.map((item) => ({
      label: item.registration || "—",
      count: Number(item.passes ?? item.observations) || 0,
    }));
  }

  function registrationPeriodLabel(days) {
    if (days >= 180) return "180 j";
    if (days >= 30) return "30 j";
    if (days >= 7) return "7 j";
    return "aujourd'hui";
  }

  function renderDailyBars() {
    const summary = state.statsSummary;
    const rows =
      summary?.enabled && Array.isArray(summary.daily)
        ? summary.daily.map((item) => ({
            label: dayLabel(item.day),
            count: Number(item.unique) || 0,
          }))
        : [];
    renderBarRows(dom.dailyBars, rows);
  }

  function updateStatsAntenna() {
    const range = Number.isFinite(state.rangeNm) ? state.rangeNm : 0;
    const areaKm2 = Math.PI * (range * 1.852) ** 2;
    setText(
      dom.antennaCenter,
      `${formatCoordinate(state.center.lat, "N", "S", 2)} ${formatCoordinate(state.center.lon, "E", "W", 3)}`,
    );
    setText(dom.antennaRange, String(Math.round(range)));
    setText(dom.antennaMax, String(Math.round(state.maxRangeNm)));
    setText(
      dom.antennaArea,
      `${Math.round(areaKm2).toLocaleString("fr-FR")} km²`,
    );
    setText(
      dom.antennaMode,
      state.locationMode === "airport" && state.airportIcao
        ? `AÉROPORT ${state.airportIcao}`
        : "GPS",
    );
    setText(dom.antennaSource, dom.sourceName.textContent || "—");
  }

  function renderStats() {
    const summary = state.statsSummary;
    const server = summary?.enabled ? summary : null;
    const current = state.aircraft.length;
    let airborne = 0;
    let ground = 0;
    let emergency = 0;
    for (const aircraft of state.aircraft) {
      if (aircraft.onGround) ground += 1;
      else airborne += 1;
      if (aircraft.kind === "emergency") emergency += 1;
    }

    setText(dom.statCurrent, pad2(current));
    setText(
      dom.statBreakdown,
      `${airborne} en vol · ${ground} au sol${emergency ? ` · ${emergency} urgence${emergency > 1 ? "s" : ""}` : ""}`,
    );
    setText(dom.statUnique, String(server?.unique ?? 0));
    setText(
      dom.statSince,
      server?.first_ts
        ? `depuis ${formatUtcTime(server.first_ts * 1000)} UTC`
        : "depuis —",
    );
    const registrationDays = Number(
      server?.registration_days ?? state.registrationDays ?? 1,
    );
    setText(
      dom.statRegistrations,
      String(Number(server?.registrations_period ?? server?.registrations ?? 0)),
    );
    const registrationPasses = Number(server?.registration_passes ?? 0);
    setText(
      dom.statRegistrationDetail,
      server
        ? `${registrationPasses.toLocaleString("fr-FR")} passages · ` +
          registrationPeriodLabel(registrationDays)
        : "aujourd'hui",
    );
    setText(
      dom.registrationPasses,
      `${registrationPasses.toLocaleString("fr-FR")} PASSAGES`,
    );
    setText(dom.statPeak, pad2(server?.peak ?? 0));
    setText(
      dom.statObservations,
      Number(server?.observations ?? 0).toLocaleString("fr-FR"),
    );
    const samples = Number(server?.samples ?? 0);
    setText(
      dom.statPolls,
      `${samples.toLocaleString("fr-FR")} relevé${samples > 1 ? "s" : ""}`,
    );
    setText(
      dom.statAverage,
      server ? Number(server.average ?? 0).toFixed(1) : "0.0",
    );

    renderActivityChart();
    renderHourlyChart();
    renderBarRows(dom.distanceBars, distanceBandRows());
    renderBarRows(dom.altitudeBars, altitudeBandRows());
    renderBarRows(dom.typeBars, typeBandRows());
    renderBarRows(dom.registrationBars, registrationRows());
    renderDailyBars();
    updateStatsAntenna();
    updateOpsCount();
    if (summary) {
      const available = Boolean(summary.enabled);
      setText(
        dom.statsNote,
        available
          ? "Compteurs serveur liés au centre et à la portée affichée."
          : "Statistiques serveur indisponibles.",
      );
      dom.statsNote?.classList.toggle("is-off", !available);
    }
  }

  function updateOpsCount() {
    if (!dom.opsCount) return;
    setText(
      dom.opsCount,
      `${pad2(state.filteredCount)}/${pad2(state.aircraft.length)}`,
    );
  }

  function setMainView(view) {
    const statsActive = view === "stats";
    state.mainView = statsActive ? "stats" : "map";
    dom.mapView.hidden = statsActive;
    dom.statsPage.hidden = !statsActive;
    dom.mapViewBtn.classList.toggle("is-active", !statsActive);
    dom.statsViewBtn.classList.toggle("is-active", statsActive);
    dom.mapViewBtn.setAttribute("aria-pressed", String(!statsActive));
    dom.statsViewBtn.setAttribute("aria-pressed", String(statsActive));
    document.querySelector(".desktop").dataset.view = state.mainView;
    if (statsActive) {
      renderStats();
      loadServerStats();
    } else {
      window.setTimeout(() => {
        state.map?.invalidateSize();
        fitRange();
      }, 40);
    }
  }

  async function loadServerStats() {
    const requestSeq = (state.statsRequestSeq || 0) + 1;
    state.statsRequestSeq = requestSeq;
    const requestedRange = Math.round(state.rangeNm);
    try {
      const response = await fetchWithTimeout(
        `/api/stats/summary?days=7&minutes=60&range_nm=${requestedRange}` +
          `&registration_days=${state.registrationDays}`,
        { cache: "no-store" },
        8000,
      );
      if (!response.ok) return;
      const payload = await response.json();
      if (requestSeq !== state.statsRequestSeq) return;
      if (payload && typeof payload === "object") {
        state.statsSummary = payload;
        state.statsFetchedAt = Date.now();
        renderStats();
      }
    } catch {
      // Le dernier résumé connu reste affiché, avec un état explicite si
      // l'API est momentanément indisponible.
      if (requestSeq === state.statsRequestSeq) {
        setText(dom.statsNote, "Statistiques serveur indisponibles.");
        dom.statsNote?.classList.add("is-off");
      }
    }
  }

  function initStatsView() {
    ensureActivityChart();
    dom.mapViewBtn.addEventListener("click", () => setMainView("map"));
    dom.statsViewBtn.addEventListener("click", () => setMainView("stats"));
    if (dom.registrationPeriod) {
      state.registrationDays = Math.max(
        1,
        Number(dom.registrationPeriod.value) || 1,
      );
      dom.registrationPeriod.addEventListener("change", () => {
        state.registrationDays = Math.max(
          1,
          Number(dom.registrationPeriod.value) || 1,
        );
        loadServerStats();
      });
    }
    setMainView("map");
    renderStats();
    loadServerStats();
  }

  function selectAircraft(hex, pan = false) {
    state.selectedHex = hex;
    renderAircraftMarkers();
    renderTable();
    renderSelection();
    if (hex && pan && state.map) {
      const aircraft = state.byHex.get(hex);
      if (aircraft) {
        state.map.panTo([aircraft.lat, aircraft.lon], {
          animate: !REDUCED_MOTION,
        });
      }
    }
  }

  function csvCell(value) {
    let text = String(value);
    // Neutralise les formules tableur sans transformer les nombres négatifs
    // (longitudes) en texte.
    if (/^[=+@]/.test(text) || (/^-/.test(text) && !/^-?\d+(?:\.\d+)?$/.test(text))) {
      text = `'${text}`;
    }
    return `"${text.replace(/"/g, '""')}"`;
  }

  function exportCsv() {
    const list = sortedAircraft(filteredAircraft());
    if (!list.length) return;
    const header = [
      "hex",
      "callsign",
      "type",
      "alt_baro_ft",
      "ground_speed_kt",
      "track_deg",
      "squawk",
      "distance_nm",
      "bearing_deg",
      "lat",
      "lon",
      "on_ground",
    ];
    const lines = [header.join(",")];
    for (const aircraft of list) {
      lines.push(
        [
          aircraft.hex,
          aircraft.callsign,
          aircraft.type || "",
          aircraft.altitude ?? "",
          aircraft.speed ?? "",
          aircraft.track ?? "",
          aircraft.squawk || "",
          aircraft.distance.toFixed(3),
          aircraft.bearing.toFixed(1),
          aircraft.lat,
          aircraft.lon,
          aircraft.onGround ? "1" : "0",
        ]
          .map(csvCell)
          .join(","),
      );
    }
    const blob = new Blob([lines.join("\n")], {
      type: "text/csv;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    const now = new Date();
    link.href = url;
    link.download = `radardesk-desktop-${now.toISOString().slice(0, 19).replace(/:/g, "-")}Z.csv`;
    link.style.display = "none";
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  /* ========================================================================
     Configuration, polling et en-têtes
     ======================================================================== */

  function populateRanges() {
    const steps = RANGE_STEPS.filter((value) => value <= state.maxRangeNm);
    if (!steps.length || steps[steps.length - 1] !== state.maxRangeNm) {
      steps.push(state.maxRangeNm);
    }
    const unique = [...new Set(steps)].sort((a, b) => a - b);
    const fragment = document.createDocumentFragment();
    for (const value of unique) {
      const option = document.createElement("option");
      option.value = String(value);
      option.textContent = `${value} NM`;
      fragment.appendChild(option);
    }
    dom.rangeSelect.replaceChildren(fragment);
    const selected = unique.reduce(
      (best, value) =>
        Math.abs(value - state.rangeNm) < Math.abs(best - state.rangeNm)
          ? value
          : best,
      unique[0],
    );
    state.rangeNm = selected;
    dom.rangeSelect.value = String(selected);
  }

  function updateHud() {
    setText(dom.mapCenter, `${formatCoordinate(state.center.lat, "N", "S", 2)} ${formatCoordinate(state.center.lon, "E", "W", 3)}`);
    setText(dom.mapRange, String(Math.round(state.rangeNm)));
    setText(
      dom.mapAirport,
      state.locationMode === "airport" && state.airportIcao
        ? `AÉROPORT ${state.airportIcao}`
        : "COORDONNÉES GPS",
    );
    updateStatsAntenna();
  }

  function applyCenter(center) {
    state.center = center;
    if (!state.map) return;
    fitRange();
    updateHud();
    // Recalcule les distances aéroports.
    state.airports = state.airports.map((airport) => ({
      ...airport,
      distance: distanceNm(center.lat, center.lon, airport.lat, airport.lon),
      bearing: bearingDeg(center.lat, center.lon, airport.lat, airport.lon),
    }));
    renderAirports();
    loadServerStats();
  }

  function setRange(value) {
    state.rangeNm = value;
    if (dom.rangeSelect.value !== String(value)) {
      dom.rangeSelect.value = String(value);
    }
    fitRange();
    updateHud();
    renderAirports();
    loadServerStats();
    if (!state.polling) {
      window.clearTimeout(state.pollTimer);
      poll();
    }
  }

  async function loadConfig() {
    try {
      const response = await fetchWithTimeout("/api/config", { cache: "no-store" });
      if (!response.ok) throw new Error();
      const config = await response.json();
      const lat = numberOrNull(config.lat);
      const lon = numberOrNull(config.lon);
      if (lat !== null && lon !== null) {
        state.center = { lat, lon };
      }
      state.maxRangeNm = Math.min(
        250,
        Math.max(5, numberOrNull(config.max_radius_nm) || 150),
      );
      state.rangeNm = Math.min(
        state.maxRangeNm,
        Math.max(5, numberOrNull(config.dist_nm) || 50),
      );
      state.locationMode = config.location_mode || "coords";
      state.airportIcao = config.airport_icao || null;
      const refreshMs = numberOrNull(config.refresh_ms);
      if (refreshMs !== null) {
        state.pollMs = Math.min(60000, Math.max(500, refreshMs));
      }
      populateRanges();
    } catch {
      populateRanges();
    }
    updateHud();
  }

  async function poll() {
    if (state.polling) return;
    state.polling = true;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 8000);
    const started = performance.now();

    try {
      const response = await fetch(
        `/api/aircraft?radius_nm=${Math.round(state.rangeNm)}`,
        {
          cache: "no-store",
          headers: { Accept: "application/json" },
          signal: controller.signal,
        },
      );
      if (!response.ok) throw new Error(`HTTP ${response.status}`);

      const payload = await response.json();
      if (!Array.isArray(payload)) throw new Error("Flux invalide");

      const upstream = response.headers.get("X-Upstream");
      const stale = response.headers.get("X-Data-Stale") === "1";
      const fallback = response.headers.get("X-Fallback") === "1";
      const cacheAge = numberOrNull(response.headers.get("X-Cache-Age"));
      const centerLat = numberOrNull(response.headers.get("X-Center-Lat"));
      const centerLon = numberOrNull(response.headers.get("X-Center-Lon"));
      const maxRange = numberOrNull(response.headers.get("X-Max-Range-NM"));
      const refreshMs = numberOrNull(response.headers.get("X-Refresh-Ms"));
      const locationMode = response.headers.get("X-Location-Mode");
      const airportIcao = response.headers.get("X-Airport-ICAO");

      if (refreshMs !== null) {
        state.pollMs = Math.min(60000, Math.max(500, refreshMs));
      }

      if (centerLat !== null && centerLon !== null) {
        const changed =
          Math.abs(centerLat - state.center.lat) > 1e-7 ||
          Math.abs(centerLon - state.center.lon) > 1e-7;
        if (changed) {
          applyCenter({ lat: centerLat, lon: centerLon });
        }
      }
      if (maxRange !== null) {
        const nextMax = Math.min(250, Math.max(5, Math.round(maxRange)));
        if (nextMax !== state.maxRangeNm) {
          state.maxRangeNm = nextMax;
          const previousRange = state.rangeNm;
          populateRanges();
          if (state.rangeNm !== previousRange) {
            setRange(state.rangeNm);
          }
        }
      }
      state.locationMode = locationMode || state.locationMode;
      state.airportIcao = airportIcao || null;
      updateHud();

      updateAircraft(payload);
      setText(dom.sourceName, (upstream || "—").toUpperCase());
      setText(
        dom.lastUpdate,
        response.headers.get("X-Last-Success")
          ? `MAJ ${formatUtcStamp(response.headers.get("X-Last-Success"))}`
          : "—",
      );
      setText(
        dom.latencyLabel,
        `LATENCE ${Math.round(performance.now() - started)} MS`,
      );
      const detailParts = [];
      if (fallback) detailParts.push("SECOURS");
      if (upstream) detailParts.push(upstream);
      if (cacheAge !== null) detailParts.push(`${cacheAge.toFixed(1)} S`);
      setStream(
        stale ? "stale" : "live",
        detailParts.length ? detailParts.join(" · ") : "FLUX ACTIF",
      );
    } catch (error) {
      setStream(
        "offline",
        error?.name === "AbortError" ? "TIMEOUT API" : "API INJOIGNABLE",
      );
    } finally {
      window.clearTimeout(timeout);
      state.polling = false;
      schedulePoll();
    }
  }

  function schedulePoll() {
    window.clearTimeout(state.pollTimer);
    if (document.hidden) return;
    state.pollTimer = window.setTimeout(poll, state.pollMs);
  }

  /* ========================================================================
     Plein écran / horloge / événements
     ======================================================================== */

  async function toggleFullscreen() {
    try {
      if (!document.fullscreenElement) {
        await document.documentElement.requestFullscreen();
      } else {
        await document.exitFullscreen();
      }
    } catch {
      // API Fullscreen indisponible.
    }
  }

  async function requestWakeLock() {
    if (!("wakeLock" in navigator) || wakeLock) return;
    try {
      wakeLock = await navigator.wakeLock.request("screen");
      wakeLock.addEventListener("release", () => {
        wakeLock = null;
      });
    } catch {
      wakeLock = null;
    }
  }

  dom.rangeSelect.addEventListener("change", () => {
    setRange(Number(dom.rangeSelect.value));
  });
  dom.weatherToggle.addEventListener("change", () => {
    if (dom.weatherToggle.checked && !state.weather.path) loadWeather();
    applyWeatherLayer();
  });
  dom.runwayToggle.addEventListener("change", renderAirports);
  dom.searchInput.addEventListener("input", () => {
    state.search = dom.searchInput.value;
    renderTable();
  });
  dom.filterSelect.addEventListener("change", () => {
    state.filter = dom.filterSelect.value;
    renderTable();
  });
  dom.exportBtn.addEventListener("click", exportCsv);
  dom.fullscreenBtn.addEventListener("click", toggleFullscreen);
  document.addEventListener("pointerdown", requestWakeLock, { once: true });

  function toggleSort(header) {
    const key = header.dataset.sort;
    if (state.sortKey === key) state.sortDir *= -1;
    else {
      state.sortKey = key;
      state.sortDir = key === "altitude" || key === "speed" ? -1 : 1;
    }
    renderTable();
  }

  for (const header of document.querySelectorAll("[data-sort]")) {
    header.tabIndex = 0;
    header.addEventListener("click", () => toggleSort(header));
    header.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        toggleSort(header);
      }
    });
  }

  document.addEventListener("fullscreenchange", () => {
    window.setTimeout(() => state.map?.invalidateSize(), 150);
  });

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      requestWakeLock();
      poll();
      refreshMetar(true);
      loadWeather();
      loadServerStats();
    }
  });

  window.setInterval(updateClock, 250);
  window.setInterval(() => {
    if (document.hidden) return;
    refreshMetar(true);
    loadWeather();
  }, WEATHER_REFRESH_MS);
  window.setInterval(() => {
    if (document.hidden) return;
    loadServerStats();
  }, STATS_REFRESH_MS);
  updateClock();
  initStatsView();

  (async () => {
    populateRanges();
    updateHud();
    await loadConfig();
    initMap();
    // Le flux ADS-B ne dépend pas des couches optionnelles : on démarre le
    // polling tout de suite, puis on charge aéroports et météo en parallèle.
    poll();
    loadServerStats();
    loadAirportData();
    loadWeather();
  })();
})();

(() => {
  "use strict";

  const DEG = Math.PI / 180;
  const RANGE_STEPS = [5, 10, 25, 50, 75, 100, 125, 150, 200, 250];
  const POLL_MS = 3000;
  const WEATHER_REFRESH_MS = 5 * 60 * 1000;
  const REDUCED_MOTION =
    window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches ?? false;
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
    const total = state.aircraft.length;
    setText(dom.opsCount, `${pad2(list.length)}/${pad2(total)}`);

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
  }

  function setRange(value) {
    state.rangeNm = value;
    if (dom.rangeSelect.value !== String(value)) {
      dom.rangeSelect.value = String(value);
    }
    fitRange();
    updateHud();
    renderAirports();
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
    }
  });

  window.setInterval(updateClock, 250);
  window.setInterval(() => {
    if (document.hidden) return;
    refreshMetar(true);
    loadWeather();
  }, WEATHER_REFRESH_MS);
  updateClock();

  (async () => {
    populateRanges();
    updateHud();
    await loadConfig();
    initMap();
    // Le flux ADS-B ne dépend pas des couches optionnelles : on démarre le
    // polling tout de suite, puis on charge aéroports et météo en parallèle.
    poll();
    loadAirportData();
    loadWeather();
  })();
})();

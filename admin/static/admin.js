(() => {
  "use strict";

  const dom = {
    form: document.getElementById("configForm"),
    radarLink: document.getElementById("radarLink"),
    linkState: document.getElementById("linkState"),
    linkDetail: document.getElementById("linkDetail"),
    sourceState: document.getElementById("sourceState"),
    sourceDetail: document.getElementById("sourceDetail"),
    cacheState: document.getElementById("cacheState"),
    targetState: document.getElementById("targetState"),
    configState: document.getElementById("configState"),
    configDetail: document.getElementById("configDetail"),
    configSource: document.getElementById("configSource"),
    securityWarning: document.getElementById("securityWarning"),
    message: document.getElementById("message"),
    lastRefresh: document.getElementById("lastRefresh"),
    saveBtn: document.getElementById("saveBtn"),
    reloadBtn: document.getElementById("reloadBtn"),
    resetBtn: document.getElementById("resetBtn"),
  };

  const numericFields = [
    "lat",
    "lon",
    "dist_nm",
    "max_radius_nm",
    "refresh_ms",
    "cache_ttl_s",
    "stale_ttl_s",
    "primary_cooldown_s",
  ];

  let formLoaded = false;
  let busy = false;
  let pollTimer = 0;
  const airportsByIcao = new Map();
  let selectedAirportIcao = null;

  function setText(node, value) {
    if (node && node.textContent !== String(value)) {
      node.textContent = String(value);
    }
  }

  function setMessage(text, kind = "") {
    setText(dom.message, text);
    dom.message.className = `message${kind ? ` is-${kind}` : ""}`;
  }

  function errorDetail(payload, status) {
    if (payload && Array.isArray(payload.detail)) {
      return payload.detail.map((item) => item.msg || String(item)).join(" · ");
    }
    if (payload && typeof payload.detail === "string") {
      return payload.detail;
    }
    return `HTTP ${status}`;
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      cache: "no-store",
      ...options,
      headers: {
        Accept: "application/json",
        ...(options.headers || {}),
      },
    });

    let payload = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }

    if (!response.ok) {
      throw new Error(errorDetail(payload, response.status));
    }
    return payload;
  }

  function setBusy(value) {
    busy = value;
    for (const button of [dom.saveBtn, dom.reloadBtn, dom.resetBtn]) {
      button.disabled = value;
    }
  }

  function airportLabel(airport) {
    const iata = airport[1] ? `${airport[1]} — ` : "";
    return `${airport[0]} — ${iata}${airport[2]}`;
  }

  function findAirport(value) {
    const code = String(value || "")
      .trim()
      .split(/[\s—]/)[0]
      .toUpperCase();
    return airportsByIcao.get(code) || null;
  }

  function syncLocationMode() {
    const mode = dom.form.elements.namedItem("location_mode").value;
    const airportMode = mode === "airport";
    const airportField = document.getElementById("airportField");
    const airportSearch = dom.form.elements.namedItem("airport_search");
    const lat = dom.form.elements.namedItem("lat");
    const lon = dom.form.elements.namedItem("lon");
    const hint = document.getElementById("locationHint");

    if (airportField) {
      airportField.hidden = !airportMode;
    }
    if (airportSearch) {
      airportSearch.required = airportMode;
    }
    if (lat) {
      lat.readOnly = airportMode;
    }
    if (lon) {
      lon.readOnly = airportMode;
    }
    if (hint) {
      hint.textContent = airportMode
        ? "La sélection d'un aéroport remplace les coordonnées manuelles."
        : "Le mode coordonnées GPS utilise directement la latitude et la longitude.";
    }
  }

  async function loadAdminAirports() {
    const datalist = document.getElementById("airportList");
    try {
      const response = await fetch("/static/data/airports.json", {
        cache: "force-cache",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        return;
      }
      const rows = await response.json();
      if (!Array.isArray(rows) || !datalist) {
        return;
      }
      const fragment = document.createDocumentFragment();
      for (const row of rows) {
        if (!Array.isArray(row) || row.length < 6 || !row[0]) {
          continue;
        }
        airportsByIcao.set(row[0], row);
        const option = document.createElement("option");
        option.value = airportLabel(row);
        fragment.appendChild(option);
      }
      datalist.replaceChildren(fragment);
    } catch {
      // La recherche d'aéroport reste indisponible ; les coordonnées GPS fonctionnent.
    }
  }

  function fillConfig(config) {
    if (!config) {
      return;
    }

    for (const name of numericFields) {
      const field = dom.form.elements.namedItem(name);
      if (field && config[name] !== undefined && config[name] !== null) {
        field.value = String(config[name]);
      }
    }

    const locationMode = dom.form.elements.namedItem("location_mode");
    const airportSearch = dom.form.elements.namedItem("airport_search");
    const latField = dom.form.elements.namedItem("lat");
    const lonField = dom.form.elements.namedItem("lon");
    const mode = config.location_mode === "airport" ? "airport" : "coords";
    selectedAirportIcao = config.airport_icao || null;
    if (locationMode) {
      locationMode.value = mode;
    }
    if (mode === "airport" && config.airport_icao) {
      const airport = airportsByIcao.get(config.airport_icao);
      if (airport) {
        selectedAirportIcao = airport[0];
        if (airportSearch) {
          airportSearch.value = airportLabel(airport);
        }
        if (latField) latField.value = String(airport[4]);
        if (lonField) lonField.value = String(airport[5]);
      } else if (airportSearch) {
        airportSearch.value = config.airport_icao;
      }
    } else if (airportSearch) {
      airportSearch.value = "";
    }
    syncLocationMode();

    const primary = dom.form.elements.namedItem("primary_provider");
    const fallback = dom.form.elements.namedItem("fallback_provider");
    const radarStyle = dom.form.elements.namedItem("radar_style");
    const weatherMetar = dom.form.elements.namedItem("weather_metar_provider");
    const metarOption = weatherMetar?.querySelector('option[value="metar_taf"]');
    const checkwxOption = weatherMetar?.querySelector('option[value="checkwx"]');
    const metarConfigured = Boolean(config.metar_taf_configured);
    const checkwxConfigured = Boolean(config.checkwx_configured);
    if (primary) {
      primary.value = config.primary_provider_key || config.primary_provider;
    }
    if (fallback) {
      fallback.value = config.fallback_provider_key || "none";
    }
    if (radarStyle) {
      radarStyle.value = config.radar_style || "phosphor";
    }
    if (weatherMetar) {
      if (metarOption) {
        metarOption.disabled = !metarConfigured;
      }
      if (checkwxOption) {
        checkwxOption.disabled = !checkwxConfigured;
      }
      const selected = config.weather_metar_provider || "aviationweather";
      weatherMetar.value =
        (selected === "metar_taf" && !metarConfigured) ||
        (selected === "checkwx" && !checkwxConfigured)
          ? "aviationweather"
          : selected;
    }
    const metarHint = document.getElementById("metarProviderHint");
    if (metarHint) {
      const configured = [];
      if (metarConfigured) configured.push("metar-taf.com");
      if (checkwxConfigured) configured.push("CheckWX");
      metarHint.classList.toggle("is-off", configured.length === 0);
      metarHint.textContent = configured.length
        ? `Fournisseurs avec clé configurée : ${configured.join(", ")}.`
        : "metar-taf.com et CheckWX indisponibles : renseigner METAR_TAF_API_KEY ou CHECKWX_API_KEY dans .env puis recréer le backend.";
    }

    const isRuntime = config.config_source === "runtime";
    setText(dom.configSource, isRuntime ? "RUNTIME" : "ENVIRONNEMENT");
    dom.configSource.classList.toggle("is-runtime", isRuntime);
    setText(
      dom.configDetail,
      `${Math.round(config.dist_nm)} → ${Math.round(config.max_radius_nm)} NM`,
    );
    formLoaded = true;
  }

  function renderStatus(status) {
    const radar = status.radar || {};
    const config = status.config || {};
    const online = Boolean(status.online);

    setText(dom.linkState, online ? "EN LIGNE" : "HORS LIGNE");
    dom.linkState.className = `stat__value ${online ? "is-online" : "is-offline"}`;
    setText(
      dom.linkDetail,
      online
        ? radar.status === "ok"
          ? "flux nominal"
          : radar.status || "connecté"
        : status.error || "backend injoignable",
    );

    setText(dom.sourceState, radar.source ? radar.source.toUpperCase() : "—");
    setText(
      dom.sourceDetail,
      radar.fallback_used ? "SECOURS" : radar.source ? "PRIMAIRE" : "—",
    );

    setText(
      dom.cacheState,
      radar.cache_age_s !== null && radar.cache_age_s !== undefined
        ? `${Number(radar.cache_age_s).toFixed(1)} S`
        : "—",
    );
    setText(
      dom.targetState,
      radar.aircraft_count !== undefined ? radar.aircraft_count : "—",
    );

    dom.securityWarning.hidden = !status.default_credentials;

    const isRuntime = config.config_source === "runtime";
    setText(dom.configState, isRuntime ? "RUNTIME" : "ENV");
    dom.configState.className = `stat__value ${isRuntime ? "is-runtime" : ""}`;
    setText(
      dom.configDetail,
      config.dist_nm !== undefined
        ? `${Math.round(config.dist_nm)} → ${Math.round(config.max_radius_nm)} NM`
        : "—",
    );
    setText(dom.configSource, isRuntime ? "RUNTIME" : "ENVIRONNEMENT");
    dom.configSource.classList.toggle("is-runtime", isRuntime);

    if (status.radar_public_port) {
      dom.radarLink.href = `${window.location.protocol}//${window.location.hostname}:${status.radar_public_port}/`;
    }

    const now = new Date();
    setText(
      dom.lastRefresh,
      `MAJ ${String(now.getUTCHours()).padStart(2, "0")}:${String(
        now.getUTCMinutes(),
      ).padStart(2, "0")}:${String(now.getUTCSeconds()).padStart(2, "0")} UTC`,
    );

    if (!formLoaded && status.config) {
      fillConfig(status.config);
    }

    if (!online) {
      setText(dom.configState, "INDISPONIBLE");
      dom.configState.className = "stat__value is-offline";
    }
  }

  async function loadStatus(fillForm = false) {
    try {
      const status = await api("/api/status");
      renderStatus(status);
      if (fillForm && status.config) {
        fillConfig(status.config);
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      renderStatus({ online: false, error: message });
    }
  }

  function collectPayload() {
    const payload = {};
    for (const name of numericFields) {
      const field = dom.form.elements.namedItem(name);
      payload[name] = Number(field.value);
    }
    payload.primary_provider = dom.form.elements.namedItem("primary_provider").value;
    payload.fallback_provider = dom.form.elements.namedItem("fallback_provider").value;
    payload.weather_metar_provider = dom.form.elements.namedItem(
      "weather_metar_provider",
    ).value;
    payload.radar_style = dom.form.elements.namedItem("radar_style").value;
    const mode = dom.form.elements.namedItem("location_mode").value;
    payload.location_mode = mode;
    if (mode === "airport") {
      const airportSearch = dom.form.elements.namedItem("airport_search");
      let airport = findAirport(airportSearch.value);
      if (!airport && selectedAirportIcao) {
        airport = airportsByIcao.get(selectedAirportIcao) || null;
      }
      if (!airport) {
        throw new Error("Sélectionner un aéroport dans la liste OACI.");
      }
      selectedAirportIcao = airport[0];
      payload.airport_icao = airport[0];
      payload.lat = Number(airport[4]);
      payload.lon = Number(airport[5]);
      dom.form.elements.namedItem("lat").value = String(airport[4]);
      dom.form.elements.namedItem("lon").value = String(airport[5]);
    } else {
      payload.airport_icao = null;
    }
    return payload;
  }

  async function saveConfig(event) {
    event.preventDefault();
    if (busy) {
      return;
    }
    if (!dom.form.checkValidity()) {
      dom.form.reportValidity();
      setMessage("Vérifier les champs en rouge avant d'appliquer.", "error");
      return;
    }

    let payload;
    try {
      payload = collectPayload();
    } catch (error) {
      setMessage(
        error instanceof Error ? error.message : String(error),
        "error",
      );
      return;
    }

    setBusy(true);
    setMessage("Application de la configuration…");
    try {
      const result = await api("/api/config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (result.config) {
        fillConfig(result.config);
      }
      await loadStatus(false);
      setMessage("Configuration appliquée et backend rechargé.", "success");
    } catch (error) {
      setMessage(
        `Échec : ${error instanceof Error ? error.message : String(error)}`,
        "error",
      );
    } finally {
      setBusy(false);
    }
  }

  async function reloadConfig() {
    if (busy) {
      return;
    }
    setBusy(true);
    setMessage("Rechargement du backend…");
    try {
      const result = await api("/api/reload", { method: "POST" });
      if (result.config) {
        fillConfig(result.config);
      }
      await loadStatus(false);
      setMessage("Backend rechargé depuis le fichier runtime.", "success");
    } catch (error) {
      setMessage(
        `Échec : ${error instanceof Error ? error.message : String(error)}`,
        "error",
      );
    } finally {
      setBusy(false);
    }
  }

  async function resetConfig() {
    if (busy) {
      return;
    }
    if (!window.confirm("Supprimer la configuration runtime et revenir aux variables d'environnement ?")) {
      return;
    }

    setBusy(true);
    setMessage("Réinitialisation vers l'environnement…");
    try {
      const result = await api("/api/config/reset", { method: "POST" });
      if (result.config) {
        fillConfig(result.config);
      }
      await loadStatus(false);
      setMessage("Configuration réinitialisée depuis l'environnement.", "success");
    } catch (error) {
      setMessage(
        `Échec : ${error instanceof Error ? error.message : String(error)}`,
        "error",
      );
    } finally {
      setBusy(false);
    }
  }

  function scheduleStatusPoll() {
    window.clearTimeout(pollTimer);
    pollTimer = window.setTimeout(async () => {
      await loadStatus(false);
      scheduleStatusPoll();
    }, 5000);
  }

  dom.form.addEventListener("submit", saveConfig);
  dom.reloadBtn.addEventListener("click", reloadConfig);
  dom.resetBtn.addEventListener("click", resetConfig);

  dom.form.elements.namedItem("location_mode").addEventListener("change", syncLocationMode);
  const airportSearchField = dom.form.elements.namedItem("airport_search");
  const selectAirportFromInput = () => {
    const airport = findAirport(airportSearchField.value);
    if (!airport) {
      selectedAirportIcao = null;
      return;
    }
    selectedAirportIcao = airport[0];
    airportSearchField.value = airportLabel(airport);
    dom.form.elements.namedItem("lat").value = String(airport[4]);
    dom.form.elements.namedItem("lon").value = String(airport[5]);
  };
  airportSearchField.addEventListener("input", selectAirportFromInput);
  airportSearchField.addEventListener("change", selectAirportFromInput);

  loadAdminAirports().finally(() => loadStatus(true));
  scheduleStatusPoll();
})();

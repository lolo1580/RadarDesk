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
    linkPill: document.getElementById("linkPill"),
    linkPillText: document.getElementById("linkPillText"),
    statsState: document.getElementById("statsState"),
    statsDetail: document.getElementById("statsDetail"),
    influxState: document.getElementById("influxState"),
    influxDetail: document.getElementById("influxDetail"),
    message: document.getElementById("message"),
    lastRefresh: document.getElementById("lastRefresh"),
    saveBtn: document.getElementById("saveBtn"),
    reloadBtn: document.getElementById("reloadBtn"),
    resetBtn: document.getElementById("resetBtn"),
    influxEnabled: document.getElementById("influx_enabled"),
    influxUrl: document.getElementById("influx_url"),
    influxOrg: document.getElementById("influx_org"),
    influxBucket: document.getElementById("influx_bucket"),
    influxToken: document.getElementById("influx_token"),
    influxMeasurement: document.getElementById("influx_measurement"),
    influxInterval: document.getElementById("influx_interval_s"),
    influxHint: document.getElementById("influxHint"),
    statsHint: document.getElementById("statsHint"),
    testInfluxBtn: document.getElementById("testInfluxBtn"),
    resetStatsBtn: document.getElementById("resetStatsBtn"),
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
    for (const button of [
      dom.saveBtn,
      dom.reloadBtn,
      dom.resetBtn,
      dom.testInfluxBtn,
      dom.resetStatsBtn,
    ]) {
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

  function updateLocationHint() {
    const hint = document.getElementById("locationHint");
    const mode = dom.form.elements.namedItem("location_mode").value;
    const airportSearch = dom.form.elements.namedItem("airport_search");
    const lat = dom.form.elements.namedItem("lat");
    const lon = dom.form.elements.namedItem("lon");
    if (!hint) {
      return;
    }

    if (mode === "airport") {
      const airport = findAirport(airportSearch.value);
      if (airport) {
        hint.textContent = `Centre actif : ${airportLabel(airport)} (${airport[4]}, ${airport[5]}).`;
      } else {
        hint.textContent = "Sélectionner un aéroport dans la liste pour définir le centre.";
      }
    } else {
      hint.textContent = `Centre actif : coordonnées GPS ${lat.value}, ${lon.value}.`;
    }
  }

  function syncLocationMode() {
    const mode = dom.form.elements.namedItem("location_mode").value;
    const airportMode = mode === "airport";
    const airportField = document.getElementById("airportField");
    const airportSearch = dom.form.elements.namedItem("airport_search");
    const lat = dom.form.elements.namedItem("lat");
    const lon = dom.form.elements.namedItem("lon");

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
    updateLocationHint();
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

    if (dom.influxEnabled) {
      dom.influxEnabled.checked = Boolean(config.influx_enabled);
    }
    if (dom.influxUrl) dom.influxUrl.value = config.influx_url || "";
    if (dom.influxOrg) dom.influxOrg.value = config.influx_org || "";
    if (dom.influxBucket) dom.influxBucket.value = config.influx_bucket || "";
    if (dom.influxMeasurement) {
      dom.influxMeasurement.value = config.influx_measurement || "adsb_stats";
    }
    if (dom.influxInterval) {
      dom.influxInterval.value = String(config.influx_interval_s || 30);
    }
    if (dom.influxToken) {
      dom.influxToken.value = "";
      dom.influxToken.placeholder = config.influx_token_configured
        ? "Token configuré — laisser vide pour conserver"
        : "Token InfluxDB v2";
    }
    if (dom.influxHint) {
      dom.influxHint.classList.toggle(
        "is-off",
        Boolean(config.influx_enabled) && !config.influx_token_configured,
      );
      dom.influxHint.textContent = config.influx_token_configured
        ? "InfluxDB v2. Token déjà configuré : laisser le champ vide pour le conserver."
        : "InfluxDB v2. Aucun token enregistré : renseigner un token avant d'activer l'export.";
    }
    if (dom.statsHint) {
      dom.statsHint.textContent =
        `Collecte SQLite ${config.stats_enabled ? "active" : "désactivée"} · ` +
        `échantillon ${Number(config.stats_sample_seconds || 30)} s · ` +
        `rétention ${Number(config.stats_retention_days || 180)} jours.`;
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
    setText(dom.linkPillText, online ? "EN LIGNE" : "HORS LIGNE");
    dom.linkPill.dataset.state = online ? "live" : "offline";

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

    const statsEnabled = online && Boolean(radar.stats_enabled);
    setText(dom.statsState, online ? (statsEnabled ? "ACTIF" : "OFF") : "—");
    dom.statsState.className = `stat__value ${statsEnabled ? "is-online" : "is-muted"}`;
    setText(
      dom.statsDetail,
      online
        ? statsEnabled
          ? `${Number(config.stats_sample_seconds || 30)} s · ${Number(config.stats_retention_days || 180)} j`
          : radar.stats_error || "désactivé"
        : "indisponible",
    );

    const influxEnabled = Boolean(config.influx_enabled);
    const influxReady =
      influxEnabled &&
      Boolean(config.influx_url) &&
      Boolean(config.influx_org) &&
      Boolean(config.influx_bucket) &&
      Boolean(config.influx_token_configured);
    setText(
      dom.influxState,
      online
        ? influxEnabled
          ? influxReady
            ? "ACTIF"
            : "INCOMPLET"
          : "OFF"
        : "—",
    );
    dom.influxState.className = `stat__value ${
      influxReady ? "is-online" : influxEnabled ? "is-warning" : "is-muted"
    }`;
    setText(
      dom.influxDetail,
      online
        ? influxEnabled
          ? influxReady
            ? "prêt à exporter"
            : "configuration incomplète"
          : "désactivé"
        : "indisponible",
    );

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

    payload.influx_enabled = Boolean(dom.influxEnabled?.checked);
    payload.influx_url = dom.influxUrl?.value.trim() || "";
    payload.influx_org = dom.influxOrg?.value.trim() || "";
    payload.influx_bucket = dom.influxBucket?.value.trim() || "";
    payload.influx_measurement = dom.influxMeasurement?.value.trim() || "adsb_stats";
    payload.influx_interval_s = Number(dom.influxInterval?.value || 30);
    const influxToken = dom.influxToken?.value.trim() || "";
    if (influxToken) {
      payload.influx_token = influxToken;
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
      window.setTimeout(() => loadStatus(false), 1200);
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
      window.setTimeout(() => loadStatus(false), 1200);
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
      window.setTimeout(() => loadStatus(false), 1200);
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

  async function testInflux() {
    if (busy) return;
    setBusy(true);
    setMessage("Test d'écriture InfluxDB avec la configuration active…");
    try {
      const result = await api("/api/influx/test", { method: "POST" });
      if (result.ok) {
        setMessage("InfluxDB répond et accepte l'écriture de test.", "success");
      } else {
        setMessage(`InfluxDB: ${result.detail || "échec du test"}`, "error");
      }
    } catch (error) {
      setMessage(
        `Échec : ${error instanceof Error ? error.message : String(error)}`,
        "error",
      );
    } finally {
      setBusy(false);
    }
  }

  async function resetStats() {
    if (busy) return;
    if (
      !window.confirm(
        "Effacer tout l'historique des statistiques de l'antenne ? Cette action est irréversible.",
      )
    ) {
      return;
    }
    setBusy(true);
    setMessage("Effacement de l'historique…");
    try {
      const result = await api("/api/stats/reset", { method: "POST" });
      setMessage(
        `Historique effacé (${Number(result.deleted || 0)} relevé(s)).`,
        "success",
      );
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
  dom.testInfluxBtn.addEventListener("click", testInflux);
  dom.resetStatsBtn.addEventListener("click", resetStats);

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
    updateLocationHint();
  };
  airportSearchField.addEventListener("input", selectAirportFromInput);
  airportSearchField.addEventListener("change", selectAirportFromInput);
  for (const name of ["lat", "lon"]) {
    dom.form.elements.namedItem(name).addEventListener("input", updateLocationHint);
  }

  function setupSideNav() {
    const links = [...document.querySelectorAll(".side-nav a")];
    const activate = (id) => {
      for (const link of links) {
        link.classList.toggle("is-active", link.getAttribute("href") === `#${id}`);
      }
    };
    for (const link of links) {
      link.addEventListener("click", () => activate(link.getAttribute("href").slice(1)));
    }
    const hash = window.location.hash.slice(1);
    if (hash && links.some((link) => link.getAttribute("href") === `#${hash}`)) {
      activate(hash);
    }
  }

  setupSideNav();
  loadAdminAirports().finally(() => loadStatus(true));
  scheduleStatusPoll();
})();

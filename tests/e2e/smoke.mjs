// Test de fumée RadarDesk.
// Prérequis : stack lancée et réseau Docker radardesk_default.
// Exemple :
//   docker run --rm --network radardesk_default \
//     -v "$PWD/tests/e2e:/work" -w /work \
//     mcr.microsoft.com/playwright:v1.64.1-noble node smoke.mjs
import { chromium } from "playwright";

const DESKTOP = process.env.DESKTOP_URL || "http://desktop/";
const ADMIN = process.env.ADMIN_URL || "http://admin:8000/";
const ADMIN_USER = process.env.ADMIN_USER || "admin";
const ADMIN_PASSWORD = process.env.ADMIN_PASSWORD || "admin";

function fail(message) {
  throw new Error(message);
}

async function desktopSmoke(browser) {
  const context = await browser.newContext({ viewport: { width: 1920, height: 920 } });
  const page = await context.newPage();
  const errors = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("pageerror", (error) => errors.push(String(error)));

  await page.goto(DESKTOP, { waitUntil: "domcontentloaded" });
  await page.waitForFunction(
    () => (document.getElementById("targetCount")?.textContent || "00") !== "00",
    { timeout: 30000 },
  );
  await page.click("#statsViewBtn");
  await page.waitForTimeout(1800);

  const result = await page.evaluate(() => ({
    reception: document.getElementById("statsPage")?.dataset.reception,
    localVisible: Boolean(document.getElementById("statsLocalView")?.offsetParent),
    onlineVisible: Boolean(document.getElementById("statsView")?.offsetParent),
    switchVisible: Boolean(document.getElementById("localStatsSwitch")?.offsetParent),
    overflowX: document.documentElement.scrollWidth - innerWidth,
  }));
  if (result.overflowX > 0) fail(`Desktop: débordement horizontal (${result.overflowX}px)`);

  if (result.reception === "local") {
    if (!result.localVisible || result.onlineVisible) {
      fail("Desktop: la vue locale n'est pas affichée par défaut");
    }
    if (!result.switchVisible) fail("Desktop: sélecteur TEMPS RÉEL/HISTORIQUE absent");
    await page.click('#localStatsSwitch button[data-local-view="history"]');
    await page.waitForTimeout(1800);
    const history = await page.evaluate(() => ({
      visible: Boolean(document.getElementById("statsView")?.offsetParent),
      title: document.getElementById("statsPageTitle")?.textContent,
    }));
    if (!history.visible || history.title !== "Historique de réception") {
      fail("Desktop: la vue HISTORIQUE ne s'affiche pas");
    }
  } else if (!result.onlineVisible) {
    fail("Desktop: la vue statistiques en ligne n'est pas affichée");
  }

  const radarErrors = errors.filter((text) =>
    !/webGL|openfreemap|NS_ERROR|tiles\.arcgis|minimal-ui|404/i.test(text),
  );
  if (radarErrors.length) fail(`Desktop: erreurs console: ${radarErrors.join(" | ")}`);
  await context.close();
}

async function adminSmoke(browser) {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    httpCredentials: { username: ADMIN_USER, password: ADMIN_PASSWORD },
  });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  await page.goto(ADMIN, { waitUntil: "networkidle" });
  await page.waitForTimeout(500);
  await page.click('.side-nav button[data-panel="radar"]');
  await page.waitForTimeout(1200);
  const info = await page.evaluate(() => ({
    telemetryVisible: Boolean(document.getElementById("localTelemetryCard")?.offsetParent),
    source: document.getElementById("sourceState")?.textContent,
    overflowX: document.documentElement.scrollWidth - innerWidth,
  }));
  if (info.overflowX > 0) fail(`Admin: débordement horizontal (${info.overflowX}px)`);
  if (info.source === "LOCAL" && !info.telemetryVisible) {
    fail("Admin: télémétrie locale absente en mode local");
  }
  if (errors.length) fail(`Admin: erreurs console: ${errors.join(" | ")}`);
  await context.close();
}

const browser = await chromium.launch({ args: ["--no-sandbox"] });
try {
  await desktopSmoke(browser);
  await adminSmoke(browser);
  console.log("OK — desktop et admin");
} finally {
  await browser.close();
}

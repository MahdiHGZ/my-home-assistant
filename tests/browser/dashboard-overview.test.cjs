const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");
const test = require("node:test");
const { JSDOM } = require("jsdom");

const web = resolve(__dirname, "../../web");
const html = readFileSync(resolve(web, "index.html"), "utf8");
const dashboard = readFileSync(resolve(web, "dashboard.js"), "utf8");
const app = readFileSync(resolve(web, "app.js"), "utf8").replace(/\binit\(\);\s*$/, "");

function load(t, lang = "en", browserStorage = {}) {
  const dom = new JSDOM(html, { url: "http://localhost/", runScripts: "outside-only" });
  t.after(() => dom.window.close());
  const { window } = dom;
  window.scrollTo = () => {};
  window.localStorage.setItem("coukab-lang", lang);
  for (const [key, value] of Object.entries(browserStorage)) {
    window.localStorage.setItem(key, JSON.stringify(value));
  }
  const calls = [];
  window.request = async (_method, path, body) => {
    calls.push([path, body && JSON.parse(JSON.stringify(body))]);
    if (path === "/api/lights/library") return {
      groups: [{ name: "Study", bulbs: ["B1", "Removed"], missing: ["Removed"] }],
      scenes: [],
    };
    return { ok: true };
  };
  window.eval(dashboard + "\n" + app + `
    api = window.request;
    caps = {
      bulbs: ["B1", "B2"], scene_modes: [{ key: "warm_white", name: "Warm white" }],
      keypad: [{ code: "KP1", combo: true, label: "capture" }],
    };
    dashboardUI.installStrings();
    applyLang();
    buildBulbGrid();
    wireEvents();
    dashboardUI.init(initTabs());
    window.receiveStatus = renderAll;
    window.addNotice = toast;
    window.drawStatus = () => {};
  `);
  return { window, doc: window.document, calls };
}

const settled = () => new Promise(resolve => setImmediate(resolve));

const snapshot = () => ({
  lights: { available: true, bulbs: [], registered_names: ["B1", "B2"] },
  vacuum: { available: true, status: "Building Map", battery: 70, fault: 143 },
  purifier: { available: false },
  moments: { count: 1, latest: "/moments/last.jpg", latest_at: Date.now() / 1000 - 120 },
  _meta: { stale: true },
});

test("overview pause and advanced settings retain the home context in English and Farsi", async t => {
  for (const lang of ["en", "fa"]) {
    const { window, doc, calls } = load(t, lang);
    const $ = selector => doc.querySelector(selector);
    window.receiveStatus(snapshot());
    assert.equal(doc.documentElement.dir, lang === "fa" ? "rtl" : "ltr");
    assert.equal($(".dashboard").firstElementChild.id, "statVacuum");
    assert.match($("#attentionList").textContent, lang === "fa" ? /کد خطا ۱۴۳.*آفلاین.*قدیمی/ : /Fault code 143.*offline.*out of date/);
    assert.match($("#statCamera .stat-line").textContent, lang === "fa" ? /۲ دقیقه/ : /2 minutes ago/);
    assert.equal($("#overviewAirPower").hidden, true);
    assert.equal($("#overviewPause").hidden, false);
    $("#overviewPause").focus();
    doc.activeElement.click();
    assert.deepEqual(calls.at(-1), ["/api/vacuum/action", { action: "pause" }]);
    const advanced = $("#statVacuum [data-i18n='advanced_settings']");
    advanced.focus();
    doc.activeElement.click();
    assert.equal(doc.activeElement, $("#s20Details summary"));
    assert.equal($("#s20Details").open, true);
    $("[data-tab='home']").click();
    assert.equal(doc.activeElement, advanced);
    await settled();
  }
});

test("actionable notices retain focus and status feedback coalesces during rapid actions", async t => {
  const { window, doc } = load(t);
  const seen = [];
  window.addNotice("First change", "good", "Undo", () => seen.push("first"));
  const first = doc.querySelector("#toastAction .toast-action");
  first.focus();
  window.addNotice("Second change", "good", "Undo", () => seen.push("second"));
  window.addNotice("Device ready", "good");
  const status = doc.querySelector("#toastStatus").firstChild;
  window.addNotice("Device ready", "good");
  assert.equal(doc.querySelector("#toastStatus").firstChild, status);
  assert.equal(doc.activeElement, first);
  assert.match(doc.querySelector("#toastAction").textContent, /First change/);
  doc.activeElement.click();
  const second = doc.querySelector("#toastAction .toast-action");
  assert.equal(doc.activeElement, second);
  assert.match(doc.querySelector("#toastAction").textContent, /Second change/);
  second.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  assert.equal(doc.querySelector("#toastAction").hidden, true);
  assert.deepEqual(seen, ["first", "second"]);
  await settled();
});

test("saved groups and the room list send only the selected bulb and preserve placement", async t => {
  const { window, doc, calls } = load(t, "en", {
    "coukab-room-devices": { "bulb:B1": { room: "Study", x: 28, y: 55 } },
    "coukab-floorplan": "data:image/png;base64,AA==",
  });
  const $ = selector => doc.querySelector(selector);
  await settled();
  assert.match($("#lightingEntries").textContent, /Removed bulbs.*Removed/);
  $("#lightingEntries button").click();
  $("#targetsOn").click();
  assert.deepEqual(calls.at(-1), ["/api/lights/control", { targets: ["B1"], power: true }]);
  $("[data-tab='rooms']").click();
  assert.equal($("#roomMap").hidden, false);
  assert.equal($("#roomMarkers button").style.left, "28%");
  $("#roomFilter").value = "Study";
  $("#roomFilter").dispatchEvent(new window.Event("change"));
  assert.equal(doc.querySelectorAll("#roomDeviceList li").length, 1);
  const position = $("#roomDeviceList input[type='number']");
  position.value = "63";
  position.dispatchEvent(new window.Event("change"));
  assert.equal(JSON.parse(window.localStorage.getItem("coukab-room-devices"))["bulb:B1"].x, 63);
  assert.equal($("#roomMarkers button").style.left, "63%");
  $("#roomDeviceList [aria-label='B1 · Off']").click();
  assert.deepEqual(calls.at(-1), ["/api/lights/control", { targets: ["B1"], power: false }]);
});

test("keyboard help is built from live bindings and preferences persist", async t => {
  const { window, doc } = load(t);
  assert.match(doc.querySelector("#physicalKeys").textContent, /KP0 \+ KP1 Capture/);
  const theme = doc.querySelector("#dashboardTheme");
  theme.value = "light";
  theme.dispatchEvent(new window.Event("change"));
  assert.equal(doc.documentElement.dataset.theme, "light");
  assert.equal(JSON.parse(window.localStorage.getItem("coukab-dashboard-preferences")).theme, "light");
  doc.body.dispatchEvent(new window.KeyboardEvent("keydown", {
    altKey: true, shiftKey: true, code: "KeyR", key: "R", bubbles: true,
  }));
  assert.equal(doc.querySelector("[data-tab='rooms']").getAttribute("aria-current"), "page");
  await settled();
});

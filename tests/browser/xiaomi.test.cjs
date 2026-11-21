const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");
const test = require("node:test");
const { JSDOM } = require("jsdom");

const web = resolve(__dirname, "../../web");
const html = readFileSync(resolve(web, "index.html"), "utf8");
const script = readFileSync(resolve(web, "xiaomi.js"), "utf8");
const nextTurn = () => new Promise(resolve => setTimeout(resolve, 20));

test("advanced settings require verification and keep partial readings unavailable in Farsi", async t => {
  const dom = new JSDOM(html, {runScripts: "outside-only", pretendToBeVisual: true});
  t.after(() => dom.window.close());
  const {window} = dom;
  const requests = [];
  let writable = false;
  window.api = async (method, path, body) => {
    requests.push({method, path, body});
    if (path === "/api/cleaning-air") return {mode: "auto", seconds: 300, enabled: false, phase: "disabled"};
    if (method === "POST") {
      writable = true;
      return {ok: true};
    }
    const settings = {mop_route: {value: 0, writable}};
    for (const key of ["repeat_clean", "dnd_enabled", "dnd_start_hour", "dnd_start_minute",
      "dnd_end_hour", "dnd_end_minute"]) settings[key] = {value: null, writable: false};
    return {firmware: "1.0.0", settings};
  };
  window.document.documentElement.lang = "fa";
  window.eval(script);
  const details = window.document.querySelector("#s20Details");
  details.open = true;
  await nextTurn();
  assert.match(details.textContent, /مسیر تی‌کشی/);
  assert.match(details.textContent, /در دسترس نیست/);
  assert.equal(details.querySelector("select").disabled, true);
  const verify = [...details.querySelectorAll("button")].find(button => /بررسی پشتیبانی/.test(button.textContent));
  verify.click();
  await nextTurn();
  assert.equal(requests.filter(request => request.method === "POST").length, 1);
  assert.equal(requests.at(-2).body.setting, "mop_route");
  assert.equal(requests.at(-2).body.verify, true);
  assert.equal(details.querySelector("select").disabled, false);
});

test("overview previews the coordinated run before saving", async t => {
  const dom = new JSDOM(html, {runScripts: "outside-only", pretendToBeVisual: true});
  t.after(() => dom.window.close());
  const {window} = dom;
  let saved;
  window.api = async (method, _path, body) => {
    if (method === "POST") {
      saved = body;
      return {...body, phase: "armed"};
    }
    return {mode: "auto", seconds: 300, enabled: false, phase: "disabled"};
  };
  window.eval(script);
  await nextTurn();
  window.document.querySelector("#cleaningAirDetails").open = true;
  const form = window.document.querySelector("#cleaningAirForm");
  const preview = window.document.querySelector("#cleaningAirPreview");
  assert.equal(form.elements.enabled.checked, false);
  form.elements.mode.value = "sleep";
  form.elements.seconds.value = "45";
  form.elements.enabled.checked = true;
  form.dispatchEvent(new window.Event("input"));
  assert.match(preview.textContent, /Sleep mode/);
  assert.match(preview.textContent, /45 seconds/);
  form.dispatchEvent(new window.Event("submit", {cancelable: true}));
  await nextTurn();
  assert.equal(saved.enabled, true);
  assert.equal(saved.mode, "sleep");
  assert.equal(saved.seconds, 45);
  assert.match(window.document.querySelector("#cleaningAirSummary").textContent, /Waiting for cleaning/);
});

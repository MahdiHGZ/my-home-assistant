const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");
const test = require("node:test");
const { JSDOM } = require("jsdom");

const web = resolve(__dirname, "../../web");
const html = readFileSync(resolve(web, "index.html"), "utf8");
const app = readFileSync(resolve(web, "app.js"), "utf8").replace(/\binit\(\);\s*$/, "");

function dashboard(t) {
  const dom = new JSDOM(html, { url: "http://localhost/", runScripts: "outside-only" });
  t.after(() => dom.window.close());
  return { window: dom.window, run: source => dom.window.eval(app + "\n" + source) };
}

test("device-provided names stay literal in both dashboard languages", t => {
  for (const lang of ["en", "fa"]) {
    const { window, run } = dashboard(t);
    window.localStorage.setItem("coukab-lang", lang);
    window.deviceName = 'Room <b>East</b> & "West"';
    run('buildRoomChips([{id: 1, name: window.deviceName}]); caps = {bulbs: [window.deviceName]}; buildBulbGrid();');
    for (const sel of ["#roomChips button", "#bulbGrid .name"]) {
      const el = window.document.querySelector(sel);
      assert.equal(el.textContent, window.deviceName);
      assert.equal(el.querySelector("b"), null);
    }
    assert.equal(run('momentTile("https://example.org/photo.jpg").querySelector("a")'), null);
  }
});

test("a stalled action releases the pending control on deadline", async t => {
  const { window, run } = dashboard(t);
  let abort;
  window.setTimeout = callback => { abort = callback; return 1; };
  window.fetch = (_path, opts) => new Promise((_, reject) =>
    opts.signal.addEventListener("abort", () => reject(new Error("aborted"))));
  const result = run('act("POST", "/api/lights/control", {power:true}, {btn:$("#undoBtn")})');
  assert.equal(window.document.querySelector("#undoBtn").classList.contains("pending"), true);
  abort();
  assert.equal(await result, null);
  assert.equal(window.document.querySelector("#undoBtn").classList.contains("pending"), false);
  assert.match(window.document.querySelector("#toast").textContent, /timed out/);
});

test("a late status poll cannot replace the more recent event snapshot", async t => {
  const { window, run } = dashboard(t);
  let resolvePoll;
  window.fetch = () => new Promise(resolve => { resolvePoll = resolve; });
  window.EventSource = class {
    addEventListener(_name, cb) { window.statusEvent = cb; }
  };
  const pending = run('renderAll = s => { window.latest = s.value; }; connectEvents(); pollStatus();');
  window.statusEvent({ data: JSON.stringify({ value: "new" }) });
  resolvePoll({ ok: true, json: async () => ({ value: "old" }) });
  await pending;
  assert.equal(window.latest, "new");
});

test("a lost chat response does not resubmit a command", async t => {
  const { window, run } = dashboard(t);
  window.ReadableStream = ReadableStream;
  const requests = [];
  window.fetch = async path => { requests.push(path); throw new Error("connection lost"); };
  await assert.rejects(run('streamChat("lights on", {}, 0, {})'), /connection lost/);
  assert.deepEqual(requests, ["/api/chat/stream"]);
});

test("stalled chat reads abort on the idle deadline", async t => {
  const { window, run } = dashboard(t);
  window.ReadableStream = ReadableStream;
  let expire;
  window.setTimeout = callback => { expire = callback; return 1; };
  window.TextDecoder = TextDecoder;
  window.fetch = async (_path, { signal }) => ({
    ok: true, body: { getReader: () => ({ read: () => new Promise((_, reject) => {
      signal.addEventListener("abort", () => reject(new Error("aborted")));
      queueMicrotask(() => expire());
    }) }) },
  });
  await assert.rejects(run('streamChat("query", {}, 0, {})'), /aborted/);
});

test("the final brightness value follows the in-flight preview", async t => {
  const { window, run } = dashboard(t);
  const seen = [];
  let release;
  window.write = (_method, _path, body) => {
    seen.push(body.brightness);
    if (seen.length === 1) return new Promise(resolve => { release = resolve; });
  };
  window.setTimeout = callback => { queueMicrotask(callback); return 1; };
  const pending = run('act = window.write; const send = brightnessSender(); const first = send(20); send(40); send(60); first;');
  assert.deepEqual(seen, [20]);
  release();
  await pending;
  assert.deepEqual(seen, [20, 60]);
});

test("a lost physical-action response retries with the same key and waits for the job", async t => {
  const { window, run } = dashboard(t);
  const calls = [];
  const id = "a".repeat(32);
  window.setTimeout = (callback, ms) => { if (ms === 1000) queueMicrotask(callback); return 1; };
  window.fetch = async (path, opts) => {
    calls.push([path, opts.headers["Idempotency-Key"]]);
    if (calls.length === 1) throw Error("connection closed");
    return calls.length === 2
      ? {ok: true, status: 202, json: async () => ({accepted: true, job_id: id})}
      : {ok: true, status: 200, json: async () => ({job: {status: "done", result: {ok: true, image: "/moments/example.jpg"}}})};
  };
  assert.equal((await run('api("POST", "/api/camera/capture", {flash:true})')).image, "/moments/example.jpg");
  assert.match(calls[0][1], /^[a-f0-9]{32}$/);
  assert.equal(calls[0][1], calls[1][1]);
  assert.equal(calls[2][0], "/api/actions/" + id);
});

test("the routine editor saves typed steps from two devices and preserves their order", async t => {
  const { window, run } = dashboard(t);
  const requests = [];
  window.fetch = async (path, opts) => {
    requests.push([path, opts.body && JSON.parse(opts.body)]);
    return {ok: true, status: 200, json: async () => path.endsWith("/save") ? {ok:true} : {routines:[], runs:[]}};
  };
  run('wireHousehold([{device:"lights",action:"all_off",fields:{}},{device:"purifier",action:"power",fields:{value:{type:"boolean"}}}]);');
  const d = window.document, picker = d.querySelector("#routineAction");
  d.querySelector("#routineName").value = "Bedtime";
  d.querySelector("#routineAdd").click();
  picker.value = "1"; picker.onchange();
  d.querySelector("#routineParams select").value = "false";
  d.querySelector("#routineAdd").click();
  await d.querySelector("#routineForm").onsubmit({preventDefault() {}});
  assert.deepEqual(requests.find(r => r[0].endsWith("/save"))[1], {name:"Bedtime", schedule:"",
    steps:[{device:"lights",action:"all_off"},{device:"purifier",action:"power",value:false}]});
});

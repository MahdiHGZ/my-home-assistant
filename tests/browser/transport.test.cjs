const assert = require("node:assert/strict");
const {readFileSync} = require("node:fs");
const {resolve} = require("node:path");
const vm = require("node:vm");
const test = require("node:test");
const {JSDOM} = require("jsdom");

const root = resolve(__dirname, "../../web");
const file = name => readFileSync(resolve(root,name), "utf8");

test("service worker revalidates the shell and recovers offline without caching API responses", async () => {
  const events = {}, store = new Map();
  const cache = {put: (url,res) => store.set(url.url,res), match: url => store.get(url.url),
                 addAll: paths => assert.equal(new Set(paths).size, paths.length)};
  const context = {URL,location:{origin:"http://localhost"},caches:{open:async()=>cache},
    self:{addEventListener:(k,fn)=>events[k]=fn, skipWaiting:()=>{}}};
  vm.runInNewContext(file("sw.js"), context);
  let work;
  events.install({waitUntil:p=>work=p});
  await work;
  const hit = path => {
    let result;
    events.fetch({request:{url:"http://localhost"+path,method:"GET"},respondWith:p=>result=p});
    return result;
  };
  for (const text of ["old","updated"]) {
    context.fetch = async (_req, opts) => {assert.equal(opts.cache,"no-cache"); return new Response(text);};
    assert.equal(await (await hit("/static/app.js")).text(),text);
  }
  context.fetch = async () => {throw Error("offline");};
  assert.equal(await (await hit("/static/app.js")).text(),"updated");
  assert.equal(hit("/api/status"),undefined);
});

test("polling recovers SSE loss, referenced icons exist and keyboard release stops a held drive", async t => {
  const dom = new JSDOM(file("index.html"), {url:"http://localhost",runScripts:"outside-only"});
  t.after(()=>dom.window.close());
  const w = dom.window, calls = [];
  w.EventSource = class {constructor(){w.stream=this;} addEventListener(){} close(){}};
  w.request = async (method,path,body) => {calls.push([method,path,body]); return {epoch:"epoch",drive_epoch:"epoch"};};
  w.setTimeout=()=>1;
  w.eval(file("dashboard.js")+"\n"+file("app.js").replace(/\binit\(\);\s*$/,"")+ `
    api=window.request; renderAll=()=>{}; connectEvents(); eventSource.onopen(); eventSource.onerror();
    window.checkPolling=()=>{if(!sseConnected)return pollStatus();};
    window.missingIcons = [...document.querySelectorAll("[data-icon]")].map(el=>el.dataset.icon).filter(n=>!ICONS[n]);
    driveEpoch="epoch"; wireDpadHold(document.querySelector("[data-vac-remote]"));
  `);
  await w.checkPolling();
  assert.equal(calls[0][1],"/api/status");
  assert.deepEqual(Array.from(w.missingIcons),[]);
  const btn=w.document.querySelector("[data-vac-remote]");
  for (const type of ["keydown","keyup"]) btn.dispatchEvent(new w.KeyboardEvent(type,{key:" "}));
  assert.deepEqual(calls.slice(1).map(x=>x[2].value),[Number(btn.dataset.vacRemote),5]);
});

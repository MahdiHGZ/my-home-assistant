/* Exercise a phone-sized browser against the real demo HTTP server. */
const assert = require("node:assert/strict");
const {spawn} = require("node:child_process");
const {once} = require("node:events");
const {createInterface} = require("node:readline");
const {resolve} = require("node:path");
const {chromium} = require("playwright-core");

async function verify() {
  const server = spawn(process.env.COUKAB_PYTHON || "python", ["main.py","--demo","--web-port","0"],
    {cwd:resolve(__dirname,"../.."), stdio:["ignore","ignore","pipe"]});
  const exited = once(server,"exit");
  const lines = createInterface({input:server.stderr});
  let browser;
  const deadline = setTimeout(()=>server.kill(),45000);
  try {
    let url;
    for await (const line of lines) {
      const match = line.match(/Web interface: (http:\/\/127\.0\.0\.1:\d+)/);
      if (match) {url=match[1];break;}
    }
    assert.ok(url,"The hardware-free demo started");
    browser = await chromium.launch(process.env.CHROME_BIN ? {executablePath:process.env.CHROME_BIN} : {channel:"chrome"});
    const page = await browser.newPage({viewport:{width:320,height:740},isMobile:true,hasTouch:true});
    await page.goto(url);
    await page.waitForFunction(()=>document.querySelector("#statVacuum .stat-line").textContent.includes("94"));
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth),320);
    const started = await page.request.post(url+"/api/vacuum/action",{data:{action:"sweep"}});
    assert.ok(started.ok(), "The demo accepted the cleaning command");
    const pause = page.locator("#overviewPause");
    await pause.waitFor({state:"visible"});
    await pause.press("Enter");
    await page.waitForFunction(()=>document.querySelector("#statVacuum .stat-line").textContent.includes("Paused"));
    const advanced = page.locator("#statVacuum [data-i18n='advanced_settings']");
    await advanced.press("Enter");
    assert.equal(await page.evaluate(()=>document.activeElement.closest("details")?.id),"s20Details");
    await page.locator("[data-tab='home']").tap();
    assert.equal(await advanced.evaluate(el=>el===document.activeElement),true);
    await page.locator("#langBtn").tap();
    assert.deepEqual(await page.evaluate(()=>[document.documentElement.dir,document.documentElement.scrollWidth]),["rtl",320]);
    console.log("Verified phone viewport, keyboard pause and device details, touch navigation and RTL.");
  } finally {
    clearTimeout(deadline);
    if (browser) await browser.close();
    server.kill();
    await exited;
  }
}
verify().catch(err=>{console.error(err);process.exitCode=1;});

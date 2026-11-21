"use strict";
(async function () {
  const byId = id => document.getElementById(id);
  const tr = (en, fa) => localStorage.getItem("coukab-lang") === "fa" ? fa : en;
  const link = byId("setupLink");
  async function request(path, body) {
    const response = await fetch(path, body ? {method:"POST", headers:{"Content-Type":"application/json"},
      body:JSON.stringify(body)} : {cache:"no-store"});
    const data = await response.json();
    if (!response.ok) throw Error(data.error || String(response.status));
    return data;
  }
  try {
    const setup = await request("/api/setup");
    if (setup.watch) {
      let revision = (await request("/api/dev-assets")).revision;
      setInterval(async () => {
        try { if ((await request("/api/dev-assets")).revision !== revision) location.reload(); }
        catch { /* Development server restarting. */ }
      }, 1600);
    }
    if (link) {
      const text = () => {
        const missing = Object.values(setup.missing).some(fields => fields.length);
        link.textContent = missing ? tr("Set up devices", "راه‌اندازی دستگاه‌ها") : tr("Setup", "تنظیمات");
      };
      text();
      byId("langBtn").addEventListener("click", text);
      return;
    }
    document.documentElement.lang = tr("en", "fa");
    document.documentElement.dir = tr("ltr", "rtl");
    const labels = {setupTitle:["Setup","تنظیمات"],backHome:["Home","خانه"],deviceTitle:["Device settings","تنظیمات دستگاه"],
      reloadSettings:["Reload device settings","بارگذاری دوباره تنظیمات"],addressTitle:["Server addresses","نشانی‌های سرور"],
      enableText:["Enable sharing","فعال‌سازی اشتراک"],sensorLegend:["Sensors","حسگرها"],
      routineLegend:["Routines","روال‌ها"],saveMqtt:["Save","ذخیره"]};
    for (const [id, words] of Object.entries(labels)) byId(id).textContent = tr(...words);
    byId("instruction").textContent = setup.demo ? tr("Simulated devices are active.", "دستگاه‌های شبیه‌سازی‌شده فعال هستند.") :
      tr("On the server, copy example.env to .env and fill in the fields listed below. Register bulbs in bulb.json. Reload after saving.",
        "در سرور، example.env را به .env کپی کنید و فیلدهای زیر را پر کنید. لامپ‌ها را در bulb.json ثبت کنید و تنظیمات را بارگذاری کنید.");
    for (const [device, fields] of Object.entries(setup.missing)) {
      const li = document.createElement("li");
      li.textContent = device + " — " + (fields.join(", ") || tr("Configured", "پیکربندی شده"));
      byId("deviceSettings").append(li);
    }
    byId("addresses").textContent = [setup.local_url, setup.manual_url || location.origin].filter(Boolean).join(" · ");
    byId("reloadSettings").hidden = setup.demo;
    byId("reloadSettings").onclick = async () => {
      try { await request("/api/setup/reload", {}); location.reload(); }
      catch (e) { byId("setupStatus").textContent = e.message; }
    };
    function show(mqtt) {
      byId("brokerStatus").textContent = !mqtt.configured ?
        tr("Set COUKAB_MQTT_HOST on the server to connect a local broker.", "برای اتصال به کارگزار محلی COUKAB_MQTT_HOST را در سرور تنظیم کنید.") :
        mqtt.connected ? tr("Broker connected", "متصل به کارگزار") : tr("Broker offline", "کارگزار آفلاین");
      byId("mqttSettings").hidden = !mqtt.configured;
      byId("shareEnabled").checked = mqtt.enabled;
      for (const [id, entries, selected] of [["shareSensors",Object.entries(mqtt.sensor_options),mqtt.sensors],
        ["shareRoutines",mqtt.routine_options.map(n=>[n,n]),mqtt.routines]]) {
        byId(id).replaceChildren(...entries.map(([key, title]) => {
          const label = document.createElement("label"), check = document.createElement("input");
          check.type = "checkbox"; check.value = key; check.checked = selected.includes(key);
          label.append(check, " " + title + " "); return label;
        }));
      }
    }
    show(await request("/api/mqtt"));
    byId("mqttSettings").onsubmit = async e => {
      e.preventDefault();
      const selected = id => [...byId(id).querySelectorAll("input:checked")].map(el=>el.value);
      try { show(await request("/api/mqtt",{enabled:byId("shareEnabled").checked,sensors:selected("shareSensors"),routines:selected("shareRoutines")})); }
      catch (err) { byId("brokerStatus").textContent = err.message; }
    };
  } catch (e) { if (byId("setupStatus")) byId("setupStatus").textContent = e.message; }
})();

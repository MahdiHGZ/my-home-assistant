(() => {
  const labels = {
    coordination: ["Vacuum and air purifier routine", "روال جارو و تصفیه هوا"],
    vacSettings: ["S20 advanced settings", "تنظیمات پیشرفته جارو S20"],
    airMaintenance: ["Purifier filter and motor readings", "وضعیت فیلتر و موتور تصفیه هوا"],
    purifier: ["Purifier", "تصفیه هوا"],
    mode: ["Purifier mode", "حالت تصفیه هوا"],
    auto: ["Auto", "خودکار"], sleep: ["Sleep", "خواب"], favorite: ["Favorite", "دلخواه"],
    seconds: ["Settling time (seconds)", "مدت انتظار پس از نظافت (ثانیه)"],
    enable: ["Enable routine", "فعال‌کردن روال"], save: ["Save", "ذخیره"],
    disabled: ["Off", "خاموش"], armed: ["Waiting for cleaning", "در انتظار شروع نظافت"],
    running: ["Cleaning in progress", "نظافت در حال انجام است"],
    settling: ["Waiting to restore the purifier", "در انتظار بازگرداندن تنظیمات تصفیه هوا"],
    restoring: ["Restoring purifier settings", "در حال بازگرداندن تنظیمات تصفیه هوا"],
    manual: ["Manual change detected", "تغییر دستی تشخیص داده شد"],
    vacuum_offline: ["Vacuum status unavailable", "وضعیت جارو در دسترس نیست"],
    purifier_offline: ["Purifier status unavailable", "وضعیت تصفیه هوا در دسترس نیست"],
    unavailable: ["Unavailable", "در دسترس نیست"], loading: ["Loading…", "در حال بارگذاری…"],
    unsupported: ["Model or firmware unavailable", "مدل یا نسخه نرم‌افزار دستگاه در دسترس نیست"],
    failed: ["The device did not confirm the change.", "دستگاه تغییر را تأیید نکرد."],
    saved: ["Saved and confirmed", "ذخیره و تأیید شد"],
    verify: ["Verify support with current settings", "بررسی پشتیبانی با تنظیمات فعلی"],
    verified: ["Write support confirmed", "امکان تغییر تنظیمات تأیید شد"],
    refresh: ["Refresh", "تازه‌سازی"], readOnly: ["Read only", "فقط خواندنی"],
    mop_route: ["Mop route", "مسیر تی‌کشی"], repeat_clean: ["Double pass", "نظافت دوباره"],
    sPattern: ["S pattern", "مسیر S"], yPattern: ["Y pattern", "مسیر Y"],
    on: ["On", "روشن"], off: ["Off", "خاموش"],
    dnd: ["Do not disturb", "مزاحم نشوید"], start: ["Starts at", "زمان شروع"], end: ["Ends at", "زمان پایان"],
    roomProfiles: ["Room cleaning profiles", "تنظیمات نظافت اتاق‌ها"], room: ["Room", "اتاق"],
    cleanMode: ["Cleaning mode", "حالت نظافت"], suction: ["Suction", "مکش"], water: ["Water output", "میزان آب"],
    sweep: ["Sweep", "جارو"], mop: ["Mop", "تی"], both: ["Sweep and mop", "جارو و تی"],
    consecutive: ["Sweep then mop", "جارو سپس تی"], silent: ["Silent", "بی‌صدا"],
    low: ["Low", "کم"], medium: ["Medium", "متوسط"], high: ["High", "زیاد"], full: ["Maximum", "حداکثر"],
    filter_life: ["Filter life remaining", "عمر باقی‌مانده فیلتر"], filter_used_time: ["Filter usage", "کارکرد فیلتر"],
    filter_left_days: ["Filter days remaining", "روزهای باقی‌مانده فیلتر"], motor_rpm: ["Motor speed", "سرعت موتور"],
    h: ["h", "ساعت"], d: ["days", "روز"], rpm: ["rpm", "دور در دقیقه"], "%": ["%", "٪"],
    firmware: ["Firmware", "نرم‌افزار دستگاه"],
  };

  const locale = () => document.documentElement.lang === "fa" ? "fa" : "en";
  const local = (key) => labels[key]?.[locale() === "fa" ? 1 : 0] || key;
  const number = (value) => value.toLocaleString(locale());
  const node = (tag, text) => {
    const el = document.createElement(tag);
    if (text !== undefined) el.textContent = text;
    return el;
  };
  function field(text, input) {
    const label = node("label");
    label.append(node("span", text), input);
    return label;
  }
  function select(choices, value, disabled) {
    const input = node("select");
    choices.forEach((label, index) => {
      const option = node("option", local(label));
      option.value = index;
      input.append(option);
    });
    input.value = value;
    input.disabled = disabled;
    return input;
  }
  function button(text, action) {
    const element = node("button", local(text));
    element.className = "pill";
    element.type = "button";
    element.addEventListener("click", action);
    return element;
  }

  const vacuum = document.querySelector("#s20Details");
  const purifier = document.querySelector("#purifierDetails");
  const detailsCache = new Map();
  const notices = new Map();
  const body = (details) => details.querySelector(".xiaomi-fields");
  const status = (details) => details.querySelector(".readout");
  let vacuumPending = false;

  async function load(details) {
    if (!details.open) return;
    notices.delete(details);
    status(details).textContent = local("loading");
    try {
      const result = await api("GET", details === vacuum ? "/api/vacuum/details" : "/api/purifier/details");
      detailsCache.set(details, result);
    } catch {
      detailsCache.set(details, {available: false});
    }
    render(details);
  }

  async function change(data) {
    if (vacuumPending) return;
    vacuumPending = true;
    const fields = body(vacuum);
    fields.inert = true;
    try {
      await api("POST", "/api/vacuum/details", data);
      await load(vacuum);
      notices.set(vacuum, data.verify ? "verified" : "saved");
    } catch {
      await load(vacuum);
      notices.set(vacuum, "failed");
    } finally {
      render(vacuum);
      vacuumPending = false;
      fields.inert = false;
      vacuum.querySelector("summary").focus();
    }
  }

  function render(details) {
    const result = detailsCache.get(details);
    if (!result) return;
    const fields = body(details);
    fields.replaceChildren();
    if (result.available === false) {
      status(details).textContent = local(result.reason === "unsupported" ? "unsupported" : "unavailable");
    } else {
      status(details).textContent = local("firmware") + " " + result.firmware;
      if (details === vacuum) renderVacuum(fields, result);
      else renderPurifier(fields, result);
    }
    const notice = notices.get(details);
    if (notice) status(details).append(" · " + local(notice));
    status(details).append(" ", button("refresh", () => load(details)));
  }

  function renderVacuum(fields, result) {
    for (const [name, options] of Object.entries({mop_route: ["sPattern", "yPattern"], repeat_clean: ["off", "on"]})) {
      const {value, writable} = result.settings[name];
      const row = node("div");
      row.className = "control-row";
      if (value === null) row.append(node("p", local(name) + " · " + local("unavailable")));
      else {
        const input = select(options, value, !writable);
        row.append(field(local(name), input));
        row.append(writable
          ? button("save", () => change({setting: name, value: Number(input.value)}))
          : button("verify", () => change({setting: name, verify: true})));
      }
      fields.append(row);
    }
    renderDnd(fields, result);
    renderProfiles(fields, result);
  }

  function renderDnd(fields, result) {
    const group = node("fieldset");
    group.append(node("legend", local("dnd")));
    const get = (key) => result.settings[key].value;
    const enabled = get("dnd_enabled");
    const start = [get("dnd_start_hour"), get("dnd_start_minute")];
    const end = [get("dnd_end_hour"), get("dnd_end_minute")];
    const clock = (parts) => {
      const input = node("input");
      input.type = "time";
      input.value = parts.includes(null) ? "" : parts.map(v => String(v).padStart(2, "0")).join(":");
      input.disabled = !result.dnd_writable;
      return input;
    };
    if ([enabled, ...start, ...end].every(value => value === null)) {
      group.append(node("p", local("unavailable")));
    } else {
      const toggle = node("input");
      toggle.type = "checkbox";
      toggle.checked = enabled === 1;
      toggle.disabled = !result.dnd_writable;
      const from = clock(start);
      const until = clock(end);
      group.append(enabled === null ? node("p", local("enable") + " · " + local("unavailable")) : field(local("dnd"), toggle),
        field(local("start"), from), field(local("end"), until));
      if (result.dnd_writable) {
        group.append(button("save", () => {
          if (!from.value || !until.value) return;
          const [sh, sm] = from.value.split(":").map(Number);
          const [eh, em] = until.value.split(":").map(Number);
          change({setting: "dnd", dnd_enabled: Number(toggle.checked), dnd_start_hour: sh,
            dnd_start_minute: sm, dnd_end_hour: eh, dnd_end_minute: em});
        }));
      } else if (result.dnd_can_verify) {
        group.append(button("verify", () => change({setting: "dnd", verify: true})));
      } else {
        group.append(node("p", local("readOnly")));
      }
    }
    fields.append(group);
  }

  function renderProfiles(fields, result) {
    const group = node("fieldset");
    group.append(node("legend", local("roomProfiles")));
    if (!result.profiles) {
      group.append(node("p", local("unavailable")));
    } else {
      const options = {
        cleanMode: ["sweep", "both", "mop", "consecutive"],
        suction: ["silent", "medium", "high", "full"],
        water: ["low", "medium", "high"],
        repeat_clean: ["off", "on"],
      };
      if (!result.profiles_writable) group.append(button("verify", () => change({setting: "profiles", verify: true})));
      for (const [, room, ...values] of result.profiles) {
        const row = node("details");
        row.append(node("summary", local("room") + " " + number(room)));
        const inputs = Object.entries(options).map(([name, choices], index) => {
          const input = select(choices, values[index], !result.profiles_writable);
          row.append(field(local(name), input));
          return input;
        });
        if (result.profiles_writable) {
          row.append(button("save", () => change({setting: "profiles", room, map_id: result.map_id,
            values: inputs.map(input => Number(input.value))})));
        }
        group.append(row);
      }
    }
    fields.append(group);
  }

  function renderPurifier(fields, result) {
    for (const [name, reading] of Object.entries(result.readings)) {
      const row = node("dd");
      fields.append(node("dt", local(name)), row);
      if (reading.value === null) {
        row.textContent = local("unavailable");
      } else {
        const date = new Date(reading.read_at * 1000);
        const time = node("time", date.toLocaleString(locale()));
        time.dateTime = date.toISOString();
        row.append(number(reading.value) + " " + local(reading.unit) + " · ", time);
      }
    }
  }

  const coordination = document.querySelector("#cleaningAirDetails");
  const form = document.querySelector("#cleaningAirForm");
  const summary = document.querySelector("#cleaningAirSummary");
  const preview = document.querySelector("#cleaningAirPreview");
  const actionStatus = document.querySelector("#cleaningAirState");
  let routine = null;
  let saving = false;
  let refreshing = false;

  function previewRule() {
    const mode = local(form.elements.mode.value);
    const seconds = number(form.elements.seconds.valueAsNumber || 0);
    preview.textContent = locale() === "fa"
      ? `با شروع نظافت S20، تصفیه هوا روی حالت «${mode}» روشن می‌شود. پس از توقف یا بازگشت جارو به پایه، ${seconds} ثانیه صبر می‌کند و تنظیماتی را که تغییر دستی نداشته‌اند بازمی‌گرداند.`
      : `When the S20 starts cleaning, the purifier turns on in ${mode} mode. After the vacuum stops or returns to its dock, wait ${seconds} seconds and restore unchanged purifier settings.`;
  }

  function renderRoutine() {
    if (!routine) {
      summary.textContent = local("unavailable");
      return;
    }
    const connection = ["vacuum_offline", "purifier_offline"].includes(routine.connection)
      ? " · " + local(routine.connection) : "";
    const value = "S20 + " + local("purifier") + " · " + local(routine.phase) + connection;
    if (summary.textContent !== value) summary.textContent = value;
    previewRule();
  }

  function setRoutine(result) {
    if (!routine || !coordination.open) {
      for (const name of ["mode", "seconds"]) form.elements[name].value = result[name];
      form.elements.enabled.checked = result.enabled;
    }
    routine = result;
    form.hidden = false;
    renderRoutine();
  }

  async function refreshRoutine() {
    if (document.hidden || saving || refreshing) return;
    refreshing = true;
    try {
      const result = await api("GET", "/api/cleaning-air");
      if (!saving) setRoutine(result);
    } catch {
      summary.textContent = local("unavailable");
    } finally {
      refreshing = false;
    }
  }

  form.addEventListener("input", previewRule);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (saving) return;
    saving = true;
    try {
      setRoutine(await api("POST", "/api/cleaning-air", {mode: form.elements.mode.value,
        seconds: form.elements.seconds.valueAsNumber, enabled: form.elements.enabled.checked}));
      actionStatus.textContent = local("saved");
    } catch {
      actionStatus.textContent = local("failed");
    } finally {
      saving = false;
    }
  });

  function translate() {
    for (const el of document.querySelectorAll("[data-xiaomi]")) el.textContent = local(el.dataset.xiaomi);
    for (const details of [vacuum, purifier]) if (details.open) render(details);
    renderRoutine();
  }

  for (const details of [vacuum, purifier]) details.addEventListener("toggle", () => load(details));
  new MutationObserver(translate).observe(document.documentElement, {attributes: true, attributeFilter: ["lang"]});
  translate();
  refreshRoutine();
  setInterval(refreshRoutine, 5000);
})();

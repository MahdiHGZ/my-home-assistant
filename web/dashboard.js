"use strict";

const dashboardUI = (() => {
  const labels = {
    attention: ["Needs attention", "نیاز به توجه"],
    details: ["Details", "جزئیات"],
    stale_readings: ["Readings may be out of date", "ممکن است داده‌ها قدیمی باشند"],
    no_capture: ["No captures yet", "هنوز عکسی ثبت نشده است"],
    last_capture: ["Last capture", "آخرین عکس"],
    turn_on: ["Turn on", "روشن کردن"],
    turn_off: ["Turn off", "خاموش کردن"],
    dismiss_notice: ["Dismiss", "بستن"],
    floorplan_setup: ["Set up floor plan", "تنظیم نقشهٔ خانه"],
    floorplan_image: ["Floor plan image", "تصویر نقشهٔ خانه"],
    remove_image: ["Remove image", "حذف تصویر"],
    floorplan_hint: ["Drag a marker or use the position fields in the device list.", "نشانگر را بکشید یا از فیلدهای موقعیت در فهرست دستگاه‌ها استفاده کنید."],
    room_filter: ["Show room", "نمایش اتاق"],
    all_rooms: ["All rooms", "همهٔ اتاق‌ها"],
    room_name: ["Room name", "نام اتاق"],
    position_x: ["Horizontal position (%)", "موقعیت افقی (٪)"],
    position_y: ["Vertical position (%)", "موقعیت عمودی (٪)"],
    floorplan_error: ["Choose a PNG, JPEG or WebP image up to 2 MB.", "تصویر PNG، JPEG یا WebP تا ۲ مگابایت انتخاب کنید."],
    storage_error: ["This browser could not save the settings.", "مرورگر نتوانست تنظیمات را ذخیره کند."],
    preferences: ["Dashboard settings", "تنظیمات داشبورد"],
    theme: ["Theme", "پوسته"],
    theme_system: ["System", "سیستم"],
    theme_light: ["Light", "روشن"],
    theme_dark: ["Dark", "تیره"],
    bulb_accent: ["Use active bulb color as accent", "استفاده از رنگ لامپ روشن به‌عنوان رنگ تأکیدی"],
    time_order: ["Order cards by time of day", "مرتب‌سازی کارت‌ها بر اساس زمان روز"],
    key_map: ["Keyboard shortcuts and keypad map", "میان‌برها و نقشهٔ صفحه‌کلید عددی"],
    dashboard_keys: ["Dashboard shortcuts", "میان‌برهای داشبورد"],
    physical_keys: ["Physical keypad", "صفحه‌کلید عددی"],
    print_keys: ["Print key map", "چاپ نقشهٔ کلیدها"],
    key_vacuum_toggle: ["Start cleaning or dock", "شروع نظافت یا بازگشت"],
    key_bright_up: ["Increase brightness", "افزایش روشنایی"],
    key_bright_down: ["Decrease brightness", "کاهش روشنایی"],
    key_countdown: ["Countdown and capture with flash", "شمارش معکوس و عکس با فلاش"],
    key_unavailable: ["Keypad map is unavailable.", "نقشهٔ صفحه‌کلید در دسترس نیست."],
    lighting_library: ["Groups and favorite scenes", "گروه‌ها و حالت‌های دلخواه"],
    save_group: ["Save group", "ذخیرهٔ گروه"],
    save_scene: ["Save current scene", "ذخیرهٔ حالت فعلی"],
    library_hint: ["Saves the selected bulbs, or all when none are selected.", "لامپ‌های انتخاب‌شده را ذخیره می‌کند، یا اگر انتخابی نیست همهٔ لامپ‌ها را ذخیره می‌کند."],
    missing_bulbs: ["Removed bulbs", "لامپ‌های حذف‌شده"],
    no_bulbs: ["No bulbs registered", "لامپی ثبت نشده است"],
    fault_code: ["Fault code", "کد خطا"],
    unknown_status: ["Unknown status", "وضعیت ناشناخته"],
    advanced_settings: ["Advanced settings", "تنظیمات پیشرفته"],
  };
  const routes = {
    lights: ["lights", "#lightsCard h2"],
    vacuum: ["appliances", "#vacuumCard h2"],
    air: ["appliances", "#airCard h2"],
    camera: ["camera", "#cameraCard h2"],
  };
  const shortcutTabs = { H: "home", L: "lights", D: "appliances", C: "camera", R: "rooms" };
  const statIds = { lights: "statLights", vacuum: "statVacuum", air: "statAir", camera: "statCamera" };
  const cleaning = new Set(["sweeping", "mopping", "sweeping and mopping", "building map"]);
  const preferenceKey = "coukab-dashboard-preferences";
  const roomKey = "coukab-room-devices";
  const imageKey = "coukab-floorplan";
  let showTab;
  let receivedAt = 0;
  let snapshot;
  let library = { groups: [], scenes: [] };
  let roomDevices = [];
  let positions = {};
  let prefs = {};

  function load(key, fallback) {
    try { return JSON.parse(localStorage.getItem(key)) || fallback; }
    catch (_) { return fallback; }
  }

  function save(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); }
    catch (_) { toast(t("storage_error"), "bad"); return false; }
    return true;
  }

  function fault(value) {
    return typeof value === "number"
      ? `${t("fault_code")} ${value.toLocaleString(lang)}`
      : trName(value);
  }

  function applyAccent() {
    const active = prefs.accent && snapshot?.lights?.bulbs?.find(bulb => bulb.ok && bulb.current_power === "on");
    const accent = active ? bulbColor(active) : "";
    if (accent && /^#[0-9a-f]{6}$/i.test(accent)) {
      document.documentElement.style.setProperty("--bulb-color", accent);
      document.documentElement.dataset.bulbAccent = "on";
    } else {
      document.documentElement.style.removeProperty("--bulb-color");
      delete document.documentElement.dataset.bulbAccent;
    }
  }

  function applyPreferences() {
    document.documentElement.dataset.theme = prefs.theme || "system";
    $("#dashboardTheme").value = prefs.theme || "system";
    $("#bulbAccent").checked = !!prefs.accent;
    $("#timeOrder").checked = !!prefs.timeOrder;
    applyAccent();
    orderOverview();
  }

  function orderOverview(urgent = []) {
    const hour = new Date().getHours();
    const base = !prefs.timeOrder
      ? ["lights", "vacuum", "air", "camera"]
      : hour >= 18 || hour < 6 ? ["lights", "camera", "air", "vacuum"]
        : hour < 12 ? ["air", "vacuum", "lights", "camera"]
          : ["vacuum", "air", "lights", "camera"];
    const grid = $(".dashboard");
    const order = [...new Set([...urgent, ...base])].map(device => statIds[device]);
    if (order.every((id, index) => grid.children[index]?.id === id) || grid.contains(document.activeElement)) return;
    for (const id of order) grid.append(document.getElementById(id));
  }

  function renderOverview() {
    if (!snapshot) return;
    const { lights = {}, vacuum = {}, purifier = {}, moments = {} } = snapshot;
    const alerts = [];
    const urgent = [];
    const alert = (device, text) => { alerts.push(text); if (device) urgent.push(device); };
    const faulted = vacuum.fault && Number(vacuum.fault) !== 0;
    if (vacuum.available && faulted) alert("vacuum", `${t("vacuum")} · ${fault(vacuum.fault)}`);
    if (!vacuum.available) alert("vacuum", `${t("vacuum")} · ${t("offline")}`);
    if (!purifier.available) alert("air", `${t("air_purifier")} · ${t("offline")}`);
    if (!lights.available || lights.ok === false) alert("lights", `${t("lights")} · ${t("offline")}`);
    if (snapshot.camera?.available === false) alert("camera", `${t("camera")} · ${t("offline")}`);
    if (snapshot._meta?.stale || Date.now() - receivedAt + (snapshot._meta?.age_ms || 0) >= 20000) {
      alert(null, t("stale_readings"));
    }
    const list = $("#attentionList");
    if (list.dataset.alerts !== JSON.stringify(alerts)) {
      list.replaceChildren(...alerts.map(text => element("li", "", text)));
      list.dataset.alerts = JSON.stringify(alerts);
    }
    $("#overviewAttention").hidden = !alerts.length;
    const working = vacuum.available && cleaning.has(String(vacuum.status).toLowerCase());
    $("#overviewPause").hidden = !working;
    $("#overviewDock").hidden = !vacuum.available || (!working && String(vacuum.status).toLowerCase() !== "paused");
    if (working) urgent.push("vacuum");
    const on = purifier.power === "ON";
    const power = $("#overviewAirPower");
    power.textContent = t(on ? "turn_off" : "turn_on");
    power.dataset.airPower = on ? "off" : "on";
    power.hidden = !purifier.available;
    if (typeof vacuum.status === "number" && vacuum.available) {
      setStat("#statVacuum", `${t("unknown_status")} ${vacuum.status.toLocaleString(lang)}`);
    }
    const valid = moments.latest && Number.isFinite(moments.latest_at);
    const minutes = valid ? Math.max(0, Math.floor((Date.now() / 1000 - moments.latest_at) / 60)) : 0;
    const [amount, unit] = minutes < 60 ? [minutes, "minute"]
      : minutes < 1440 ? [Math.floor(minutes / 60), "hour"]
        : [Math.floor(minutes / 1440), "day"];
    const relative = new Intl.RelativeTimeFormat(lang, { numeric: "auto" });
    setStat("#statCamera", valid ? `${t("last_capture")} · ${relative.format(-amount, unit)}`
      : moments.latest ? t("last_capture") : t("no_capture"));
    const scene = $("#overviewScene");
    scene.disabled = !lights.available || lights.ok === false;
    orderOverview(urgent);
  }

  function actionButton(label, fn) {
    const button = element("button", "pill", label);
    button.type = "button";
    button.onclick = fn;
    return button;
  }

  function routeDevice(device, bulb) {
    if (bulb) selectBulbs([bulb]);
    showTab(...routes[device]);
  }

  function selectBulbs(bulbs) {
    selectedBulbs.clear();
    for (const bulb of bulbs) selectedBulbs.add(bulb);
    for (const cell of $("#bulbGrid").children) {
      const selected = selectedBulbs.has(cell.dataset.name);
      cell.classList.toggle("selected", selected);
      if (cell instanceof HTMLButtonElement) cell.setAttribute("aria-pressed", String(selected));
    }
  }

  function missing(entry, current) {
    return [...new Set([...entry.missing, ...entry.bulbs.filter(bulb => !current.includes(bulb))])];
  }

  function renderLibrary() {
    if (!caps) return;
    const current = snapshot?.lights?.registered_names || caps.bulbs || [];
    const rows = [];
    for (const [kind, entries] of Object.entries({ group: library.groups, scene: library.scenes })) {
      for (const entry of entries) {
        const removed = missing(entry, current);
        const row = element("div", "lighting-entry");
        const recall = actionButton(entry.name, async (event) => {
          if (kind === "group") {
            selectBulbs(entry.bulbs.filter(bulb => !removed.includes(bulb)));
            $("#targetsOn").focus();
          } else {
            const result = await act("POST", "/api/lights/library/recall", { name: entry.name }, { btn: event.currentTarget });
            if (result) {
              const message = result.missing?.length ? `${t("missing_bulbs")} · ${result.missing.join(", ")}` : t("done");
              toast(message, "good");
            }
          }
        });
        recall.disabled = removed.length === entry.bulbs.length;
        recall.setAttribute("aria-label", `${t(kind === "group" ? "target" : "run")} ${entry.name}`);
        row.append(recall, actionButton(t("remove"), async () => {
          if (await act("POST", "/api/lights/library/delete", { kind, name: entry.name })) await refreshLibrary();
        }));
        row.append(element("p", "hint", entry.bulbs.join(", ")));
        if (removed.length) row.append(element("p", "hint missing-bulbs", `${t("missing_bulbs")} · ${removed.join(", ")}`));
        rows.push(row);
      }
    }
    $("#lightingEntries").replaceChildren(...rows);
    const preferred = library.scenes.find(entry => missing(entry, current).length < entry.bulbs.length);
    const builtIn = (caps.scene_modes || []).find(mode => mode.key === "warm_white") || caps.scene_modes?.[0];
    const quick = $("#overviewScene");
    quick.hidden = !preferred && !builtIn;
    quick.textContent = preferred?.name || trName(builtIn?.name) || "";
    quick.onclick = (event) => preferred
      ? act("POST", "/api/lights/library/recall", { name: preferred.name }, { btn: event.currentTarget })
      : act("POST", "/api/lights/action", { action: "mode", mode: builtIn.key }, { btn: event.currentTarget });
  }

  async function refreshLibrary() {
    try {
      library = await api("GET", "/api/lights/library");
      renderLibrary();
    } catch (error) { toast(error.message, "bad"); }
  }

  function roomLabel(device) {
    return device.bulb || t(device.type === "air" ? "air_purifier" : device.type);
  }

  function renderRoomFilter() {
    const filter = $("#roomFilter");
    const selected = filter.value;
    const rooms = [...new Set(roomDevices.map(device => positions[device.key].room).filter(Boolean))].sort();
    filter.replaceChildren(new Option(t("all_rooms"), ""), ...rooms.map(room => new Option(room, room)));
    if (rooms.includes(selected)) filter.value = selected;
  }

  function renderRooms() {
    const list = $("#roomDeviceList");
    const markers = $("#roomMarkers");
    const room = $("#roomFilter").value;
    list.replaceChildren();
    markers.replaceChildren();
    for (const device of roomDevices) {
      const position = positions[device.key];
      if (room && position.room !== room) continue;
      const label = roomLabel(device);
      const item = element("li", "room-device");
      const controls = element("div", "btn-row wrap");
      controls.append(actionButton(label, () => routeDevice(device.type, device.bulb)));
      if (device.bulb) {
        for (const [text, power] of [["on", true], ["off", false]]) {
          const button = actionButton(t(text), (event) => act("POST", "/api/lights/control", {
            targets: [device.bulb], power,
          }, { btn: event.currentTarget }));
          button.setAttribute("aria-label", `${label} · ${t(text)}`);
          controls.append(button);
        }
      }
      item.append(controls);
      const settings = element("details", "room-device-settings");
      settings.append(element("summary", "", position.room || t("edit")));
      const marker = actionButton(label, () => routeDevice(device.type, device.bulb));
      marker.classList.add("room-marker");
      marker.tabIndex = -1;
      const updateMarker = () => {
        marker.style.left = position.x + "%";
        marker.style.top = position.y + "%";
      };
      updateMarker();
      const fields = {};
      for (const [field, key] of [["room", "room_name"], ["x", "position_x"], ["y", "position_y"]]) {
        const input = element("input");
        input.value = position[field];
        fields[field] = input;
        if (field === "room") {
          input.maxLength = 48;
        } else {
          input.type = "number";
          input.min = 0;
          input.max = 100;
          input.step = "any";
          input.required = true;
        }
        input.onchange = () => {
          if (!input.reportValidity()) { input.value = position[field]; return; }
          position[field] = field === "room" ? input.value.trim() : Number(input.value);
          save(roomKey, positions);
          updateMarker();
          if (field === "room") {
            settings.querySelector("summary").textContent = position.room || t("edit");
            const wasFiltered = !!$("#roomFilter").value;
            renderRoomFilter();
            if (wasFiltered) renderRooms();
          }
        };
        const wrapper = element("label", "", t(key));
        wrapper.append(input);
        settings.append(wrapper);
      }
      item.append(settings);
      wireMarker(marker, position, fields, updateMarker);
      markers.append(marker);
      list.append(item);
    }
  }

  function wireMarker(marker, position, fields, update) {
    let start;
    let moved = false;
    marker.addEventListener("pointerdown", event => {
      if (event.button !== 0) return;
      start = { id: event.pointerId, x: event.clientX, y: event.clientY };
      moved = false;
      marker.setPointerCapture(event.pointerId);
    });
    marker.addEventListener("pointermove", event => {
      if (!start || start.id !== event.pointerId ||
          (!moved && Math.hypot(event.clientX - start.x, event.clientY - start.y) < 5)) return;
      moved = true;
      const box = $("#roomMap").getBoundingClientRect();
      position.x = Math.max(0, Math.min(100, Math.round(100 * (event.clientX - box.left) / box.width)));
      position.y = Math.max(0, Math.min(100, Math.round(100 * (event.clientY - box.top) / box.height)));
      fields.x.value = position.x;
      fields.y.value = position.y;
      update();
    });
    const release = () => {
      start = null;
      if (moved) save(roomKey, positions);
    };
    marker.addEventListener("pointerup", release);
    marker.addEventListener("pointercancel", release);
    marker.addEventListener("click", (event) => {
      if (moved) { event.stopImmediatePropagation(); moved = false; }
    }, { capture: true });
  }

  function setRoomImage(image) {
    const allowed = typeof image === "string" && /^data:image\/(png|jpeg|webp);base64,/i.test(image);
    $("#roomMap").hidden = !allowed;
    $("#floorplanClear").disabled = !allowed;
    if (allowed) $("#floorplanImage").src = image;
    else $("#floorplanImage").removeAttribute("src");
  }

  function wireRooms() {
    positions = load(roomKey, {});
    roomDevices = [
      ...(caps.bulbs || []).map(bulb => ({ key: `bulb:${bulb}`, type: "lights", bulb })),
      ...["vacuum", "air", "camera"].map(type => ({ key: type, type })),
    ];
    roomDevices.forEach((device, i) => {
      const old = positions[device.key];
      positions[device.key] = {
        room: typeof old?.room === "string" ? old.room : "",
        x: Number.isFinite(old?.x) ? old.x : 25 + (i % 3) * 25,
        y: Number.isFinite(old?.y) ? old.y : 100 * (Math.floor(i / 3) + 1) / (Math.ceil(roomDevices.length / 3) + 1),
      };
    });
    $("#roomFilter").onchange = renderRooms;
    setRoomImage(load(imageKey, ""));
    $("#floorplanClear").onclick = () => { if (save(imageKey, "")) setRoomImage(""); };
    $("#floorplanInput").onchange = (event) => {
      const file = event.currentTarget.files[0];
      event.currentTarget.value = "";
      if (!file) return;
      if (!["image/png", "image/jpeg", "image/webp"].includes(file.type) || file.size > 2 * 1024 * 1024) {
        toast(t("floorplan_error"), "bad");
        return;
      }
      const reader = new FileReader();
      reader.onload = () => { if (save(imageKey, reader.result)) setRoomImage(reader.result); };
      reader.onerror = () => toast(t("floorplan_error"), "bad");
      reader.readAsDataURL(file);
    };
  }

  function renderKeys() {
    const tabLabel = tab => t(tab === "appliances" ? "devices" : tab);
    function keyLine(key, name) {
      const li = element("li");
      const glyph = element("kbd", "", key);
      glyph.dir = "ltr";
      li.append(glyph, ` ${name}`);
      return li;
    }
    $("#dashboardKeys").replaceChildren(...Object.entries(shortcutTabs).map(([key, tab]) =>
      keyLine(`Shift + Alt + ${key}`, tabLabel(tab))));
    $("#physicalKeys").replaceChildren(...(caps?.keypad || []).map(binding =>
      keyLine((binding.combo ? "KP0 + " : "") + binding.code,
        binding.label ? t(binding.label) : trName(binding.mode))));
    if (!caps?.keypad?.length) $("#physicalKeys").append(element("li", "", t("key_unavailable")));
  }

  function applyLanguage() {
    for (const button of document.querySelectorAll(".nav-tab")) {
      button.removeAttribute("aria-label");
    }
    $("#langBtn").setAttribute("aria-label", lang === "en" ? "فارسی" : "English");
    $("#refreshBtn").setAttribute("aria-label", lang === "en" ? "Refresh status" : "تازه‌سازی وضعیت");
    for (const button of document.querySelectorAll("[data-overview-device]")) {
      button.setAttribute("aria-label", `${t("details")} · ${t(button.dataset.overviewDevice)}`);
    }
    if (!showTab) return;
    renderRoomFilter();
    renderRooms();
    renderKeys();
    renderLibrary();
    renderOverview();
  }

  function init(navigate) {
    showTab = navigate;
    prefs = load(preferenceKey, {});
    for (const [id, key] of [["dashboardTheme", "theme"], ["bulbAccent", "accent"], ["timeOrder", "timeOrder"]]) {
      const input = document.getElementById(id);
      input.onchange = () => {
        prefs[key] = input.type === "checkbox" ? input.checked : input.value;
        save(preferenceKey, prefs);
        applyPreferences();
        renderOverview();
      };
    }
    applyPreferences();
    document.querySelectorAll("[data-overview-device]").forEach(button => {
      button.onclick = () => routeDevice(button.dataset.overviewDevice);
    });
    const advanced = actionButton(t("advanced_settings"), () => {
      $("#s20Details").open = true;
      showTab("appliances", "#s20Details summary");
    });
    advanced.dataset.i18n = "advanced_settings";
    $("#statVacuum .overview-actions").append(advanced);
    document.addEventListener("keydown", event => {
      if (!event.altKey || !event.shiftKey || event.ctrlKey || event.metaKey || event.repeat || event.isComposing ||
          event.target.closest?.("input, select, textarea, [contenteditable]")) return;
      const key = event.code.startsWith("Key") ? event.code.slice(3) : event.key.toUpperCase();
      if (!shortcutTabs[key]) return;
      event.preventDefault();
      showTab(shortcutTabs[key], `.nav-tab[data-tab="${shortcutTabs[key]}"]`);
    });
    for (const [key, tab] of Object.entries(shortcutTabs)) {
      $(`.nav-tab[data-tab="${tab}"]`).setAttribute("aria-keyshortcuts", `Alt+Shift+${key}`);
    }
    $("#printKeyMap").onclick = () => window.print();
    $("#lightingLibraryForm").onsubmit = async (event) => {
      event.preventDefault();
      const bulbs = selectedBulbs.size ? [...selectedBulbs] : (snapshot?.lights?.registered_names || caps.bulbs);
      if (!bulbs?.length) { toast(t("no_bulbs"), "bad"); return; }
      const name = $("#lightingName").value;
      const button = event.submitter;
      const kind = button?.value || "group";
      if (await act("POST", "/api/lights/library/save", { kind, name, bulbs }, { btn: button })) {
        $("#lightingName").value = "";
        await refreshLibrary();
      }
    };
    wireRooms();
    applyLanguage();
    refreshLibrary();
    setInterval(renderOverview, 5000);
  }

  function render(next) {
    const oldBulbs = snapshot?.lights?.registered_names || caps?.bulbs || [];
    if (snapshot !== next) receivedAt = Date.now();
    snapshot = next;
    if (JSON.stringify(oldBulbs) !== JSON.stringify(next?.lights?.registered_names || caps?.bulbs || [])) renderLibrary();
    renderOverview();
    applyAccent();
  }

  // The translation table lives here but is installed after app.js defines I18N.
  function installStrings() {
    for (const [key, [english, farsi]] of Object.entries(labels)) {
      I18N.en[key] = english;
      I18N.fa[key] = farsi;
    }
    Object.assign(FA_NAMES, {
      "maximum brightness": "حداکثر روشنایی", "max bright": "حداکثر روشنایی",
      "sweep then mop": "جارو و سپس تی", "edge": "لبه‌ها", "point": "نقطه‌ای",
      "remote": "هدایت از راه دور", "explore": "اکتشاف", "room": "اتاق",
      "floor": "طبقه", "global": "سراسری", "returning to dock": "بازگشت به پایه",
      "s pattern": "مسیر S شکل", "y pattern": "مسیر Y شکل",
      "error": "خطا", "stuck": "گیر کرده", "offline": "آفلاین",
    });
  }

  return { init, render, applyLanguage, fault, installStrings };
})();

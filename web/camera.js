(() => {
  const root = document.querySelector("#cameraTools");
  if (!root) return;
  const strings = {
    en: {
      preview: "Fresh preview", settings: "Camera settings", windows: "Daily private intervals (HH:MM-HH:MM, one per line)",
      override: "Capture policy", schedule: "Follow schedule", private: "Block capture", allow: "Ignore local schedule",
      motion: "Save a moment when motion is detected", cooldown: "Motion cooldown in seconds",
      interval: "Timelapse interval in minutes (0 disables capture)", export: "Download timelapse frames", save: "Save settings",
      ptz: "Check camera movement", unsupported: "Camera movement unavailable", active: "Camera capture active",
      idle: "Camera background capture idle", unavailable: "Camera unavailable", settings_error: "Camera settings unavailable",
      scheduledPrivate: "Camera capture blocked", frames: "Retained frames", missed: "Missed frames", motionEvent: "Motion",
      up: "Tilt up", down: "Tilt down", left: "Pan left", right: "Pan right", saved: "Camera settings saved",
      rule: "When a person is detected", notify: "Gallery notification only", lights_on: "Turn on household lights",
      vacuum_pause: "Pause vacuum", dailyDigest: "Enable daily digest", digest: "View today’s digest",
      search: "Search gallery", searchLabel: "Caption or date", caption: "Caption", saveCaption: "Save caption",
      sweep: "Capture selected angles (2–4 presets)", cancelSweep: "Cancel multi-angle capture",
      running: "Multi-angle capture running", done: "Multi-angle capture complete",
      cancelled: "Multi-angle capture cancelled", failedSweep: "Multi-angle capture failed",
      person: "Person detected", unknown: "Unclassified motion", applied: "Action applied",
      presence: "Presence observations", lastSeen: "Last seen", notDetected: "No person detected", presenceUnknown: "Unknown",

      actionFailed: "Device action failed", digestActions: "Device actions", digestMoments: "Retained moments",
      failed: "Camera request failed", previewAlt: "Fresh camera preview", clear: "Close preview",
    },
    fa: {
      preview: "پیش‌نمایش تازه", settings: "تنظیمات دوربین", windows: "بازه‌های روزانه حریم خصوصی (HH:MM-HH:MM، هر بازه در یک خط)",
      override: "سیاست ثبت تصویر", schedule: "طبق برنامه", private: "مسدود کردن ثبت تصویر", allow: "نادیده گرفتن برنامه محلی",
      motion: "ذخیره عکس هنگام تشخیص حرکت", cooldown: "فاصله بین هشدارهای حرکت به ثانیه",
      interval: "فاصله ثبت تایم‌لپس به دقیقه (۰ برای غیرفعال کردن)", export: "دریافت فریم‌های تایم‌لپس", save: "ذخیره تنظیمات",
      ptz: "بررسی امکان چرخش دوربین", unsupported: "چرخش دوربین در دسترس نیست", active: "ثبت تصویر فعال است",
      idle: "ثبت تصویر در پس‌زمینه غیرفعال است", unavailable: "دوربین در دسترس نیست", settings_error: "تنظیمات دوربین در دسترس نیست",
      scheduledPrivate: "ثبت تصویر مسدود است", frames: "فریم‌های ذخیره‌شده", missed: "فریم‌های از دست رفته", motionEvent: "حرکت",
      up: "چرخش به بالا", down: "چرخش به پایین", left: "چرخش به چپ", right: "چرخش به راست", saved: "تنظیمات دوربین ذخیره شد",
      rule: "هنگام تشخیص فرد", notify: "فقط اعلان در گالری", lights_on: "روشن کردن چراغ‌های خانه",
      vacuum_pause: "مکث جاروبرقی", dailyDigest: "فعال کردن خلاصه روزانه", digest: "مشاهده خلاصه امروز",
      search: "جست‌وجوی گالری", searchLabel: "شرح یا تاریخ عکس", caption: "شرح عکس", saveCaption: "ذخیره شرح",
      sweep: "ثبت زاویه‌های انتخابی (۲ تا ۴ موقعیت)", cancelSweep: "لغو ثبت چندزاویه‌ای",
      running: "در حال ثبت چندزاویه‌ای", done: "ثبت چندزاویه‌ای کامل شد",
      cancelled: "ثبت چندزاویه‌ای لغو شد", failedSweep: "ثبت چندزاویه‌ای ناموفق بود",
      person: "فرد شناسایی شد", unknown: "حرکت نامشخص", applied: "فرمان اجرا شد",
      presence: "مشاهدات حضور", lastSeen: "آخرین مشاهده", notDetected: "فردی شناسایی نشد", presenceUnknown: "نامشخص",

      actionFailed: "فرمان دستگاه ناموفق بود", digestActions: "فرمان‌های دستگاه‌ها", digestMoments: "عکس‌های ذخیره‌شده",
      failed: "درخواست دوربین ناموفق بود", previewAlt: "پیش‌نمایش تازه دوربین", clear: "بستن پیش‌نمایش",
    },
  };
  const translate = (key) => strings[document.documentElement.lang === "fa" ? "fa" : "en"][key];
  const localized = new Map();
  function element(tag, parent, key) {
    const node = document.createElement(tag);
    if (key) {
      localized.set(node, key);
      node.textContent = translate(key);
    }
    parent.append(node);
    return node;
  }
  function button(parent, key, run) {
    const node = element("button", parent, key);
    node.type = "button";
    node.className = "pill";
    node.addEventListener("click", async () => {
      node.disabled = true;
      message.textContent = "";
      try {
        await run();
      } catch {
        message.textContent = translate("failed");
      } finally {
        node.disabled = node === previewButton && !!lastStatus?.private;
      }
    });
    return node;
  }
  function field(form, key, tag, options) {
    const row = element("p", form);
    const label = element("label", row);
    element("span", label, key);
    label.append(" ");
    return Object.assign(element(tag, label), options);
  }

  const message = element("p", root);
  message.setAttribute("role", "status");
  const preview = element("div", root);
  const previewButton = button(root, "preview", async () => {
    const data = await api("POST", "/api/camera/preview", {});
    preview.replaceChildren();
    const image = element("img", preview);
    image.src = "data:image/jpeg;base64," + data.jpeg;
    image.alt = translate("previewAlt");
    image.style.maxWidth = "100%";
    closePreview.hidden = false;
  });
  const closePreview = button(root, "clear", () => {
    preview.replaceChildren();
    closePreview.hidden = true;
  });
  closePreview.hidden = true;

  const details = element("details", root);
  element("summary", details, "settings");
  const form = element("div", details);
  const state = element("p", form);
  const windows = field(form, "windows", "textarea", {rows: 3, dir: "ltr"});
  windows.style.maxWidth = "100%";
  const override = field(form, "override", "select", {});
  for (const key of ["schedule", "private", "allow"]) {
    element("option", override, key).value = key;
  }
  const motion = field(form, "motion", "input", {type: "checkbox"});
  const cooldown = field(form, "cooldown", "input", {type: "number", min: 15, max: 3600});
  const interval = field(form, "interval", "input", {type: "number", min: 0, max: 1440});
  const rule = field(form, "rule", "select", {});
  for (const key of ["notify", "lights_on", "vacuum_pause"]) {
    element("option", rule, key).value = key;
  }
  const dailyDigest = field(form, "dailyDigest", "input", {type: "checkbox"});
  const save = button(form, "save", async () => {
    if (!cooldown.reportValidity() || !interval.reportValidity()) return;
    const data = await api("POST", "/api/camera/settings", {
      windows: windows.value.split("\n").map(line => line.trim()).filter(Boolean).map(line => line.split("-").map(t => t.trim())),
      override: override.value,
      motion: motion.checked,
      cooldown_seconds: Number(cooldown.value),
      timelapse_minutes: Number(interval.value),
      presence_rule: rule.value,
      daily_digest: dailyDigest.checked,
    });
    refresh(data);
    message.textContent = translate("saved");
  });
  save.disabled = true;
  const archive = element("a", form, "export");
  archive.href = "/api/camera/timelapse.zip";
  archive.hidden = true;
  const digestText = element("div", form);
  const digest = button(form, "digest", async () => {
    const data = await api("GET", "/api/camera/digest");
    const count = Object.values(data.device_actions).reduce((sum, value) => sum + value, 0);
    digestText.replaceChildren(
      `${data.date} · ${translate("digestMoments")} ${data.moments.length} · ${translate("digestActions")} ${count}`,
    );
    const photos = element("div", digestText);
    photos.className = "gallery";
    for (const moment of data.moments) {
      const path = "/moments/" + moment.name;
      if (!isMomentPath(path)) continue;
      const tile = momentTile(path);
      if (moment.caption) tile.querySelector("img").alt = moment.caption;
      photos.append(tile);
    }
  });
  digest.hidden = true;
  const sweepState = element("p", form);
  const cancelSweep = button(form, "cancelSweep", () => api("POST", "/api/camera/sweep/cancel", {}));
  cancelSweep.hidden = true;
  const movement = element("div", form);
  button(form, "ptz", async () => {
    for (const node of movement.children) localized.delete(node);
    movement.replaceChildren();
    const data = await api("GET", "/api/camera/ptz");
    if (!data.supported) {
      element("p", movement, "unsupported");
      return;
    }
    for (const direction of ["up", "down", "left", "right"]) {
      button(movement, direction, () => api("POST", "/api/camera/ptz", {direction}));
    }
    const selected = [];
    for (const [preset, name] of Object.entries(data.presets || {})) {
      const label = element("label", movement);
      const checkbox = element("input", label);
      checkbox.type = "checkbox";
      selected.push({preset, checkbox});
      label.append(name, " ");
      const go = button(movement, null, () => api("POST", "/api/camera/ptz", {preset}));
      go.textContent = name;
    }
    if (selected.length >= 2) {
      button(movement, "sweep", async () => {
        const presets = selected.filter(item => item.checkbox.checked).map(item => item.preset);
        await api("POST", "/api/camera/sweep", {presets});
        refresh(await api("GET", "/api/camera/settings"));
      });
    }
  });
  const presence = element("details", form);
  presence.hidden = true;
  element("summary", presence, "presence");
  const lastSeen = element("p", presence);
  const intervals = element("ul", presence);
  const events = element("ul", form);
  const search = element("details", root);
  element("summary", search, "search");
  const query = field(search, "searchLabel", "input", {type: "search", maxLength: 240});
  const results = element("ul", search);
  let searchSequence = 0;
  async function find() {
    const sequence = ++searchSequence;
    const data = await api("GET", "/api/camera/search?q=" + encodeURIComponent(query.value));
    if (sequence !== searchSequence) return;
    for (const node of results.querySelectorAll("label span, button")) localized.delete(node);
    results.replaceChildren();
    for (const item of data.matches) {
      const path = "/moments/" + item.name;
      if (!isMomentPath(path)) continue;
      const row = element("li", results);
      const link = element("a", row);
      link.href = path;
      link.textContent = item.name;
      link.target = "_blank";
      link.rel = "noopener";
      const caption = field(row, "caption", "input", {maxLength: 240, value: item.caption});
      const submit = element("button", row, "saveCaption");
      submit.type = "button";
      submit.className = "pill";
      submit.addEventListener("click", async () => {
        try {
          await api("POST", "/api/camera/caption", {name: item.name, caption: caption.value});
          await find();
        } catch {
          message.textContent = translate("failed");
        }
      });
    }
  }
  button(search, "search", find);
  search.addEventListener("toggle", () => {
    if (search.open) find().catch(() => { message.textContent = translate("failed"); });
  });

  let lastStatus = null;

  function refresh(data) {
    lastStatus = data;
    previewButton.disabled = data.private;
    if (data.private) {
      preview.replaceChildren();
      closePreview.hidden = true;
      for (const node of movement.children) localized.delete(node);
      movement.replaceChildren();
    }
    const label = data.private || data.state === "private" ? "scheduledPrivate" : data.state;
    state.textContent = `${translate(label)} · ${translate("frames")} ${data.timelapse_frames} · ${translate("missed")} ${data.timelapse_missed}`;
    archive.hidden = !data.timelapse_frames;
    digest.hidden = !data.settings.daily_digest;
    if (digest.hidden) digestText.textContent = "";
    cancelSweep.hidden = data.sweep.state !== "running";
    sweepState.textContent = data.sweep.state === "idle" ? ""
      : translate(data.sweep.state === "failed" ? "failedSweep" : data.sweep.state);
    presence.hidden = !data.presence.intervals.length;
    lastSeen.textContent = data.presence.last_seen
      ? `${translate("lastSeen")} · ${data.presence.last_seen.replace("T", " ")}` : "";
    intervals.replaceChildren();
    for (const interval of data.presence.intervals.slice(0, 10)) {
      const key = {person: "person", not_detected: "notDetected", unknown: "presenceUnknown"}[interval.state];
      const start = interval.started.replace("T", " ");
      const end = interval.last_observed.replace("T", " ");
      element("li", intervals).textContent = `${translate(key)} · ${start}${start === end ? "" : " – " + end}`;
    }
    events.replaceChildren();
    for (const event of data.events) {
      const row = element("li", events);
      const link = element("a", row);
      if (isMomentPath(event.moment)) link.href = event.moment;
      link.textContent = `${translate(event.kind === "person" ? "person" : "unknown")} · ${event.time.replace("T", " ")}`;
      if (event.outcome === "applied") row.append(" · " + translate("applied"));
      if (event.outcome === "failed") row.append(" · " + translate("actionFailed"));
    }
  }
  async function load() {
    try {
      const data = await api("GET", "/api/camera/settings");
      windows.value = data.settings.windows.map(row => row.join("-")).join("\n");
      override.value = data.settings.override;
      motion.checked = data.settings.motion;
      cooldown.value = data.settings.cooldown_seconds;
      interval.value = data.settings.timelapse_minutes;
      rule.value = data.settings.presence_rule;
      dailyDigest.checked = data.settings.daily_digest;
      save.disabled = false;
      refresh(data);
    } catch {
      message.textContent = translate("failed");
    }
  }
  details.addEventListener("toggle", () => {
    if (details.open) load();
  });
  setInterval(async () => {
    if (document.hidden || (!details.open && closePreview.hidden)) return;
    try {
      refresh(await api("GET", "/api/camera/settings"));
    } catch {
      state.textContent = translate("settings_error");
    }
  }, 5000);
  new MutationObserver(() => {
    for (const [node, key] of localized) node.textContent = translate(key);
    if (lastStatus) refresh(lastStatus);
  }).observe(document.documentElement, {attributes: true, attributeFilter: ["lang"]});
})();

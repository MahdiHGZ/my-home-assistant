"use strict";

const POLL_MS = 5000;          // fallback polling when SSE is down
const SSE_RETRY_MS = 5000;
const $ = (sel) => document.querySelector(sel);

let caps = null;
let lastStatus = null;
const selectedBulbs = new Set();
const selectedRooms = new Set();
let polling = false;
let statusRevision = 0;
let statusReceivedAt = 0;
let driveEpoch = null;
let sseConnected = false;
let eventSource = null;
let chatMessagesSent = 0;

// --- icons (Lucide-style, inlined so the page works fully offline) ----------

const ICONS = {
  home: '<path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M9 22V12h6v10"/>',
  refresh: '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>',
  bulb: '<path d="M15 14c.2-1 .7-1.7 1.5-2.5 1-.9 1.5-2.2 1.5-3.5A6 6 0 0 0 6 8c0 1 .2 2.2 1.5 3.5.7.7 1.3 1.5 1.5 2.5"/><path d="M9 18h6"/><path d="M10 22h4"/>',
  vacuum: '<circle cx="12" cy="12" r="9"/><path d="M4 13h16"/><circle cx="12" cy="8" r="1.3" fill="currentColor"/>',
  wind: '<path d="M12.8 19.6A2 2 0 1 0 14 16H2"/><path d="M17.5 8a2.5 2.5 0 1 1 2 4H2"/><path d="M9.8 4.4A2 2 0 1 1 11 8H2"/>',
  camera: '<path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z"/><circle cx="12" cy="13" r="3"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/>',
  moon: '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
  sunset: '<path d="M12 10V2"/><path d="m4.93 10.93 1.41 1.41"/><path d="M2 18h2"/><path d="M20 18h2"/><path d="m19.07 10.93-1.41 1.41"/><path d="M22 22H2"/><path d="m16 6-4 4-4-4"/>',
  heart: '<path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.29 1.51 4.04 3 5.5l7 7Z"/>',
  film: '<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M7 3v18"/><path d="M3 7.5h4"/><path d="M3 12h18"/><path d="M3 16.5h4"/><path d="M17 3v18"/><path d="M17 7.5h4"/><path d="M17 16.5h4"/>',
  minus: '<path d="M5 12h14"/>',
  plus: '<path d="M5 12h14"/><path d="M12 5v14"/>',
  palette: '<circle cx="13.5" cy="6.5" r=".8" fill="currentColor"/><circle cx="17.5" cy="10.5" r=".8" fill="currentColor"/><circle cx="8.5" cy="7.5" r=".8" fill="currentColor"/><circle cx="6.5" cy="12.5" r=".8" fill="currentColor"/><path d="M12 2C6.5 2 2 6.5 2 12s4.5 10 10 10c.9 0 1.6-.7 1.6-1.7 0-.4-.2-.8-.4-1.1-.3-.3-.4-.7-.4-1.1a1.6 1.6 0 0 1 1.6-1.6h2C19.5 16.4 22 14 22 11 22 6 17.5 2 12 2Z"/>',
  droplet: '<path d="M12 22a7 7 0 0 0 7-7c0-2-1-3.9-3-5.5s-3.5-4-4-6.5c-.5 2.5-2 4.9-4 6.5C6 11.1 5 13 5 15a7 7 0 0 0 7 7z"/>',
  party: '<path d="M5.8 11.3 2 22l10.7-3.8"/><path d="M4 3h.01"/><path d="M22 8h.01"/><path d="M15 2h.01"/><path d="M22 20h.01"/><path d="m22 2-2.2.7a2.9 2.9 0 0 0-2 3.1c.1.9-.6 1.6-1.5 1.6h-.3c-.9 0-1.6.6-1.8 1.5L14 10"/><path d="m11 13c1.9 1.9 2.8 4.2 2 5-.8.8-3.1-.1-5-2-1.9-1.9-2.8-4.2-2-5 .8-.8 3.1.1 5 2Z"/>',
  'chevron-left': '<path d="m15 18-6-6 6-6"/>',
  'chevron-right': '<path d="m9 18 6-6-6-6"/>',
  'chevron-up': '<path d="m18 15-6-6-6 6"/>',
  'chevron-down': '<path d="m6 9 6 6 6-6"/>',
  shuffle: '<path d="M2 18h1.4c1.3 0 2.5-.6 3.3-1.7l6.1-8.6c.7-1.1 2-1.7 3.3-1.7H22"/><path d="m18 2 4 4-4 4"/><path d="M2 6h1.9c1.5 0 2.9.9 3.6 2.2"/><path d="M22 18h-5.9c-1.3 0-2.6-.7-3.3-1.8l-.5-.8"/><path d="m18 14 4 4-4 4"/>',
  power: '<path d="M12 2v10"/><path d="M18.4 6.6a9 9 0 1 1-12.77.04"/>',
  'power-off': '<path d="M18.36 6.64A9 9 0 0 1 20.77 15"/><path d="M6.16 6.16a9 9 0 1 0 12.68 12.68"/><path d="M12 2v4"/><path d="m2 2 20 20"/>',
  undo: '<path d="M3 7v6h6"/><path d="M21 17a9 9 0 0 0-9-9 9 9 0 0 0-6 2.3L3 13"/>',
  pause: '<rect x="14" y="4" width="4" height="16" rx="1"/><rect x="6" y="4" width="4" height="16" rx="1"/>',
  square: '<rect width="14" height="14" x="5" y="5" rx="2"/>',
  bell: '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>',
  brush: '<path d="m9.06 11.9 8.07-8.06a2.85 2.85 0 1 1 4.03 4.03l-8.06 8.08"/><path d="M7.07 14.94c-1.66 0-3 1.35-3 3.02 0 1.33-2.5 1.52-2 2.02 1.08 1.1 2.49 2.02 4 2.02 2.2 0 4-1.8 4-4.04a3.01 3.01 0 0 0-3-3.02z"/>',
  sparkles: '<path d="m12 3-1.9 5.8a2 2 0 0 1-1.3 1.3L3 12l5.8 1.9a2 2 0 0 1 1.3 1.3L12 21l1.9-5.8a2 2 0 0 1 1.3-1.3L21 12l-5.8-1.9a2 2 0 0 1-1.3-1.3z"/><path d="M5 3v4"/><path d="M19 17v4"/><path d="M3 5h4"/><path d="M17 19h4"/>',
  fan: '<path d="M10.8 16.4a6 6 0 0 1-8.6-7l5.4 1.4a6 6 0 0 1 7-8.6L13.2 7.6a6 6 0 0 1 8.6 7l-5.4-1.4a6 6 0 0 1-7 8.6Z"/><circle cx="12" cy="12" r="1.2"/>',
  sliders: '<line x1="4" x2="4" y1="21" y2="14"/><line x1="4" x2="4" y1="10" y2="3"/><line x1="12" x2="12" y1="21" y2="12"/><line x1="12" x2="12" y1="8" y2="3"/><line x1="20" x2="20" y1="21" y2="16"/><line x1="20" x2="20" y1="12" y2="3"/><line x1="2" x2="6" y1="14" y2="14"/><line x1="10" x2="14" y1="8" y2="8"/><line x1="18" x2="22" y1="16" y2="16"/>',
  lock: '<rect width="18" height="11" x="3" y="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
  volume: '<polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/>',
  zap: '<path d="M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 .78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z"/>',
  chat: '<path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z"/>',
  send: '<path d="M14.54 21.69a.5.5 0 0 0 .94-.02l6.5-19a.5.5 0 0 0-.64-.64l-19 6.5a.5.5 0 0 0-.02.94l7.93 3.18a2 2 0 0 1 1.11 1.11z"/><path d="m21.85 2.15-10.94 10.94"/>',
  download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" x2="12" y1="15" y2="3"/>',
  trash: '<path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" x2="10" y1="11" y2="17"/><line x1="14" x2="14" y1="11" y2="17"/>',
};

function svgFor(name) {
  const body = Object.hasOwn(ICONS, name) && ICONS[name];
  if (!body) return "";
  return `<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
}

function hydrateIcons(root = document) {
  root.querySelectorAll("[data-icon]").forEach((el) => {
    el.innerHTML = svgFor(el.dataset.icon);
  });
}

const SCENE_ICONS = {
  cool_white: "sun", warm_white: "sun", sunset: "sunset",
  sleep: "moon", romantic: "heart", movie: "film",
};
const AIR_MODE_ICONS = { auto: "fan", sleep: "moon", favorite: "heart", manual: "sliders" };

// --- i18n (English / Farsi) --------------------------------------------------

const I18N = {
  en: {
    routines: "Routines", edit_routine: "Create or edit a routine", routine_name: "Name", daily_at: "Daily time (optional, server time)",
    add_step: "Add step", save: "Save", recent_runs: "Recent runs", air_history: "Air quality over 24 hours", hour: "Hour",
    empty_samples: "No readings yet", no_runs: "No runs yet", run: "Run", edit: "Edit", remove: "Remove", failed: "Failed", pending: "Pending",
    home: "Home", devices: "Devices",
    sub: "LAN home control", live: "live", offline: "offline", loading: "Loading…",
    lights: "Lights", vacuum: "Vacuum", air: "Air", air_purifier: "Air purifier",
    camera: "Camera", khatoon: "Khatoon",
    target: "Target", hint_target: "Tap bulbs to target them. None selected = all.",
    on: "On", off: "Off", scenes: "Scenes", brightness: "Brightness",
    color: "Color", pick: "Pick", white_temp: "White temperature", warm: "Warm", cool: "Cool",
    party_fx: "Party & effects", party: "Party", prev: "Prev", cycle: "Cycle", next: "Next",
    random: "Random", each: "Each", full: "Full", all_off: "All off", undo: "Undo",
    pattern: "Dance pattern",
    rooms: "Rooms", clean_rooms: "Clean selected rooms", pick_rooms: "Select at least one room",
    sweep: "Sweep", mop: "Mop", both: "Both", pause: "Pause", stop: "Stop",
    dock: "Dock", find: "Find", suction: "Suction", water: "Water",
    volume: "Volume", drive: "Manual drive", maintenance: "Maintenance",
    mode: "Mode", fan: "Fan level", fav_speed: "Favorite speed (RPM)", screen: "Screen",
    ionizer: "Ionizer", child_lock: "Child lock", buzzer: "Buzzer",
    capture: "Capture", capture_flash: "Capture + flash", capturing: "capturing…",
    chat_hint: "Give one command — she acts and replies. Each command is independent (no memory of past messages).",
    chat_placeholder: "e.g. Set the lights warm and start the vacuum", send: "Send",
    thinking: "thinking…", model_loading: "(loading the model — the first reply is slow)",
    captured: "Captured", done: "Done", hold_stop: "Hold to stop",
    download: "Download", delete: "Delete", deleted: "Deleted", hold_delete: "Hold to delete",
    delete_pass_prompt: "Delete password:",
    undone: "Undone", undo_q: "Undo latest change", off_done: "All lights off",
    m_battery: "Battery", m_status: "Status", m_suction: "Suction", m_area: "Area",
    m_fault: "Fault", m_power: "Power", m_pm25: "PM2.5", m_temp: "Temp",
    m_humidity: "Humidity", m_mode: "Mode", m_fan: "Fan", m_filter: "Filter",
    m_main_brush: "Main brush", m_side_brush: "Side brush", m_hypa: "HEPA",
    m_mop_cloth: "Mop", m_hours: "h used",
    unavailable: "Unavailable", moments_one: "moment", moments_many: "moments",
    party_label: "party mode", controls_failed: "Could not load controls: ",
    panel_alert: "Panel alert", panel_alert_hint: "Pop a message up on the wall touch panel (Latin text only).",
    panel_alert_placeholder: "e.g. Dinner is ready!", panel_send_notice: "Notice",
    panel_send_alert: "Alert", panel_alert_sent: "Sent to the panel",
  },
  fa: {
    routines: "روال‌ها", edit_routine: "ساخت یا ویرایش روال", routine_name: "نام", daily_at: "زمان روزانه (اختیاری، ساعت سرور)",
    add_step: "افزودن گام", save: "ذخیره", recent_runs: "اجراهای اخیر", air_history: "کیفیت هوا در ۲۴ ساعت", hour: "ساعت",
    empty_samples: "هنوز داده‌ای نیست", no_runs: "هنوز اجرایی نیست", run: "اجرا", edit: "ویرایش", remove: "حذف", failed: "ناموفق", pending: "در انتظار",
    home: "خانه", devices: "دستگاه‌ها",
    sub: "کنترل خانه در شبکه محلی", live: "زنده", offline: "آفلاین", loading: "در حال بارگذاری…",
    lights: "چراغ‌ها", vacuum: "جاروبرقی", air: "هوا", air_purifier: "تصفیه هوا",
    camera: "دوربین", khatoon: "خاتون",
    target: "انتخاب لامپ", hint_target: "روی لامپ‌ها بزنید تا انتخاب شوند. هیچ‌کدام = همه.",
    on: "روشن", off: "خاموش", scenes: "حالت‌ها", brightness: "روشنایی",
    color: "رنگ", pick: "انتخاب", white_temp: "دمای سفید", warm: "گرم", cool: "سرد",
    party_fx: "پارتی و افکت‌ها", party: "پارتی", prev: "قبلی", cycle: "چرخه", next: "بعدی",
    random: "تصادفی", each: "هرکدام", full: "کامل", all_off: "همه خاموش", undo: "واگرد",
    pattern: "الگوی رقص نور",
    rooms: "اتاق‌ها", clean_rooms: "نظافت اتاق‌های انتخابی", pick_rooms: "حداقل یک اتاق انتخاب کنید",
    sweep: "جارو", mop: "تی", both: "هردو", pause: "مکث", stop: "توقف",
    dock: "بازگشت", find: "پیداکن", suction: "مکش", water: "آب",
    volume: "صدا", drive: "هدایت دستی", maintenance: "قطعات مصرفی",
    mode: "حالت", fan: "سرعت فن", fav_speed: "سرعت دلخواه (RPM)", screen: "صفحه",
    ionizer: "یونیزر", child_lock: "قفل کودک", buzzer: "بوق",
    capture: "عکس", capture_flash: "عکس با فلاش", capturing: "در حال گرفتن…",
    chat_hint: "یک دستور بدهید — عمل می‌کند و پاسخ می‌دهد. هر دستور مستقل است (حافظه‌ای از پیام‌های قبلی ندارد).",
    chat_placeholder: "مثلاً: چراغ‌ها را گرم کن و جارو را روشن کن", send: "ارسال",
    thinking: "در حال فکر…", model_loading: "(در حال بارگذاری مدل — پاسخ اول کند است)",
    captured: "ثبت شد", done: "انجام شد", hold_stop: "برای توقف نگه دارید",
    download: "دانلود", delete: "حذف", deleted: "حذف شد", hold_delete: "برای حذف نگه دارید",
    delete_pass_prompt: "رمز حذف:",
    undone: "واگرد شد", undo_q: "واگرد آخرین تغییر", off_done: "همه چراغ‌ها خاموش شد",
    m_battery: "باتری", m_status: "وضعیت", m_suction: "مکش", m_area: "مساحت",
    m_fault: "خطا", m_power: "برق", m_pm25: "PM2.5", m_temp: "دما",
    m_humidity: "رطوبت", m_mode: "حالت", m_fan: "فن", m_filter: "فیلتر",
    m_main_brush: "برس اصلی", m_side_brush: "برس کناری", m_hypa: "فیلتر HEPA",
    m_mop_cloth: "دستمال تی", m_hours: "ساعت کارکرد",
    unavailable: "در دسترس نیست", moments_one: "عکس", moments_many: "عکس",
    party_label: "حالت پارتی", controls_failed: "بارگذاری کنترل‌ها ناموفق بود: ",
    panel_alert: "اعلان پنل", panel_alert_hint: "پیامی روی پنل لمسی دیواری نمایش دهید (فقط متن لاتین).",
    panel_alert_placeholder: "مثلاً: !Dinner is ready", panel_send_notice: "اطلاع",
    panel_send_alert: "هشدار", panel_alert_sent: "به پنل ارسال شد",
  },
};

// Device-reported names (modes, levels, patterns, statuses) → Farsi.
const FA_NAMES = {
  "cool white": "سفید سرد", "warm white": "سفید گرم", "sunset": "غروب",
  "sleep": "خواب", "romantic": "رمانتیک", "movie": "فیلم",
  "auto": "خودکار", "favorite": "دلخواه", "manual": "دستی",
  "low": "کم", "medium": "متوسط", "mid": "متوسط", "high": "زیاد",
  "silent": "بی‌صدا", "basic": "معمولی", "strong": "قوی", "full speed": "حداکثر",
  "off": "خاموش", "bright": "روشن", "brightest": "خیلی روشن",
  "snake wave": "موج مار", "pulse": "تپش", "row sweep": "جاروی ردیفی",
  "waterfall": "آبشار", "cross": "ضربدر", "random": "تصادفی",
  "charging": "در حال شارژ", "charging complete": "شارژ کامل", "idle": "بیکار",
  "sweeping": "در حال جارو", "mopping": "در حال تی", "paused": "مکث",
  "go charging": "در راه شارژ", "sweeping and mopping": "جارو و تی",
  "building map": "ساخت نقشه", "upgrading": "به‌روزرسانی",
};

let lang = localStorage.getItem("coukab-lang") || "en";
const t = (key) => (I18N[lang] && I18N[lang][key]) || I18N.en[key] || key;
const trName = (name) => {
  if (name == null) return name;
  const key = String(name).toLowerCase();
  if (lang === "fa" && Object.hasOwn(FA_NAMES, key)) return FA_NAMES[key];
  return name;
};

function applyLang() {
  document.documentElement.lang = lang === "fa" ? "fa" : "en";
  document.documentElement.dir = lang === "fa" ? "rtl" : "ltr";
  document.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
  document.querySelectorAll("[data-i18n-ph]").forEach((el) => { el.placeholder = t(el.dataset.i18nPh); });
  $("#langBtn").textContent = lang === "fa" ? "EN" : "FA";
  if (caps) {
    buildDynamic();
    if (lastStatus) renderAll(lastStatus);
  }
  if (typeof dashboardUI !== "undefined") dashboardUI.applyLanguage();
}

// --- networking --------------------------------------------------------------

async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const retriable = method === "POST" && body?.action !== "remote" &&
    /^\/api\/(lights\/(control|action|library\/(save|recall))|purifier\/action|vacuum\/action|camera\/(capture|delete)|routines\/run)$/.test(path);
  if (retriable) {
    opts.headers["Idempotency-Key"] = Array.from(crypto.getRandomValues(new Uint8Array(16)),
      byte => byte.toString(16).padStart(2, "0")).join("");
  }
  const timeout = path.includes("/chat") ? 180000 :
    path.includes("/camera/capture") ? 100000 : method === "POST" ? 65000 : 16000;
  const deadline = new AbortController();
  const timer = setTimeout(() => deadline.abort(), timeout);
  try {
    const send = () => fetch(path, { ...opts, signal: deadline.signal });
    let res;
    try {
      res = await send();
    } catch (error) {
      if (!retriable || deadline.signal.aborted) throw error;
      res = await send();
    }
    if (!res.ok && !res.headers.get("Content-Type")?.includes("application/json")) {
      throw new Error(`Request failed (${res.status})`);
    }
    const data = await res.json();
    if (!res.ok || data.ok === false) throw new Error(data.error || `Request failed (${res.status})`);
    if (res.status === 202 && data.accepted) return waitForJob(data.job_id);
    return data;
  } catch (err) {
    if (deadline.signal.aborted) throw new Error("Request timed out.");
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

async function waitForJob(id) {
  if (!/^[a-f0-9]{32}$/.test(id)) throw new Error("Invalid action receipt.");
  const until = Date.now() + 90000;
  while (Date.now() < until) {
    await new Promise(resolve => setTimeout(resolve, 1000));
    const { job } = await api("GET", "/api/actions/" + id);
    if (job.status === "done") return job.result || { ok: true };
    if (job.status === "failed" || job.status === "cancelled") {
      throw new Error(job.error || "Action failed.");
    }
  }
  throw new Error(`Action ${id} was accepted. Completion is still unconfirmed.`);
}

let toastTimer = null;
const toastActions = [];

function showToastAction(restoreFocus = false) {
  const box = $("#toastAction");
  const notice = toastActions[0];
  box.hidden = !notice;
  box.className = notice ? `toast show ${notice.kind}` : "toast";
  box.replaceChildren();
  if (!notice) {
    if (restoreFocus && document.contains(noticeOrigin) && noticeOrigin.getClientRects().length) noticeOrigin.focus();
    return;
  }
  const finish = (run) => {
    const focused = box.contains(document.activeElement);
    toastActions.shift();
    showToastAction(focused);
    if (run) notice.actionFn();
  };
  const undo = element("button", "toast-action", notice.actionLabel);
  undo.onclick = () => finish(true);
  const dismiss = element("button", "toast-dismiss", t("dismiss_notice"));
  dismiss.onclick = () => finish(false);
  const description = element("span", "", notice.msg);
  description.setAttribute("role", "status");
  box.append(description, undo, dismiss);
  if (toastActions.length > 1) box.append(element("span", "toast-count", `+${toastActions.length - 1}`));
  if (restoreFocus) undo.focus();
}

let noticeOrigin = null;
function toast(msg, kind = "", actionLabel = null, actionFn = null, origin = document.activeElement) {
  if (actionLabel && actionFn) {
    if (!toastActions.length) noticeOrigin = origin;
    toastActions.push({ msg, kind, actionLabel, actionFn });
    if (toastActions.length === 1) showToastAction();
    else {
      const box = $("#toastAction");
      const count = box.querySelector(".toast-count") || box.appendChild(element("span", "toast-count"));
      count.textContent = `+${toastActions.length - 1}`;
    }
    return;
  }
  // Status feedback has its own live region and never replaces an Undo prompt.
  const box = $("#toastStatus");
  if (box.textContent !== msg || !box.classList.contains("show")) box.textContent = msg;
  box.className = `toast show ${kind}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { box.className = "toast"; }, 2600);
}

// Fire an action with pending state on the button, haptic tick, error flash
// on the owning card, and a quick refresh (when SSE isn't already pushing).
async function act(method, path, body, opts = {}) {
  const btn = opts.btn || null;
  if (navigator.vibrate) navigator.vibrate(10);
  if (btn) btn.classList.add("pending");
  try {
    const res = await api(method, path, body);
    if (opts.okMsg) toast(opts.okMsg, "good", opts.actionLabel, opts.actionFn, btn);
    if (!sseConnected) setTimeout(pollStatus, 250);
    return res;
  } catch (e) {
    toast(e.message, "bad");
    const card = btn && btn.closest(".card");
    if (card) {
      card.classList.remove("err-flash");
      void card.offsetWidth;
      card.classList.add("err-flash");
    }
    return null;
  } finally {
    if (btn) btn.classList.remove("pending");
  }
}

function targets() {
  return selectedBulbs.size ? [...selectedBulbs] : "all";
}

// --- color helpers -----------------------------------------------------------

function rgbIntToHex(n) {
  n = parseInt(n, 10) & 0xffffff;
  return "#" + n.toString(16).padStart(6, "0");
}

function lerp(a, b, t2) { return Math.round(a + (b - a) * t2); }

function ctToHex(k) {
  const x = Math.max(0, Math.min(1, (k - 1700) / (6500 - 1700)));
  const r = lerp(255, 207, x), g = lerp(159, 227, x), b = lerp(69, 255, x);
  return `#${[r, g, b].map((v) => v.toString(16).padStart(2, "0")).join("")}`;
}

function bulbColor(b) {
  if (b.current_power !== "on") return "#2a3350";
  if (b.current_color_mode === "2" && b.current_color_temp) return ctToHex(+b.current_color_temp);
  if (b.current_rgb) return rgbIntToHex(b.current_rgb);
  return "#cccccc";
}

// --- dynamic UI build --------------------------------------------------------

function buildScenes() {
  const row = $("#sceneBtns");
  row.innerHTML = "";
  (caps.scene_modes || []).forEach((m) => {
    const btn = document.createElement("button");
    btn.className = "pill";
    btn.innerHTML = svgFor(SCENE_ICONS[m.key] || "sun");
    btn.append(element("span", "", trName(m.name)));
    btn.dataset.light = "mode";
    btn.dataset.mode = m.key;
    row.appendChild(btn);
  });
}

function buildColorChips() {
  const wrap = $("#colorChips");
  wrap.innerHTML = "";
  const named = {
    red: "#ff0000", orange: "#ffa500", yellow: "#ffff00", green: "#00ff00",
    cyan: "#00ffff", blue: "#0000ff", purple: "#8000ff",
  };
  (caps.color_cycle || []).forEach((name) => {
    const chip = document.createElement("button");
    chip.className = "chip";
    chip.style.background = named[name] || name;
    chip.setAttribute("aria-label", name);
    chip.onclick = (e) => act("POST", "/api/lights/control", { targets: targets(), color: name }, { btn: e.currentTarget });
    wrap.appendChild(chip);
  });
}

function buildBulbGrid() {
  const grid = $("#bulbGrid");
  grid.innerHTML = "";
  if (!caps.bulbs.length) {
    grid.append(element("p", "hint", t("no_bulbs")));
    return;
  }
  caps.bulbs.forEach((name) => {
    const cell = document.createElement("button");
    cell.className = "bulb off";
    cell.dataset.name = name;
    cell.append(element("span", "dot"), element("span", "name", name), element("span", "meta", "—"));
    cell.setAttribute("aria-pressed", String(selectedBulbs.has(name)));
    cell.classList.toggle("selected", selectedBulbs.has(name));
    cell.onclick = () => {
      if (selectedBulbs.has(name)) selectedBulbs.delete(name);
      else selectedBulbs.add(name);
      cell.classList.toggle("selected", selectedBulbs.has(name));
      cell.setAttribute("aria-pressed", String(selectedBulbs.has(name)));
    };
    grid.appendChild(cell);
  });
}

function buildSelect(el, options) {
  el.innerHTML = "";
  options.forEach((o) => {
    const opt = document.createElement("option");
    opt.value = o.value;
    opt.textContent = trName(o.name);
    opt.dataset.name = o.name;
    el.appendChild(opt);
  });
}

function buildToggleButtons(containerSel, options, dataKey, iconFn) {
  const row = $(containerSel);
  row.innerHTML = "";
  options.forEach((o) => {
    const btn = document.createElement("button");
    btn.className = "pill";
    const ic = iconFn ? iconFn(o) : "";
    btn.innerHTML = ic ? svgFor(ic) : "";
    btn.append(element("span", "", trName(o.name)));
    btn.dataset[dataKey] = o.value;
    btn.dataset.name = o.name;
    row.appendChild(btn);
  });
}

function buildPatternRow() {
  const row = $("#patternRow");
  row.innerHTML = "";
  (caps.dance_patterns || []).forEach((name, i) => {
    const btn = document.createElement("button");
    btn.className = "pill";
    btn.append(element("span", "", trName(name)));
    btn.dataset.pattern = i;
    btn.dataset.name = name;
    row.appendChild(btn);
  });
}

function buildRoomChips(rooms) {
  const wrap = $("#roomsWrap");
  const row = $("#roomChips");
  row.innerHTML = "";
  if (!rooms || !rooms.length) { wrap.classList.add("hidden"); return; }
  rooms.forEach((r) => {
    const btn = document.createElement("button");
    btn.className = "pill toggle";
    btn.append(element("span", "", r.name));
    btn.dataset.roomId = r.id;
    btn.onclick = () => {
      if (selectedRooms.has(r.id)) selectedRooms.delete(r.id);
      else selectedRooms.add(r.id);
      btn.classList.toggle("on", selectedRooms.has(r.id));
    };
    row.appendChild(btn);
  });
  wrap.classList.remove("hidden");
}

function buildDynamic() {
  buildScenes();
  buildColorChips();
  buildSelect($("#vacSuction"), caps.suction_levels || []);
  buildSelect($("#vacWater"), caps.water_levels || []);
  buildSelect($("#airScreen"), caps.screen_brightness || []);
  buildToggleButtons("#airModes", caps.purifier_modes || [], "airMode",
    (o) => AIR_MODE_ICONS[o.name.toLowerCase()] || "fan");
  buildToggleButtons("#airFan", caps.fan_levels || [], "airFan", () => "fan");
  buildPatternRow();
  hydrateIcons();
}

// --- status rendering --------------------------------------------------------

function setStat(id, line, off) {
  const card = $(id);
  const el = card.querySelector(".stat-line");
  if (el.textContent !== line && el.textContent !== "—") {
    el.classList.remove("bump");
    void el.offsetWidth;
    el.classList.add("bump");
  }
  el.textContent = line;
  card.classList.toggle("off", !!off);
}

function element(tag, cls, text) {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  if (text != null) el.textContent = text;
  return el;
}

function metric(k, v) {
  const row = element("div", "metric");
  row.append(element("span", "k", k), element("span", "v", v));
  return row;
}

function renderLights(l) {
  const badge = $("#partyBadge");
  const state = l.state || {};
  const party = !!state.party_running;
  badge.classList.toggle("hidden", !party);
  if (party) $("#partyBadgeText").textContent = trName(state.party_pattern);

  // Dance pattern picker — only meaningful while the party runs.
  $("#patternWrap").classList.toggle("hidden", !party);
  if (party) {
    document.querySelectorAll("#patternRow .pill").forEach((b) => {
      b.classList.toggle("active", b.dataset.name === state.party_pattern);
    });
  }

  // Undo depth badge.
  const depth = state.undo_depth ?? 0;
  const undoCount = $("#undoCount");
  undoCount.textContent = depth;
  undoCount.classList.toggle("hidden", depth === 0);
  $("#undoBtn").disabled = depth === 0;

  if (!l.available) { setStat("#statLights", t("offline"), true); return; }
  const bulbs = l.bulbs || [];
  const on = bulbs.filter((b) => b.current_power === "on").length;
  setStat("#statLights", party ? t("party_label") : `${on} / ${bulbs.length}`, on === 0 && !party);

  const byName = {};
  bulbs.forEach((b) => { byName[b.name] = b; });
  document.querySelectorAll(".bulb").forEach((cell) => {
    const b = byName[cell.dataset.name];
    const dot = cell.querySelector(".dot");
    const meta = cell.querySelector(".meta");
    if (!b || !b.ok) { cell.classList.add("off"); meta.textContent = "—"; dot.style.background = "#2a3350"; return; }
    const isOn = b.current_power === "on";
    cell.classList.toggle("off", !isOn);
    dot.style.background = bulbColor(b);
    dot.style.color = bulbColor(b);
    meta.textContent = isOn ? `${b.current_brightness || "?"}%` : t("off");
  });
}

function renderVacuum(v) {
  const out = $("#vacuumReadout");
  if (!v.available) {
    out.className = "readout unavailable";
    out.textContent = v.error ? `${t("unavailable")} — ${v.error}` : t("unavailable");
    setStat("#statVacuum", t("offline"), true);
    return;
  }
  out.className = "readout";
  const parts = [];
  if (v.battery != null) parts.push(metric(t("m_battery"), `${v.battery}%`));
  if (v.status != null) parts.push(metric(t("m_status"), trName(v.status)));
  if (v.suction_level != null) parts.push(metric(t("m_suction"), trName(v.suction_level)));
  if (v.cleaning_area != null) parts.push(metric(t("m_area"), `${v.cleaning_area} m²`));
  if (Number(v.fault) !== 0 && v.fault) parts.push(metric(t("m_fault"), typeof dashboardUI === "undefined" ? v.fault : dashboardUI.fault(v.fault)));
  out.replaceChildren(...parts);
  setStat("#statVacuum", `${trName(v.status) || "—"} · ${v.battery ?? "?"}%`);
  syncSelectByName($("#vacSuction"), v.suction_level);
  syncSelectByName($("#vacWater"), v.water_state);
  if (v.volume != null && document.activeElement !== $("#vacVolume")) $("#vacVolume").value = v.volume;
}

function renderAir(a) {
  const out = $("#airReadout");
  if (!a.available) {
    out.className = "readout unavailable";
    out.textContent = a.error ? `${t("unavailable")} — ${a.error}` : t("unavailable");
    setStat("#statAir", t("offline"), true);
    return;
  }
  out.className = "readout";
  const parts = [];
  if (a.power != null) parts.push(metric(t("m_power"), a.power === "ON" ? t("on") : t("off")));
  if (a.pm25 != null) parts.push(metric(t("m_pm25"), a.pm25));
  if (a.temperature != null) parts.push(metric(t("m_temp"), `${a.temperature}°`));
  if (a.humidity != null) parts.push(metric(t("m_humidity"), `${a.humidity}%`));
  if (a.mode != null) parts.push(metric(t("m_mode"), trName(a.mode)));
  if (a.fan_level != null) parts.push(metric(t("m_fan"), trName(a.fan_level)));
  if (a.filter_left_days != null) parts.push(metric(t("m_filter"), `${a.filter_left_days}d`));
  out.replaceChildren(...parts);
  const isOn = a.power === "ON";
  const reading = a.pm25 == null ? "" : ` · ${a.pm25.toLocaleString(lang)} PM2.5`;
  setStat("#statAir", t(isOn ? "on" : "off") + reading, !isOn);

  markActive("#airModes", a.mode);
  markActive("#airFan", a.fan_level);
  syncSelectByName($("#airScreen"), a.screen_brightness);
  setToggle('[data-air-toggle="anion"]', a.anion === "ON");
  setToggle('[data-air-toggle="child_lock"]', a.child_lock === "ON");
  setToggle('[data-air-toggle="buzzer"]', a.buzzer === "ON");
  if (a.favorite_speed != null && document.activeElement !== $("#airFavSpeed")) {
    $("#airFavSpeed").value = a.favorite_speed;
  }
}

// One image tile: clickable full-view + a download link and hold-to-delete.
function isMomentPath(src) {
  return typeof src === "string" && /^\/moments\/[^/\\?#%]+\.(?:jpe?g|png)$/i.test(src)
    && new URL(src, location.href).pathname.startsWith("/moments/");
}

function momentTile(src, { large = false } = {}) {
  const fig = document.createElement("figure");
  fig.className = "gallery-item" + (large ? " large" : "");

  const link = document.createElement("a");
  if (!isMomentPath(src)) return fig;
  link.href = src;
  link.target = "_blank";
  link.rel = "noopener";
  const img = document.createElement("img");
  img.src = src + "?thumb=1";
  img.alt = "moment";
  img.loading = "lazy";
  img.decoding = "async";
  link.append(img);

  const actions = document.createElement("div");
  actions.className = "img-actions";

  const dl = document.createElement("a");
  dl.className = "img-btn";
  dl.href = src + "?download=1";
  dl.setAttribute("download", "");
  dl.title = t("download");
  dl.setAttribute("aria-label", t("download"));
  dl.innerHTML = svgFor("download");
  dl.addEventListener("click", (e) => e.stopPropagation());

  const del = document.createElement("button");
  del.className = "img-btn del hold-btn";
  del.title = t("hold_delete");
  del.setAttribute("aria-label", t("delete"));
  del.innerHTML = svgFor("trash");
  wireHoldButton(del, 600, () => deleteMoment(src), "hold_delete");

  actions.append(dl, del);
  fig.append(link, actions);
  return fig;
}

function renderMoments(m) {
  const word = m.count === 1 ? t("moments_one") : t("moments_many");
  if (typeof dashboardUI === "undefined") setStat("#statCamera", `${m.count} ${word}`);

  const latest = $("#momentLatest");
  if (m.latest) {
    if (latest.dataset.src !== m.latest) {
      latest.dataset.src = m.latest;
      latest.innerHTML = "";
      latest.appendChild(momentTile(m.latest, { large: true }));
    }
  } else {
    latest.dataset.src = "";
    latest.innerHTML = "";
  }

  const gallery = $("#momentGallery");
  const recent = (m.recent || []).slice(0, 8);
  const key = recent.join("|");
  if (gallery.dataset.key !== key) {
    gallery.dataset.key = key;
    gallery.innerHTML = "";
    recent.forEach((src) => gallery.appendChild(momentTile(src)));
  }
}

async function sendPanelAlert(level) {
  const input = $("#alertText");
  const text = input.value.trim();
  if (!text) { input.focus(); return; }
  try {
    await api("POST", "/api/panel/alert", { text, level });
    input.value = "";
    toast(t("panel_alert_sent"), "good");
  } catch (e) {
    toast(e.message, "bad");
  }
}

let deletePassword = null; // remembered for the session once accepted

async function deleteMoment(src) {
  const body = { image: src };
  if (caps.delete_protected) {
    if (deletePassword == null) {
      const entered = prompt(t("delete_pass_prompt"));
      if (entered == null || entered === "") return; // cancelled
      deletePassword = entered;
    }
    body.password = deletePassword;
  }
  try {
    const res = await api("POST", "/api/camera/delete", body);
    toast(t("deleted"), "good");
    // Force a rebuild from the fresh summary the server returned.
    $("#momentLatest").dataset.src = "";
    $("#momentGallery").dataset.key = "";
    renderMoments(res);
  } catch (e) {
    deletePassword = null; // wrong password (or other failure): ask again next time
    toast(e.message, "bad");
  }
}

function syncSelectByName(sel, name) {
  if (name == null || document.activeElement === sel) return;
  for (const opt of sel.options) {
    if ((opt.dataset.name || opt.textContent).toLowerCase() === String(name).toLowerCase()) {
      sel.value = opt.value;
      return;
    }
  }
}

function markActive(containerSel, name) {
  document.querySelectorAll(`${containerSel} .pill`).forEach((b) => {
    b.classList.toggle("active", name != null && (b.dataset.name || "").toLowerCase() === String(name).toLowerCase());
  });
}

function setToggle(sel, on) {
  const btn = document.querySelector(sel);
  if (btn) btn.classList.toggle("on", on);
}

function renderAll(s) {
  if (lastStatus !== s) statusReceivedAt = Date.now();
  lastStatus = s;
  renderLights(s.lights || {});
  renderVacuum(s.vacuum || {});
  renderAir(s.purifier || {});
  renderMoments(s.moments || { count: 0, recent: [] });
  if (typeof dashboardUI !== "undefined") dashboardUI.render(s);
}

function setLive(ok) {
  $("#liveDot").className = `live-dot ${ok ? "ok" : "err"}`;
  $("#liveText").textContent = ok ? t("live") : t("offline");
}

async function pollStatus() {
  if (polling) return;
  polling = true;
  const revision = statusRevision;
  try {
    const s = await api("GET", "/api/status");
    if (revision !== statusRevision) return;
    driveEpoch = s.drive_epoch;
    renderAll(s);
    setLive(true);
  } catch (e) {
    if (revision === statusRevision) setLive(false);
  } finally {
    polling = false;
  }
}

// --- live updates (SSE with polling fallback) --------------------------------

function connectEvents() {
  if (eventSource) eventSource.close();
  if (typeof EventSource !== "function") {
    sseConnected = false;
    return;
  }
  eventSource = new EventSource("/api/events");
  eventSource.addEventListener("status", (e) => {
    sseConnected = true;
    setLive(true);
    try {
      const snapshot = JSON.parse(e.data);
      statusRevision++;
      driveEpoch = snapshot.drive_epoch;
      renderAll(snapshot);
    } catch (_) { /* ignore bad frame */ }
  });
  eventSource.onopen = () => { sseConnected = true; setLive(true); };
  eventSource.onerror = () => {
    sseConnected = false;
    setLive(false);
    eventSource.close();
    eventSource = null;
    setTimeout(() => { if (!document.hidden) connectEvents(); }, SSE_RETRY_MS);
  };
}

// --- event wiring ------------------------------------------------------------

// Keep one light write in flight. After a drag, send the latest absolute value.
function brightnessSender() {
  let pending = null;
  let running = false;
  return async (value) => {
    pending = { targets: targets(), brightness: value };
    if (running) return;
    running = true;
    try {
      while (pending) {
        const body = pending;
        pending = null;
        await act("POST", "/api/lights/control", body);
        await new Promise(resolve => setTimeout(resolve, 350));
      }
    } finally {
      running = false;
    }
  };
}

function wireEvents() {
  // Lights actions (delegated)
  document.addEventListener("click", (e) => {
    const p = e.target.closest("[data-pattern]");
    if (p) {
      act("POST", "/api/lights/action", { action: "party_pattern", value: +p.dataset.pattern }, { btn: p });
      return;
    }
    const tEl = e.target.closest("[data-light]");
    if (!tEl) return;
    const action = tEl.dataset.light;
    const body = { action };
    if (action === "mode") {
      body.mode = tEl.dataset.mode;
      const scene = caps?.scene_modes?.find(mode => mode.key === body.mode);
      if (selectedBulbs.size && scene) {
        const selection = { targets: targets(), power: true };
        if (scene.brightness != null) selection.brightness = scene.brightness;
        if (scene.color != null) selection.color = scene.color;
        act("POST", "/api/lights/control", selection, { btn: tEl });
        return;
      }
    }
    const opts = { btn: tEl };
    if (action === "all_off") {
      opts.okMsg = t("off_done");
      opts.actionLabel = t("undo_q");
      opts.actionFn = () => act("POST", "/api/lights/action", { action: "undo" });
    }
    act("POST", "/api/lights/action", body, opts);
  });

  $("#targetsOn").addEventListener("click", (e) =>
    act("POST", "/api/lights/control", { targets: targets(), power: true }, { btn: e.currentTarget }));
  $("#targetsOff").addEventListener("click", (e) =>
    act("POST", "/api/lights/control", { targets: targets(), power: false }, { btn: e.currentTarget }));

  // Brightness: live preview while dragging (throttled), final value on release.
  const sendBrightness = brightnessSender();
  for (const ev of ["input", "change"]) {
    $("#brightness").addEventListener(ev, e => sendBrightness(+e.target.value));
  }

  $("#colorPicker").addEventListener("change", (e) =>
    act("POST", "/api/lights/control", { targets: targets(), color: e.target.value }));
  $("#colorTemp").addEventListener("change", (e) =>
    act("POST", "/api/lights/control", { targets: targets(), color: String(e.target.value) }));

  // Vacuum: simple actions (stop has its own hold-to-confirm below)
  document.addEventListener("click", (e) => {
    const v = e.target.closest("[data-vac]");
    if (v) act("POST", "/api/vacuum/action", { action: v.dataset.vac }, { btn: v });
  });
  $("#vacSuction").addEventListener("change", (e) =>
    act("POST", "/api/vacuum/action", { action: "suction", value: +e.target.value }));
  $("#vacWater").addEventListener("change", (e) =>
    act("POST", "/api/vacuum/action", { action: "water", value: +e.target.value }));
  $("#vacVolume").addEventListener("change", (e) =>
    act("POST", "/api/vacuum/action", { action: "volume", value: +e.target.value }));

  // Vacuum stop: hold 600 ms to confirm (a fat-finger tap shows a hint instead).
  wireHoldButton($("#vacStopBtn"), 600, () =>
    act("POST", "/api/vacuum/action", { action: "stop" }, { btn: $("#vacStopBtn") }));

  // Rooms
  $("#cleanRoomsBtn").addEventListener("click", (e) => {
    if (!selectedRooms.size) { toast(t("pick_rooms"), "bad"); return; }
    act("POST", "/api/vacuum/action",
      { action: "room_sweep", value: [...selectedRooms].join(",") },
      { btn: e.currentTarget, okMsg: t("done") });
  });

  // D-pad: press-and-hold drives continuously; release sends stop.
  document.querySelectorAll("[data-vac-remote]").forEach((btn) => wireDpadHold(btn));
  $("#dpadStop").addEventListener("click", (e) =>
    act("POST", "/api/vacuum/action", { action: "remote", value: 5 }, { btn: e.currentTarget }));

  // Maintenance: fetch consumables on first open.
  $("#maintDetails").addEventListener("toggle", function onToggle() {
    if (!this.open || this.dataset.loaded) return;
    this.dataset.loaded = "1";
    loadConsumables();
  });

  // Air purifier
  document.addEventListener("click", (e) => {
    const p = e.target.closest("[data-air-power]");
    if (p) act("POST", "/api/purifier/action", { action: "power", value: p.dataset.airPower === "on" }, { btn: p });
    const m = e.target.closest("[data-air-mode]");
    if (m) act("POST", "/api/purifier/action", { action: "mode", value: +m.dataset.airMode }, { btn: m });
    const f = e.target.closest("[data-air-fan]");
    if (f) act("POST", "/api/purifier/action", { action: "fan", value: +f.dataset.airFan }, { btn: f });
    const tg = e.target.closest("[data-air-toggle]");
    if (tg) {
      const next = !tg.classList.contains("on");
      tg.classList.toggle("on", next); // optimistic; the next push corrects it on failure
      act("POST", "/api/purifier/action", { action: tg.dataset.airToggle, value: next }, { btn: tg });
    }
  });
  $("#airScreen").addEventListener("change", (e) =>
    act("POST", "/api/purifier/action", { action: "screen", value: +e.target.value }));
  $("#airFavSpeed").addEventListener("change", (e) =>
    act("POST", "/api/purifier/action", { action: "favorite_speed", value: +e.target.value }));

  // Camera
  $("#captureBtn").addEventListener("click", () => capture(false));
  $("#captureFlashBtn").addEventListener("click", () => capture(true));

  // Panel alert (popup on the wall touch controller)
  $("#alertForm").addEventListener("submit", (e) => {
    e.preventDefault();
    sendPanelAlert("info");
  });
  $("#alertUrgent").addEventListener("click", () => sendPanelAlert("alert"));

  // Refresh
  $("#refreshBtn").addEventListener("click", (e) => {
    const btn = e.currentTarget;
    btn.classList.remove("spin");
    void btn.offsetWidth;
    btn.classList.add("spin");
    pollStatus();
  });

  // Language toggle
  $("#langBtn").addEventListener("click", () => {
    lang = lang === "fa" ? "en" : "fa";
    localStorage.setItem("coukab-lang", lang);
    applyLang();
  });

  // Chat
  $("#chatForm").addEventListener("submit", onChat);

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) return;
    if (!sseConnected) connectEvents();
    pollStatus();
  });
}

// Press-and-hold with progress fill; quick tap shows a hint instead.
function wireHoldButton(btn, ms, fire, hintKey = "hold_stop") {
  let timer = null;
  const cancel = (hint) => {
    if (timer) {
      clearTimeout(timer);
      timer = null;
      if (hint) toast(t(hintKey));
    }
    btn.classList.remove("holding");
    btn.style.removeProperty("--hold-ms");
  };
  btn.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    btn.style.setProperty("--hold-ms", `${ms}ms`);
    btn.classList.add("holding");
    timer = setTimeout(() => { timer = null; btn.classList.remove("holding"); fire(); }, ms);
  });
  btn.addEventListener("pointerup", () => cancel(true));
  btn.addEventListener("pointerleave", () => cancel(false));
  btn.addEventListener("pointercancel", () => cancel(false));
  btn.addEventListener("contextmenu", (e) => e.preventDefault());
}

// Hold a direction to keep driving; release sends remote-stop.
function wireDpadHold(btn) {
  const dir = +btn.dataset.vacRemote;
  let held = false;
  let loopTimer = null;
  let session = "";
  let seq = 0;
  let epoch = null;
  let directionInFlight = false;

  const newSession = () => {
    if (globalThis.crypto && typeof globalThis.crypto.randomUUID === "function") {
      return globalThis.crypto.randomUUID();
    }
    return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  };
  const send = async (value) => {
    const hold = session;
    const sequence = ++seq;
    try {
      const result = await api("POST", "/api/vacuum/action", {
        action: "remote", value, session: hold, epoch, seq: sequence,
      });
      if (session !== hold || sequence !== seq) return;
      if (result.epoch) driveEpoch = result.epoch;
      if (result.ignored && held && value !== 5) {
        held = false;
        clearTimeout(loopTimer);
        btn.classList.remove("driving");
      }
    } catch (err) {
      toast(err.message, "bad");
    }
  };

  const loop = () => {
    if (!held) return;
    // Direction repeats are best-effort and coalesced. STOP is never held
    // behind this flag; it is sent immediately with a higher sequence below.
    if (!directionInFlight) {
      directionInFlight = true;
      send(dir).finally(() => { directionInFlight = false; });
    }
    loopTimer = setTimeout(loop, 400);
  };

  const press = e => {
    e.preventDefault();
    if (held || !driveEpoch) return;
    epoch = driveEpoch;
    if (navigator.vibrate) navigator.vibrate(10);
    session = newSession();
    seq = 0;
    held = true;
    btn.classList.add("driving");
    loop();
  };
  btn.addEventListener("pointerdown", press);
  btn.addEventListener("keydown", e => {
    if (e.key === " " || e.key === "Enter") press(e);
  });
  const release = () => {
    if (!held) return;
    held = false;
    clearTimeout(loopTimer);
    btn.classList.remove("driving");
    send(5); // stop
  };
  window.addEventListener("blur", release);
  btn.addEventListener("blur", release);
  btn.addEventListener("keyup", e => {
    if (e.key === " " || e.key === "Enter") release();
  });
  btn.addEventListener("pointerup", release);
  btn.addEventListener("pointerleave", release);
  btn.addEventListener("pointercancel", release);
  btn.addEventListener("contextmenu", (e) => e.preventDefault());
}

async function loadConsumables() {
  const body = $("#maintBody");
  body.replaceChildren(element("span", "hint", t("loading")));
  try {
    const c = await api("GET", "/api/vacuum/consumables");
    if (c.available === false) throw new Error(c.error || t("unavailable"));
    const rows = [
      ["m_main_brush", c.main_brush_life, c.main_brush_hours],
      ["m_side_brush", c.side_brush_life, c.side_brush_hours],
      ["m_hypa", c.hypa_life, c.hypa_hours],
      ["m_mop_cloth", c.mop_life, c.mop_hours],
      ["m_filter", c.filter_life, c.filter_hours],
    ].filter(([, life]) => life != null);
    const metrics = rows.map(([key, life, hours]) => {
      const cls = life < 10 ? "bad" : life < 30 ? "warn" : "";
      const hoursTxt = hours != null ? ` · ${hours}${t("m_hours")}` : "";
      const row = metric(t(key) + hoursTxt, `${life}%`);
      row.className = `metric maint-metric ${cls}`;
      const bar = element("span", "bar");
      const fill = element("span", `fill ${cls}`);
      fill.style.width = `${Math.max(0, Math.min(100, Number(life) || 0))}%`;
      bar.append(fill);
      row.append(bar);
      return row;
    });
    body.replaceChildren(...(metrics.length ? metrics : [element("span", "hint", t("unavailable"))]));
  } catch (e) {
    body.replaceChildren(element("span", "hint", `${t("unavailable")} — ${e.message}`));
    delete $("#maintDetails").dataset.loaded; // allow retry on next open
  }
}

async function loadRooms() {
  try {
    const r = await api("GET", "/api/vacuum/rooms");
    if (r.available === false) return;
    buildRoomChips(r.rooms || []);
  } catch (_) { /* rooms stay hidden */ }
}

async function capture(flash) {
  const btn = flash ? $("#captureFlashBtn") : $("#captureBtn");
  const html = btn.innerHTML;
  btn.innerHTML = svgFor("refresh") + `<span>${t("capturing")}</span>`;
  btn.querySelector(".ic").style.animation = "spin 1s linear infinite";
  btn.disabled = true;
  try {
    const res = await api("POST", "/api/camera/capture", { flash });
    if (isMomentPath(res.image)) {
      $("#momentLatest").replaceChildren(momentTile(res.image, { large: true }));
      $("#momentLatest").dataset.src = "";  // let renderMoments rebuild with overlay actions
    }
    toast(t("captured"), "good");
    if (!sseConnected) setTimeout(pollStatus, 250);
  } catch (e) {
    toast(e.message, "bad");
  } finally {
    btn.innerHTML = html;
    btn.disabled = false;
  }
}

async function onChat(e) {
  e.preventDefault();
  const input = $("#chatInput");
  const msg = input.value.trim();
  if (!msg) return;
  input.value = "";
  const log = $("#chatLog");
  // Single-shot: each command replaces the previous one (no conversation).
  log.innerHTML = "";
  log.insertAdjacentHTML("beforeend", `<div class="chat-msg user"></div>`);
  log.lastElementChild.textContent = msg;
  log.insertAdjacentHTML("beforeend", `<div class="chat-msg bot pending"></div>`);
  const pending = log.lastElementChild;
  log.scrollTop = log.scrollHeight;

  // Elapsed-time ticker; warn about model load on the first message.
  const firstMessage = chatMessagesSent === 0 && !(caps && caps.chat_ready);
  chatMessagesSent += 1;
  const started = Date.now();
  const tick = () => {
    const secs = Math.floor((Date.now() - started) / 1000);
    let text = t("thinking") + (secs >= 2 ? ` ${secs}s` : "");
    if (firstMessage && secs >= 3) text += `\n${t("model_loading")}`;
    pending.textContent = text;
  };
  tick();
  const ticker = setInterval(tick, 1000);

  try {
    await streamChat(msg, pending, ticker, log);
  } catch (err) {
    clearInterval(ticker);
    pending.classList.remove("pending");
    pending.classList.add("bad");
    pending.textContent = `Error: ${err.message}`;
  }
  log.scrollTop = log.scrollHeight;
}

// Stream the reply as newline-delimited JSON events. Tool activity shows as a
// dim status line; answer tokens stream into the bubble. Falls back to the
// blocking /api/chat endpoint if streaming isn't available.
async function streamChat(msg, pending, ticker, log) {
  if (typeof ReadableStream === "undefined") {
    const data = await api("POST", "/api/chat", { message: msg });
    clearInterval(ticker);
    pending.classList.remove("pending");
    pending.textContent = data.reply || "(no response)";
    return;
  }
  const controller = new AbortController();
  let timer;
  const resetDeadline = () => {
    clearTimeout(timer);
    timer = setTimeout(() => controller.abort(), 180000);
  };
  resetDeadline();
  try {
    const res = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: msg }),
      signal: controller.signal,
    });
    if (!res.ok || !res.body) throw new Error(`Assistant request failed (${res.status})`);
    await readChatStream(res, pending, ticker, log, resetDeadline);
  } finally {
    clearTimeout(timer);
  }
}

async function readChatStream(res, pending, ticker, log, resetDeadline) {
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let bufLine = "";
  let answer = "";
  let status = "";
  let started = false;

  const render = () => {
    // Status (dim) above the streamed answer text.
    pending.innerHTML = "";
    if (status && !answer) {
      const s = document.createElement("div");
      s.className = "chat-status";
      s.textContent = "⚙ " + status;
      pending.appendChild(s);
    }
    if (answer) pending.appendChild(document.createTextNode(answer));
    log.scrollTop = log.scrollHeight;
  };

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    resetDeadline();
    bufLine += decoder.decode(value, { stream: true });
    let nl;
    while ((nl = bufLine.indexOf("\n")) >= 0) {
      const line = bufLine.slice(0, nl).trim();
      bufLine = bufLine.slice(nl + 1);
      if (!line) continue;
      let ev;
      try { ev = JSON.parse(line); } catch (_) { continue; }
      if (!started) { started = true; clearInterval(ticker); pending.classList.remove("pending"); }
      if (ev.type === "status") {
        status = ev.text || "";
      } else if (ev.type === "token") {
        answer += ev.text || "";
      } else if (ev.type === "done") {
        if (ev.text) answer = ev.text;
      } else if (ev.type === "error") {
        pending.classList.add("bad");
        answer = `Error: ${ev.text || "assistant failed"}`;
      }
      render();
    }
  }
  clearInterval(ticker);
  pending.classList.remove("pending");
  if (!answer) pending.textContent = "(no response)";
}

function wireHousehold(actions) {
  const picker = $("#routineAction"), params = $("#routineParams");
  let steps = [];
  const button = (key, click) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "pill";
    b.textContent = t(key);
    b.onclick = click;
    return b;
  };
  const label = step => [t(step.device === "purifier" ? "air_purifier" : step.device),
    t(step.action).replaceAll("_", " "), step.mode ?? step.value].filter(x => x != null).join(" · ");
  const showSteps = () => {
    $("#routineSteps").replaceChildren(...steps.map((step, i) => {
      const li = document.createElement("li");
      li.append(label(step), " ", button("remove", () => { steps.splice(i, 1); showSteps(); }));
      return li;
    }));
  };
  picker.replaceChildren(...actions.map((a, i) => new Option(label(a), String(i))));
  function showParams() {
    params.replaceChildren();
    for (const [key, schema] of Object.entries(actions[picker.value]?.fields || {})) {
      let field;
      if (schema.enum || schema.type === "boolean") {
        field = document.createElement("select");
        for (const value of schema.enum || [true, false]) field.add(new Option(String(value), String(value)));
      } else {
        field = document.createElement("input");
        field.type = schema.type === "integer" ? "number" : "text";
        if (schema.minimum != null) field.min = schema.minimum;
        if (schema.maximum != null) field.max = schema.maximum;
        field.value = schema.minimum ?? "";
      }
      field.dataset.key = key;
      field.setAttribute("aria-label", key);
      params.append(field);
    }
  }
  picker.onchange = showParams;
  showParams();
  $("#routineAdd").onclick = () => {
    const a = actions[picker.value];
    if (!a || steps.length >= 16) return;
    const step = { device: a.device, action: a.action };
    for (const field of params.children) {
      const key = field.dataset.key, type = a.fields[key].type;
      if (!field.value || !field.reportValidity()) return;
      step[key] = type === "integer" ? Number(field.value) : type === "boolean" ? field.value === "true" : field.value;
    }
    steps.push(step);
    showSteps();
  };
  async function refresh() {
    try {
      const { routines, runs } = await api("GET", "/api/routines");
      $("#routineList").replaceChildren(...routines.map(r => {
        const row = document.createElement("div");
        row.className = "btn-row wrap";
        row.append(r.name + (r.schedule ? " · " + r.schedule : ""), button("run", async e => {
          const b = e.currentTarget;
          b.disabled = true;
          try {
            const result = await api("POST", "/api/routines/run", {name: r.name});
            toast(t(result.complete ? "done" : "failed"), result.complete ? "good" : "bad");
            $("#runHistory").open = true;
            await refresh();
          } catch (err) { toast(err.message, "bad"); }
          finally { b.disabled = false; }
        }), button("edit", () => {
          $("#routineEditor").open = true;
          $("#routineName").value = r.name;
          $("#routineTime").value = r.schedule || "";
          steps = r.steps.slice();
          showSteps();
        }), button("delete", async () => {
          if (!confirm(t("delete") + " " + r.name + "?")) return;
          try { await api("POST", "/api/routines/delete", {name: r.name}); await refresh(); }
          catch (err) { toast(err.message, "bad"); }
        }));
        return row;
      }));
      $("#runResults").replaceChildren(...runs.map(r => {
        const entry = document.createElement("p");
        entry.textContent = r.name + " · " + new Date(r.ts * 1000).toLocaleString(lang) +
          " — " + r.outcomes.map(s => label(s) + " (" + t(s.status) + ")").join(", ");
        return entry;
      }));
      if (!runs.length) $("#runResults").textContent = t("no_runs");
    } catch (err) { toast(err.message, "bad"); }
  }
  $("#routineForm").onsubmit = async e => {
    e.preventDefault();
    try {
      await api("POST", "/api/routines/save", {name: $("#routineName").value, steps, schedule: $("#routineTime").value});
      steps = []; showSteps(); $("#routineForm").reset(); showParams(); $("#routineEditor").open = false;
      await refresh();
    } catch (err) { toast(err.message, "bad"); }
  };
  $("#runHistory").ontoggle = () => { if ($("#runHistory").open) refresh(); };
  refresh();
  $("#airHistory").ontoggle = async () => {
    if (!$("#airHistory").open) return;
    try {
      const { hours } = await api("GET", "/api/history");
      $("#airHours").replaceChildren(...hours.map(h => {
        const row = document.createElement("tr");
        const stamp = new Date(h.ts * 1000).toLocaleTimeString(lang, {hour: "2-digit", minute: "2-digit"});
        for (const val of [stamp, h.pm25, h.temperature, h.humidity]) {
          row.insertCell().textContent = typeof val === "number" ? val.toLocaleString(lang, {maximumFractionDigits: 1}) : val ?? "—";
        }
        return row;
      }));
      $("#airHistoryStatus").textContent = hours.length ? "" : t("empty_samples");
    } catch (err) { $("#airHistoryStatus").textContent = err.message; }
  };
}

// --- tab routing for mobile bottom nav ---------------------------------------

function initTabs() {
  let current = "home", homeScroll = 0, homeFocus = null;
  const tabs = document.querySelectorAll(".nav-tab");
  const cards = {
    home: ["#overviewAttention", ".dashboard", "#panelAlertCard", "#routinesCard", "#preferencesCard"],
    lights: ["#lightsCard"],
    appliances: ["#vacuumCard", "#airCard"],
    camera: ["#cameraCard"],
    rooms: ["#roomCard"],
    chat: ["#assistantCard"]
  };

  function showTab(tabName, focusSelector) {
    if (current === "home" && tabName !== "home") {
      homeScroll = window.scrollY;
      homeFocus = document.activeElement;
    }
    const returning = current !== "home" && tabName === "home";
    current = tabName;
    tabs.forEach(btn => {
      const active = btn.dataset.tab === tabName;
      btn.classList.toggle("active", active);
      if (active) btn.setAttribute("aria-current", "page");
      else btn.removeAttribute("aria-current");
    });

    Object.keys(cards).forEach(key => {
      const selectors = cards[key];
      selectors.forEach(sel => {
        const el = $(sel);
        if (!el) return;
        
        if (key === "chat" && !(caps && caps.chat)) {
          el.classList.add("hidden");
          return;
        }

        if (tabName === key) {
          el.classList.remove("tab-hidden");
        } else {
          el.classList.add("tab-hidden");
        }
      });
    });
    
    window.scrollTo({ top: returning ? homeScroll : 0, behavior: "instant" });
    if (returning && homeFocus?.isConnected && homeFocus !== document.body) {
      homeFocus.focus({ preventScroll: true });
    } else if (focusSelector) {
      const destination = $(focusSelector);
      if (destination) {
        if (!destination.matches("button, a, summary, input, select")) destination.setAttribute("tabindex", "-1");
        destination.focus();
        destination.scrollIntoView?.({ block: "nearest" });
      }
    }
  }

  tabs.forEach(btn => {
    btn.addEventListener("click", () => {
      showTab(btn.dataset.tab);
    });
  });

  showTab("home");
  return showTab;
}

// --- init --------------------------------------------------------------------

async function init() {
  if (typeof dashboardUI !== "undefined") dashboardUI.installStrings();
  hydrateIcons();
  applyLang();
  wireEvents();

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }

  try {
    caps = await api("GET", "/api/capabilities");
  } catch (e) {
    toast(t("controls_failed") + e.message, "bad");
    caps = { bulbs: [], scene_modes: [], color_cycle: [], dance_patterns: [] };
  }
  buildBulbGrid();
  buildDynamic();
  if (caps.favorite_speed) {
    const fs = $("#airFavSpeed");
    fs.min = caps.favorite_speed.min; fs.max = caps.favorite_speed.max; fs.step = caps.favorite_speed.step;
  }
  if (caps.chat) {
    $("#assistantCard").classList.remove("hidden");
    const chatTab = $('[data-tab="chat"]');
    if (chatTab) chatTab.classList.remove("hidden");
  } else {
    const chatTab = $('[data-tab="chat"]');
    if (chatTab) chatTab.classList.add("hidden");
  }

  wireHousehold(caps.actions || []);
  const showTab = initTabs();
  if (typeof dashboardUI !== "undefined") dashboardUI.init(showTab);

  connectEvents();       // live pushes; falls back to polling on failure
  pollStatus();          // immediate first paint even if SSE is slow to open
  loadRooms();
  setInterval(() => {
    if (document.hidden || (sseConnected && Date.now() - statusReceivedAt < 15000)) return;
    pollStatus();
  }, POLL_MS);
}

init();

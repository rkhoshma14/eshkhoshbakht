// ============ پنل خوشبخت — منطق اصلی اپ ============

// اگه داخل Telegram Mini App باز شده باشیم initData پر خواهد بود؛
// چون اسکریپت telegram-web-app.js بیرون تلگرام هم شیء WebApp رو می‌سازه،
// صرفِ وجودش کافی نیست و باید initData رو هم چک کرد.
const TG = (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.initData)
  ? window.Telegram.WebApp
  : null;

const state = {
  tab: "overview",
  view: "list",
  subs: [],
  currentSub: null,
  pingResults: null,
  generated: [],
  currentGen: null,
  cart: [],
  // وقتی می‌خوایم به یک ساب سفارشی موجود کانفیگ اضافه کنیم
  targetGenId: null,
  targetGenName: "",
};

const app = document.getElementById("app");

/* ---------- SVG icons (line) ---------- */
const ICONS = {
  logo: '<svg viewBox="0 0 24 24"><path d="M12 3l2.2 4.5L19 8.3l-3.5 3.4.8 4.8L12 14.5 7.7 16.5l.8-4.8L5 8.3l4.8-.8L12 3z"/><circle cx="12" cy="12" r="2"/></svg>',
  subs: '<svg viewBox="0 0 24 24"><path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/></svg>',
  custom: '<svg viewBox="0 0 24 24"><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/><circle cx="12" cy="12" r="3"/></svg>',
  plus: '<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>',
  logout: '<svg viewBox="0 0 24 24"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/></svg>',
  chevron: '<svg viewBox="0 0 24 24"><path d="M15 18l-6-6 6-6"/></svg>',
  back: '<svg viewBox="0 0 24 24"><path d="M15 18l-6-6 6-6"/></svg>',
  refresh: '<svg viewBox="0 0 24 24"><path d="M23 4v6h-6M1 20v-6h6"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>',
  ping: '<svg viewBox="0 0 24 24"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>',
  broom: '<svg viewBox="0 0 24 24"><path d="M3 21l2-2m0 0l7.5-7.5m-7.5 7.5l7.5-7.5m0 0L19 4m-6.5 6.5L19 4m0 0l-2-2"/><path d="M14 7l3 3"/></svg>',
  export: '<svg viewBox="0 0 24 24"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12"/></svg>',
  note: '<svg viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M16 13H8M16 17H8M10 9H8"/></svg>',
  trash: '<svg viewBox="0 0 24 24"><path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6h14z"/><path d="M10 11v6M14 11v6"/></svg>',
  edit: '<svg viewBox="0 0 24 24"><path d="M12 20h9M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z"/></svg>',
  copy: '<svg viewBox="0 0 24 24"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>',
  link: '<svg viewBox="0 0 24 24"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>',
  empty: '<svg viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 9h6M9 15h3"/></svg>',
  check: '<svg viewBox="0 0 24 24"><path d="M20 6L9 17l-5-5"/></svg>',
};

function icon(name, cls) {
  const svg = ICONS[name] || ICONS.empty;
  return `<span class="icon ${cls || ""}" aria-hidden="true">${svg}</span>`;
}

// ---------- کمکی‌ها ----------

async function api(method, path, body) {
  const opts = { method, credentials: "same-origin", headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  if (res.status === 401) {
    window.location.href = "/panel/login";
    throw new Error("unauthorized");
  }
  let data = null;
  try { data = await res.json(); } catch (e) { /* no body */ }
  if (!res.ok) {
    throw new Error((data && data.error) || `خطای ${res.status}`);
  }
  return data;
}

function toast(msg, isError) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.className = "toast show" + (isError ? " error" : "");
  setTimeout(() => { t.className = "toast" + (isError ? " error" : ""); }, 2400);
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("کپی شد");
  } catch (e) {
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    document.body.removeChild(ta);
    toast("کپی شد");
  }
}

function esc(s) {
  const d = document.createElement("div");
  d.textContent = s == null ? "" : String(s);
  return d.innerHTML;
}

function fmtDate(iso) {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    return d.toLocaleString("fa-IR", { dateStyle: "short", timeStyle: "short" });
  } catch (e) {
    return iso;
  }
}

let modalRoot = null;
function openModal(html) {
  closeModal();
  modalRoot = document.createElement("div");
  modalRoot.className = "modal-backdrop";
  modalRoot.innerHTML = `<div class="modal">${html}</div>`;
  modalRoot.addEventListener("click", (e) => { if (e.target === modalRoot) closeModal(); });
  document.body.appendChild(modalRoot);
}
function closeModal() {
  if (modalRoot) { modalRoot.remove(); modalRoot = null; }
}

// ---------- بارگذاری اولیه ----------

async function authenticateWithTelegram() {
  try {
    const res = await fetch("/api/auth/webapp", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ initData: TG.initData }),
    });
    if (res.ok) return true;
    const d = await res.json().catch(() => ({}));
    app.innerHTML = `<div class="empty-state">${icon("empty", "icon-lg")}<div>ورود ناموفق بود: ${esc(d.error || "")}</div></div>`;
    return false;
  } catch (e) {
    app.innerHTML = `<div class="empty-state">${icon("empty", "icon-lg")}<div>خطا در اتصال به سرور</div></div>`;
    return false;
  }
}

async function boot() {
  // اگه داخل Mini App تلگرام هستیم، اول با initData وارد میشیم — همه‌چیز
  // توی همین یک بارگذاری صفحه انجام میشه، بدون هیچ ریدایرکت یا ناوبری وسط کار.
  if (TG) {
    const ok = await authenticateWithTelegram();
    if (!ok) return;
  }

  try {
    state.subs = await api("GET", "/api/subs");
  } catch (e) {
    app.innerHTML = `<div class="empty-state">${icon("empty", "icon-lg")}<div>خطا: ${esc(e.message)}</div></div>`;
    return;
  }
  try {
    state.generated = await api("GET", "/api/generated");
  } catch (e) { /* آمار سفارشی‌ها اختیاریه، جلوی بوت رو نمی‌گیره */ }
  render();
}

// ---------- شل + رندر اصلی ----------

function renderShell(body) {
  const showNav = state.view === "list";
  const logoutBtn = TG ? "" : `<a href="/panel/logout" class="btn-sm btn" style="text-decoration:none">${icon("logout", "icon-sm")} خروج</a>`;
  const logoutSidebar = TG ? "" : `<a class="nav-item" href="/panel/logout">${icon("logout")} خروج</a>`;
  return `
    <div class="mobile-bar">
      <strong>${icon("logo")} خوشبخت</strong>
      ${logoutBtn}
    </div>
    <div class="app-layout">
      <aside class="sidebar">
        <div class="sidebar-brand">
          <div class="sidebar-brand-icon">${ICONS.logo}</div>
          <div class="sidebar-brand-text">
            <strong>خوشبخت</strong>
            <span>پنل مدیریت تونل</span>
          </div>
        </div>
        <nav class="nav-section">
          <button class="nav-item ${state.tab === "overview" && showNav ? "active" : ""}" data-tab="overview">
            ${icon("logo")} داشبورد
          </button>
          <button class="nav-item ${state.tab === "subs" && showNav ? "active" : ""}" data-tab="subs">
            ${icon("subs")} اشتراک‌ها
          </button>
          <button class="nav-item ${state.tab === "generated" && showNav ? "active" : ""}" data-tab="generated">
            ${icon("custom")} اشتراک‌های سفارشی
          </button>
        </nav>
        <div class="sidebar-footer">${logoutSidebar}</div>
      </aside>
      <main class="main">${body}</main>
    </div>
    <nav class="mobile-nav">
      <button class="${state.tab === "overview" ? "active" : ""}" data-tab="overview">${icon("logo")}<span>داشبورد</span></button>
      <button class="${state.tab === "subs" ? "active" : ""}" data-tab="subs">${icon("subs")}<span>اشتراک‌ها</span></button>
      <button class="${state.tab === "generated" ? "active" : ""}" data-tab="generated">${icon("custom")}<span>سفارشی</span></button>
    </nav>
    ${renderCartBar()}
  `;
}

// ---------- دکمه‌ی برگشت نیتیو تلگرام (فقط داخل Mini App) ----------

function tgBackHandler() {
  if (state.view === "sub-detail") { state.view = "list"; state.currentSub = null; render(); }
  else if (state.view === "gen-detail") { state.view = "list"; state.currentGen = null; render(); }
}
if (TG) {
  TG.BackButton.onClick(tgBackHandler);
}

function syncTgBackButton() {
  if (!TG) return;
  if (state.view === "sub-detail" || state.view === "gen-detail") TG.BackButton.show();
  else TG.BackButton.hide();
}

function render() {
  let body = "";
  if (state.view === "sub-detail" && state.currentSub) {
    body = renderSubDetail(state.currentSub);
  } else if (state.view === "gen-detail" && state.currentGen) {
    body = renderGenDetail(state.currentGen);
  } else if (state.tab === "overview") {
    body = renderOverview();
  } else if (state.tab === "subs") {
    body = renderSubsList();
  } else {
    body = renderGeneratedList();
  }

  app.innerHTML = renderShell(body);
  bindTopLevelEvents();
  bindCartBarEvents();
  syncTgBackButton();
}

function bindTopLevelEvents() {
  document.querySelectorAll("[data-tab]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.tab = btn.dataset.tab;
      state.view = "list";
      if (state.tab === "generated") loadGenerated();
      else render();
    });
  });
}

// ================= داشبورد (نمای کلی) =================

function renderOverview() {
  const totalSubs = state.subs.length;
  const totalConfigs = state.subs.reduce((sum, s) => sum + (s.config_count || 0), 0);
  const withNote = state.subs.filter((s) => s.note).length;
  const totalGenerated = state.generated.length;

  const recent = [...state.subs]
    .sort((a, b) => new Date(b.updated_at || 0) - new Date(a.updated_at || 0))
    .slice(0, 5);

  const recentHtml = recent.length
    ? recent.map((s) => `
      <div class="list-item" data-open-sub="${s.id}">
        <div>
          <div class="title">${esc(s.name)}</div>
          <div class="subtitle">${s.config_count} کانفیگ · ${fmtDate(s.updated_at)}${s.note ? " · یادداشت" : ""}</div>
        </div>
        <span class="chevron">${icon("chevron")}</span>
      </div>`).join("")
    : `<div class="empty-state">${icon("empty", "icon-lg")}<div>هنوز اشتراکی اضافه نکردی.</div></div>`;

  setTimeout(() => {
    app.querySelectorAll("[data-open-sub]").forEach((el) => {
      el.addEventListener("click", () => openSub(parseInt(el.dataset.openSub)));
    });
    const addBtn = document.getElementById("overview-add-btn");
    if (addBtn) addBtn.addEventListener("click", openAddSubModal);
  });

  return `
    <div class="page-header">
      <div>
        <h2>${icon("logo")} داشبورد</h2>
        <p class="page-desc">یک نگاه کلی به اشتراک‌ها و کانفیگ‌هات</p>
      </div>
      <button class="btn" id="overview-add-btn">${icon("plus")} افزودن اشتراک</button>
    </div>
    <div class="stat-grid">
      <div class="stat-card" style="--accent:var(--gold)">
        <div class="stat-label">${icon("subs")} کل اشتراک‌ها</div>
        <div class="stat-value">${totalSubs}</div>
      </div>
      <div class="stat-card" style="--accent:var(--teal)">
        <div class="stat-label">${icon("link")} کل کانفیگ‌ها</div>
        <div class="stat-value">${totalConfigs}</div>
      </div>
      <div class="stat-card" style="--accent:var(--teal)">
        <div class="stat-label">${icon("note")} یادداشت‌دار</div>
        <div class="stat-value">${withNote}</div>
      </div>
      <div class="stat-card" style="--accent:var(--gold)">
        <div class="stat-label">${icon("custom")} اشتراک سفارشی</div>
        <div class="stat-value">${totalGenerated}</div>
      </div>
    </div>
    <div class="card">
      <div class="card-header"><div class="detail-title" style="font-size:.95rem">آخرین اشتراک‌ها</div></div>
      ${recentHtml}
    </div>
  `;
}

// ================= تب اشتراک‌ها =================

function renderSubsList() {
  const items = state.subs.map((s) => `
    <div class="list-item" data-open-sub="${s.id}">
      <div>
        <div class="title">${esc(s.name)}</div>
        <div class="subtitle">${s.config_count} کانفیگ · ${fmtDate(s.updated_at)}${s.note ? " · یادداشت" : ""}</div>
      </div>
      <span class="chevron">${icon("chevron")}</span>
    </div>
  `).join("");

  setTimeout(() => {
    app.querySelectorAll("[data-open-sub]").forEach((el) => {
      el.addEventListener("click", () => openSub(parseInt(el.dataset.openSub)));
    });
    const addBtn = document.getElementById("add-sub-btn");
    if (addBtn) addBtn.addEventListener("click", openAddSubModal);
    const cancelAdd = document.getElementById("cancel-add-mode");
    if (cancelAdd) cancelAdd.addEventListener("click", cancelAddToGenerated);
  });

  return `
    ${state.targetGenId ? `
    <div class="card" style="border-color:var(--gold);background:var(--gold-dim)">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap">
        <div>
          <div class="title" style="color:var(--gold);font-weight:700">افزودن کانفیگ به «${esc(state.targetGenName)}»</div>
          <div class="subtitle" style="margin-top:4px">یک اشتراک را باز کن، کانفیگ‌ها را تیک بزن، بعد از نوار پایین «افزودن» را بزن. لینک ساب عوض نمی‌شود.</div>
        </div>
        <button class="btn-sm btn" id="cancel-add-mode">انصراف</button>
      </div>
    </div>` : ""}
    <div class="page-header">
      <div>
        <h2>${icon("subs")} اشتراک‌ها</h2>
        <p class="page-desc">منبع‌های اشتراک VPN خود را مدیریت کنید</p>
      </div>
      <button class="btn" id="add-sub-btn">${icon("plus")} افزودن اشتراک</button>
    </div>
    ${state.subs.length ? items : `<div class="empty-state">${icon("empty", "icon-lg")}<div>هنوز اشتراکی اضافه نکردی.</div></div>`}
  `;
}

function openAddSubModal() {
  openModal(`
    <h2>${icon("plus")} افزودن اشتراک</h2>
    <label>لینک اشتراک</label>
    <input type="url" id="f-url" placeholder="https://..." dir="ltr"/>
    <label>اسم</label>
    <input type="text" id="f-name" placeholder="مثلا: آمریکا - محمد"/>
    <label>یادداشت (اختیاری)</label>
    <textarea id="f-note"></textarea>
    <div class="modal-actions">
      <button class="btn-outline btn" id="cancel-btn">انصراف</button>
      <button class="btn" id="submit-btn">افزودن</button>
    </div>
  `);
  document.getElementById("cancel-btn").addEventListener("click", closeModal);
  document.getElementById("submit-btn").addEventListener("click", async () => {
    const sub_url = document.getElementById("f-url").value.trim();
    const name = document.getElementById("f-name").value.trim();
    const note = document.getElementById("f-note").value.trim();
    if (!sub_url || !name) { toast("لینک و اسم اجباریه.", true); return; }
    const btn = document.getElementById("submit-btn");
    btn.disabled = true; btn.textContent = "در حال بررسی...";
    try {
      await api("POST", "/api/subs", { sub_url, name, note });
      closeModal();
      toast("اشتراک اضافه شد");
      state.subs = await api("GET", "/api/subs");
      render();
    } catch (e) {
      toast(e.message, true);
      btn.disabled = false; btn.innerHTML = "افزودن";
    }
  });
}

async function openSub(id) {
  try {
    state.currentSub = await api("GET", `/api/subs/${id}`);
    state.pingResults = null;
    state.view = "sub-detail";
    render();
  } catch (e) {
    toast(e.message, true);
  }
}

function renderSubDetail(sub) {
  const rows = sub.configs.map((c) => {
    const inCart = state.cart.some((it) => it.sub_id === sub.id && it.index === c.index);
    const ms = state.pingResults ? state.pingResults[c.index] : undefined;
    let msBadge = "";
    if (ms !== undefined) {
      if (ms === null) {
        msBadge = '<span class="badge badge-dead">تایم‌اوت</span>';
      } else {
        const tier = ms < 90 ? "badge-ping-good" : ms < 180 ? "badge-ping-mid" : "badge-ping-bad";
        msBadge = `<span class="badge ${tier}">${Math.round(ms)} ms</span>`;
      }
    }
    return `
      <div class="config-row">
        <input type="checkbox" class="cfg-check" data-idx="${c.index}" ${inCart ? "checked" : ""}/>
        <span class="badge">${esc(c.protocol)}</span>
        <span class="remark">${esc(c.remark || "(بدون نام)")}</span>
        ${msBadge}
        <div class="config-actions">
          <button class="btn-sm btn" data-rename="${c.index}" title="رنیم">${icon("edit", "icon-sm")}</button>
        </div>
      </div>
    `;
  }).join("");

  setTimeout(() => bindSubDetailEvents(sub), 0);

  return `
    <button class="back-link" id="back-to-subs">${icon("back", "icon-sm")} بازگشت به اشتراک‌ها</button>
    <div class="card">
      <div class="card-header">
        <div>
          <div class="detail-title">${esc(sub.name)}</div>
          <div class="detail-meta">${sub.config_count} کانفیگ · ${fmtDate(sub.updated_at)}</div>
        </div>
        <button class="btn-sm btn btn-danger" id="delete-sub-btn">${icon("trash", "icon-sm")} حذف</button>
      </div>
      ${sub.note ? `<div class="note-box">${esc(sub.note)}</div>` : ""}
      <div class="action-bar">
        <button class="btn-sm btn" id="refresh-sub-btn">${icon("refresh", "icon-sm")} بروزرسانی</button>
        <button class="btn-sm btn" id="ping-sub-btn">${icon("ping", "icon-sm")} پینگ</button>
        <button class="btn-sm btn" id="dead-sub-btn">${icon("broom", "icon-sm")} حذف مرده‌ها</button>
        <button class="btn-sm btn" id="export-sub-btn">${icon("export", "icon-sm")} خروجی</button>
        <button class="btn-sm btn" id="note-sub-btn">${icon("note", "icon-sm")} یادداشت</button>
      </div>
    </div>
    <div class="card">
      ${rows || `<div class="empty-state">این اشتراک کانفیگی نداره.</div>`}
    </div>
  `;
}

function bindSubDetailEvents(sub) {
  const back = document.getElementById("back-to-subs");
  if (back) back.addEventListener("click", () => { state.view = "list"; state.currentSub = null; render(); });

  const del = document.getElementById("delete-sub-btn");
  if (del) del.addEventListener("click", () => confirmDeleteSub(sub));

  const refresh = document.getElementById("refresh-sub-btn");
  if (refresh) refresh.addEventListener("click", () => refreshSub(sub.id));

  const ping = document.getElementById("ping-sub-btn");
  if (ping) ping.addEventListener("click", () => pingSub(sub.id));

  const dead = document.getElementById("dead-sub-btn");
  if (dead) dead.addEventListener("click", () => previewDeadConfigs(sub));

  const exp = document.getElementById("export-sub-btn");
  if (exp) exp.addEventListener("click", () => exportSub(sub.id));

  const note = document.getElementById("note-sub-btn");
  if (note) note.addEventListener("click", () => openNoteModal(sub));

  app.querySelectorAll(".cfg-check").forEach((chk) => {
    chk.addEventListener("change", () => toggleCart(sub, parseInt(chk.dataset.idx), chk.checked));
  });

  app.querySelectorAll("[data-rename]").forEach((btn) => {
    btn.addEventListener("click", () => openRenameModal(sub, parseInt(btn.dataset.rename)));
  });
}

async function refreshSub(id) {
  toast("در حال بروزرسانی...");
  try {
    state.currentSub = await api("POST", `/api/subs/${id}/refresh`);
    state.pingResults = null;
    render();
    toast("بروزرسانی شد");
  } catch (e) { toast(e.message, true); }
}

function confirmDeleteSub(sub) {
  openModal(`
    <h2>${icon("trash")} حذف اشتراک</h2>
    <p class="muted">مطمئنی می‌خوای «${esc(sub.name)}» با ${sub.config_count} کانفیگ حذف بشه؟ این کار قابل بازگشت نیست.</p>
    <div class="modal-actions">
      <button class="btn-outline btn" id="cancel-btn">انصراف</button>
      <button class="btn btn-danger" id="confirm-btn">بله، حذف کن</button>
    </div>
  `);
  document.getElementById("cancel-btn").addEventListener("click", closeModal);
  document.getElementById("confirm-btn").addEventListener("click", async () => {
    try {
      await api("DELETE", `/api/subs/${sub.id}`);
      closeModal();
      toast("حذف شد");
      state.cart = state.cart.filter((it) => it.sub_id !== sub.id);
      state.subs = await api("GET", "/api/subs");
      state.view = "list";
      state.currentSub = null;
      render();
    } catch (e) { toast(e.message, true); }
  });
}

async function pingSub(id) {
  toast(`در حال پینگ ${state.currentSub.configs.length} کانفیگ...`);
  try {
    const results = await api("GET", `/api/subs/${id}/ping`);
    state.pingResults = {};
    results.forEach((r) => { state.pingResults[r.index] = r.ms; });
    render();
    toast("پینگ تمام شد");
  } catch (e) { toast(e.message, true); }
}

async function previewDeadConfigs(sub) {
  toast("در حال بررسی کانفیگ‌های مرده...");
  let data;
  try {
    data = await api("POST", `/api/subs/${sub.id}/delete-dead`, {});
  } catch (e) { toast(e.message, true); return; }

  if (!data.dead || data.dead.length === 0) {
    toast("همه‌ی کانفیگ‌ها زنده‌اند");
    return;
  }

  const list = data.dead.slice(0, 30).map((d) => `<div class="config-row"><span class="badge">${esc(d.protocol)}</span><span class="remark">${esc(d.remark || "(بدون نام)")}</span></div>`).join("");
  const indices = data.dead.map((d) => d.index);

  openModal(`
    <h2>${icon("broom")} ${data.dead.length} کانفیگ مرده</h2>
    <div style="max-height:260px;overflow-y:auto;margin:10px 0">${list}</div>
    ${data.dead.length > 30 ? `<p class="muted">و ${data.dead.length - 30} تای دیگر...</p>` : ""}
    <p class="muted" style="margin-top:8px">این کار قابل بازگشت نیست.</p>
    <div class="modal-actions">
      <button class="btn-outline btn" id="cancel-btn">انصراف</button>
      <button class="btn btn-danger" id="confirm-btn">حذف ${data.dead.length} کانفیگ</button>
    </div>
  `);
  document.getElementById("cancel-btn").addEventListener("click", closeModal);
  document.getElementById("confirm-btn").addEventListener("click", async () => {
    try {
      const result = await api("POST", `/api/subs/${sub.id}/delete-dead`, { indices });
      state.currentSub = result;
      state.pingResults = null;
      closeModal();
      toast(`${result.removed} کانفیگ حذف شد`);
      render();
    } catch (e) { toast(e.message, true); }
  });
}

async function exportSub(id) {
  toast("در حال آماده‌سازی...");
  let data;
  try {
    data = await api("GET", `/api/subs/${id}/export`);
  } catch (e) { toast(e.message, true); return; }

  openModal(`
    <h2>${icon("export")} خروجی اشتراک</h2>
    <p class="muted">متن base64 آمادهٔ وارد کردن در کلاینت VPN.</p>
    <textarea id="export-text" rows="8" readonly>${esc(data.content)}</textarea>
    <div class="modal-actions">
      <button class="btn-outline btn" id="close-btn">بستن</button>
      <button class="btn" id="copy-btn">${icon("copy", "icon-sm")} کپی</button>
    </div>
  `);
  document.getElementById("close-btn").addEventListener("click", closeModal);
  document.getElementById("copy-btn").addEventListener("click", () => copyText(data.content));
}

function openNoteModal(sub) {
  openModal(`
    <h2>${icon("note")} یادداشت «${esc(sub.name)}»</h2>
    ${sub.note ? `<div class="note-box" style="margin-bottom:10px">${esc(sub.note)}</div>` : ""}
    <label>${sub.note ? "متن جدید به یادداشت اضافه می‌شود" : "یادداشت جدید"}</label>
    <textarea id="note-text"></textarea>
    <div class="modal-actions">
      ${sub.note ? `<button class="btn-outline btn btn-danger" id="clear-btn">${icon("trash", "icon-sm")} حذف کامل</button>` : ""}
      <button class="btn-outline btn" id="cancel-btn">انصراف</button>
      <button class="btn" id="save-btn">ذخیره</button>
    </div>
  `);
  document.getElementById("cancel-btn").addEventListener("click", closeModal);
  document.getElementById("save-btn").addEventListener("click", async () => {
    const text = document.getElementById("note-text").value.trim();
    if (!text) { toast("متن خالی است.", true); return; }
    try {
      state.currentSub = await api("POST", `/api/subs/${sub.id}/note`, { note: text });
      closeModal();
      toast("یادداشت ذخیره شد");
      render();
    } catch (e) { toast(e.message, true); }
  });
  const clearBtn = document.getElementById("clear-btn");
  if (clearBtn) clearBtn.addEventListener("click", async () => {
    try {
      state.currentSub = await api("POST", `/api/subs/${sub.id}/note`, { clear: true });
      closeModal();
      toast("یادداشت حذف شد");
      render();
    } catch (e) { toast(e.message, true); }
  });
}

function openRenameModal(sub, idx) {
  const cfg = sub.configs.find((c) => c.index === idx);
  openModal(`
    <h2>${icon("edit")} رنیم کانفیگ</h2>
    <p class="muted" style="margin-bottom:10px"><span class="badge">${esc(cfg.protocol)}</span> ${esc(cfg.remark || "(بدون نام)")}</p>
    <label>اسم جدید</label>
    <input type="text" id="rename-name" placeholder="اسم دلخواه"/>
    <div class="modal-actions">
      <button class="btn-outline btn" id="cancel-btn">انصراف</button>
      <button class="btn" id="submit-btn">رنیم و کپی</button>
    </div>
  `);
  document.getElementById("cancel-btn").addEventListener("click", closeModal);
  document.getElementById("submit-btn").addEventListener("click", async () => {
    const name = document.getElementById("rename-name").value.trim();
    if (!name) { toast("اسم نمی‌تواند خالی باشد.", true); return; }
    try {
      const data = await api("POST", `/api/subs/${sub.id}/configs/${idx}/rename`, { name });
      closeModal();
      await copyText(data.renamed);
    } catch (e) { toast(e.message, true); }
  });
}

// ---------- سبد (cart) ساخت سفارشی ----------

function toggleCart(sub, idx, checked) {
  const cfg = sub.configs.find((c) => c.index === idx);
  if (!cfg) return;
  if (checked) {
    if (!state.cart.some((it) => it.sub_id === sub.id && it.index === idx)) {
      state.cart.push({
        sub_id: sub.id,
        sub_name: sub.name,
        index: idx,
        remark: cfg.remark || "",
        protocol: cfg.protocol,
        name: "",
      });
    }
  } else {
    state.cart = state.cart.filter((it) => !(it.sub_id === sub.id && it.index === idx));
  }
  renderCartBar();
  bindCartBarEvents();
}

function renderCartBar() {
  if (!state.cart.length) return `<div class="cart-bar" id="cart-bar"></div>`;
  const actionBtn = state.targetGenId
    ? `<button class="btn" id="cart-add-to-gen">${icon("plus", "icon-sm")} افزودن به «${esc(state.targetGenName)}»</button>`
    : `<button class="btn" id="cart-build">${icon("custom", "icon-sm")} ساخت اشتراک سفارشی</button>`;
  return `
    <div class="cart-bar show" id="cart-bar">
      <span class="count">${state.cart.length} کانفیگ انتخاب‌شده</span>
      <button class="btn-sm btn" id="cart-clear">پاک کردن</button>
      ${actionBtn}
    </div>
  `;
}

function bindCartBarEvents() {
  const clear = document.getElementById("cart-clear");
  if (clear) clear.addEventListener("click", () => { state.cart = []; render(); });
  const build = document.getElementById("cart-build");
  if (build) build.addEventListener("click", openBuildCustomModal);
  const addTo = document.getElementById("cart-add-to-gen");
  if (addTo) addTo.addEventListener("click", submitAddToGenerated);
}

async function submitAddToGenerated() {
  if (!state.targetGenId || !state.cart.length) return;
  const items = state.cart.map((it) => ({
    sub_id: it.sub_id,
    index: it.index,
    name: it.name || undefined,
  }));
  try {
    const result = await api("POST", `/api/generated/${state.targetGenId}/add-configs`, { items });
    const added = result.added || state.cart.length;
    const genId = state.targetGenId;
    state.cart = [];
    state.targetGenId = null;
    state.targetGenName = "";
    toast(`${added} کانفیگ اضافه شد`);
    state.currentGen = await api("GET", `/api/generated/${genId}`);
    state.tab = "generated";
    state.view = "gen-detail";
    render();
  } catch (e) {
    toast(e.message, true);
  }
}

function startAddToGenerated(gen) {
  state.targetGenId = gen.id;
  state.targetGenName = gen.name;
  state.cart = [];
  state.currentGen = null;
  state.tab = "subs";
  state.view = "list";
  toast(`کانفیگ‌ها را از اشتراک‌ها تیک بزن — بعد «افزودن به ${gen.name}»`);
  render();
}

function cancelAddToGenerated() {
  state.targetGenId = null;
  state.targetGenName = "";
  state.cart = [];
  render();
}

function openBuildCustomModal() {
  const itemsHtml = state.cart.map((it, i) => `
    <div class="config-row">
      <span class="badge">${esc(it.protocol)}</span>
      <span class="remark">${esc(it.remark || "(بدون نام)")} <span class="muted">· ${esc(it.sub_name)}</span></span>
      <input type="text" class="cart-name" data-i="${i}" placeholder="اسم دلخواه" style="width:120px;padding:6px 8px;font-size:.78rem" value="${esc(it.name)}"/>
    </div>
  `).join("");

  openModal(`
    <h2>${icon("custom")} ساخت اشتراک سفارشی</h2>
    <div style="max-height:200px;overflow-y:auto;margin-bottom:8px">${itemsHtml}</div>
    <label>اسم اشتراک</label>
    <input type="text" id="build-name" placeholder="مثلاً: انتخابی گیمینگ"/>
    <label>مدت اعتبار (روز) — ۰ = بدون انقضا</label>
    <input type="number" id="build-expiry" value="0" min="0" dir="ltr"/>
    <div class="modal-actions">
      <button class="btn-outline btn" id="cancel-btn">انصراف</button>
      <button class="btn" id="submit-btn">ساخت</button>
    </div>
  `);
  document.getElementById("cancel-btn").addEventListener("click", closeModal);
  document.getElementById("submit-btn").addEventListener("click", async () => {
    const name = document.getElementById("build-name").value.trim();
    const expiry_days = parseInt(document.getElementById("build-expiry").value) || 0;
    if (!name) { toast("اسم اشتراک اجباری است.", true); return; }
    document.querySelectorAll(".cart-name").forEach((inp) => {
      const i = parseInt(inp.dataset.i);
      state.cart[i].name = inp.value.trim();
    });
    const items = state.cart.map((it) => ({
      sub_id: it.sub_id,
      index: it.index,
      name: it.name || undefined,
    }));
    const btn = document.getElementById("submit-btn");
    btn.disabled = true;
    try {
      const gen = await api("POST", "/api/build-custom", { name, items, expiry_days });
      closeModal();
      state.cart = [];
      toast("اشتراک سفارشی ساخته شد");
      state.tab = "generated";
      await loadGenerated();
      if (gen && gen.id) openGen(gen.id);
    } catch (e) {
      toast(e.message, true);
      btn.disabled = false;
    }
  });
}

// ================= تب اشتراک‌های سفارشی =================

async function loadGenerated() {
  try {
    state.generated = await api("GET", "/api/generated");
    render();
  } catch (e) { toast(e.message, true); }
}

function renderGeneratedList() {
  const items = state.generated.map((g) => `
    <div class="list-item" data-open-gen="${g.id}">
      <div>
        <div class="title">${esc(g.name)} ${g.expired ? '<span class="badge badge-expired">منقضی</span>' : ""} ${g.live ? '<span class="badge badge-ms">لایو</span>' : ""}</div>
        <div class="subtitle">${g.config_count} کانفیگ · ${fmtDate(g.created_at)}${g.remaining_text ? ` · ${esc(g.remaining_text)}` : ""}</div>
      </div>
      <span class="chevron">${icon("chevron")}</span>
    </div>
  `).join("");

  setTimeout(() => {
    app.querySelectorAll("[data-open-gen]").forEach((el) => {
      el.addEventListener("click", () => openGen(parseInt(el.dataset.openGen)));
    });
  });

  return `
    <div class="page-header">
      <div>
        <h2>${icon("custom")} اشتراک‌های سفارشی</h2>
        <p class="page-desc">لینک‌های عمومی که خودت ساختی</p>
      </div>
    </div>
    ${state.generated.length ? items : `<div class="empty-state">${icon("empty", "icon-lg")}<div>هنوز اشتراک سفارشی نساختی.<br/>از تب اشتراک‌ها کانفیگ‌ها را تیک بزن.</div></div>`}
  `;
}

async function openGen(id) {
  try {
    state.currentGen = await api("GET", `/api/generated/${id}`);
    state.view = "gen-detail";
    render();
  } catch (e) { toast(e.message, true); }
}

function renderGenDetail(gen) {
  const rows = (gen.configs || []).map((c) => `
    <div class="config-row">
      <span class="badge">${esc(c.protocol)}</span>
      <span class="remark">${esc(c.remark || "(بدون نام)")}</span>
      <div class="config-actions">
        <button class="btn-sm btn" data-gen-rename="${c.index}" title="تغییر اسم">${icon("edit", "icon-sm")}</button>
        <button class="btn-sm btn btn-danger" data-gen-del="${c.index}" title="حذف">${icon("trash", "icon-sm")}</button>
      </div>
    </div>
  `).join("");

  setTimeout(() => {
    document.getElementById("back-to-gens").addEventListener("click", () => {
      state.view = "list"; state.currentGen = null; render();
    });
    document.getElementById("copy-gen-url").addEventListener("click", () => copyText(gen.url));
    document.getElementById("delete-gen-btn").addEventListener("click", () => confirmDeleteGen(gen));
    const addBtn = document.getElementById("add-to-gen-btn");
    if (addBtn) addBtn.addEventListener("click", () => startAddToGenerated(gen));
    app.querySelectorAll("[data-gen-rename]").forEach((btn) => {
      btn.addEventListener("click", () => openRenameGenConfig(gen, parseInt(btn.dataset.genRename)));
    });
    app.querySelectorAll("[data-gen-del]").forEach((btn) => {
      btn.addEventListener("click", () => confirmDeleteGenConfig(gen, parseInt(btn.dataset.genDel)));
    });
  }, 0);

  const expLabel = gen.expires_at
    ? (gen.expired ? `منقضی‌شده (${fmtDate(gen.expires_at)})` : fmtDate(gen.expires_at))
    : "بدون محدودیت";
  const liveLabel = gen.live ? " · همگام با منبع" : " · ثابت";
  const remainingLabel = gen.remaining_text ? gen.remaining_text : "";

  return `
    <button class="back-link" id="back-to-gens">${icon("back", "icon-sm")} بازگشت</button>
    <div class="card">
      <div class="card-header">
        <div>
          <div class="detail-title">${esc(gen.name)}</div>
          <div class="detail-meta">${gen.config_count} کانفیگ · ${fmtDate(gen.created_at)} · انقضا: ${expLabel}${liveLabel}</div>
          ${remainingLabel ? `<div class="detail-meta" style="margin-top:6px;font-weight:600;color:var(--accent)">${esc(remainingLabel)}</div>` : ""}
        </div>
        <button class="btn-sm btn btn-danger" id="delete-gen-btn">${icon("trash", "icon-sm")} حذف</button>
      </div>
      <label>لینک اشتراک</label>
      <code class="url">${esc(gen.url)}</code>
      <div class="action-bar">
        <button class="btn-sm btn" id="copy-gen-url">${icon("copy", "icon-sm")} کپی لینک</button>
        <button class="btn" id="add-to-gen-btn">${icon("plus", "icon-sm")} افزودن کانفیگ از اشتراک دیگر</button>
      </div>
    </div>
    <div class="card">${rows || '<div class="empty-state">کانفیگی نیست.</div>'}</div>
  `;
}

function openRenameGenConfig(gen, idx) {
  const cfg = (gen.configs || []).find((c) => c.index === idx);
  const current = cfg ? (cfg.remark || "") : "";
  openModal(`
    <h2>${icon("edit")} تغییر اسم کانفیگ</h2>
    <label>اسم جدید</label>
    <input type="text" id="gen-rename-input" value="${esc(current)}" placeholder="مثلاً: گیمینگ-۱" />
    <div class="modal-actions">
      <button class="btn-outline btn" id="cancel-btn">انصراف</button>
      <button class="btn" id="confirm-btn">ذخیره</button>
    </div>
  `);
  document.getElementById("cancel-btn").addEventListener("click", closeModal);
  document.getElementById("confirm-btn").addEventListener("click", async () => {
    const name = document.getElementById("gen-rename-input").value.trim();
    if (!name) return toast("اسم را وارد کن", true);
    try {
      state.currentGen = await api("POST", `/api/generated/${gen.id}/configs/${idx}/rename`, { name });
      closeModal();
      toast("اسم تغییر کرد");
      render();
    } catch (e) { toast(e.message, true); }
  });
}

function confirmDeleteGenConfig(gen, idx) {
  const cfg = (gen.configs || []).find((c) => c.index === idx);
  const label = cfg ? (cfg.remark || cfg.protocol) : `#${idx}`;
  openModal(`
    <h2>${icon("trash")} حذف کانفیگ</h2>
    <p class="muted">«${esc(label)}» از این اشتراک سفارشی حذف شود؟</p>
    <div class="modal-actions">
      <button class="btn-outline btn" id="cancel-btn">انصراف</button>
      <button class="btn btn-danger" id="confirm-btn">حذف</button>
    </div>
  `);
  document.getElementById("cancel-btn").addEventListener("click", closeModal);
  document.getElementById("confirm-btn").addEventListener("click", async () => {
    try {
      state.currentGen = await api("DELETE", `/api/generated/${gen.id}/configs/${idx}`);
      closeModal();
      toast("حذف شد");
      render();
    } catch (e) { toast(e.message, true); }
  });
}

function confirmDeleteGen(gen) {
  openModal(`
    <h2>${icon("trash")} حذف اشتراک سفارشی</h2>
    <p class="muted">مطمئنی می‌خوای «${esc(gen.name)}» حذف بشه؟ لینکش دیگر کار نمی‌کند.</p>
    <div class="modal-actions">
      <button class="btn-outline btn" id="cancel-btn">انصراف</button>
      <button class="btn btn-danger" id="confirm-btn">بله، حذف کن</button>
    </div>
  `);
  document.getElementById("cancel-btn").addEventListener("click", closeModal);
  document.getElementById("confirm-btn").addEventListener("click", async () => {
    try {
      await api("DELETE", `/api/generated/${gen.id}`);
      closeModal();
      toast("حذف شد");
      state.view = "list";
      state.currentGen = null;
      await loadGenerated();
    } catch (e) { toast(e.message, true); }
  });
}

if (TG) {
  try {
    TG.ready();
    TG.expand();
    TG.setHeaderColor("#070f1a");
    TG.setBackgroundColor("#070f1a");
  } catch (e) { /* نسخه‌های قدیمی کلاینت تلگرام ممکنه این متدها رو نداشته باشن */ }
}

boot();

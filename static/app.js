// ============ پنل خوشبخت — منطق اصلی اپ ============

const state = {
  tab: "subs",           // "subs" | "generated"
  view: "list",          // "list" | "sub-detail" | "gen-detail"
  subs: [],
  currentSub: null,
  pingResults: null,     // {index: ms}
  generated: [],
  currentGen: null,
  cart: [],               // [{sub_id, sub_name, index, remark, protocol, name}]
};

const app = document.getElementById("app");

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
    toast("کپی شد ✓");
  } catch (e) {
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    document.body.removeChild(ta);
    toast("کپی شد ✓");
  }
}

function esc(s) {
  const d = document.createElement("div");
  d.textContent = s == null ? "" : String(s);
  return d.innerHTML;
}

function fmtDate(iso) {
  if (!iso) return "-";
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

async function boot() {
  try {
    state.subs = await api("GET", "/api/subs");
  } catch (e) {
    app.innerHTML = `<div class="empty-state">خطا: ${esc(e.message)}</div>`;
    return;
  }
  render();
}

// ---------- رندر اصلی ----------

function render() {
  let body = "";
  if (state.view === "sub-detail" && state.currentSub) {
    body = renderSubDetail(state.currentSub);
  } else if (state.view === "gen-detail" && state.currentGen) {
    body = renderGenDetail(state.currentGen);
  } else if (state.tab === "subs") {
    body = renderSubsList();
  } else {
    body = renderGeneratedList();
  }

  app.innerHTML = `
    <div class="topbar">
      <h1>⚡ پنل خوشبخت</h1>
      <a href="/panel/logout" class="btn-sm btn" style="text-decoration:none">خروج</a>
    </div>
    ${state.view === "list" ? `
      <div class="tabs">
        <button class="tab-btn ${state.tab === "subs" ? "active" : ""}" data-tab="subs">📋 اشتراک‌ها</button>
        <button class="tab-btn ${state.tab === "generated" ? "active" : ""}" data-tab="generated">🛠 اشتراک‌های سفارشی من</button>
      </div>
    ` : ""}
    ${body}
    ${renderCartBar()}
  `;

  bindTopLevelEvents();
  bindCartBarEvents();
}

function bindTopLevelEvents() {
  app.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.tab = btn.dataset.tab;
      state.view = "list";
      if (state.tab === "generated") loadGenerated();
      else render();
    });
  });
}

// ================= تب اشتراک‌ها =================

function renderSubsList() {
  const items = state.subs.map((s) => `
    <div class="list-item" data-open-sub="${s.id}">
      <div>
        <div class="title">${esc(s.name)}</div>
        <div class="subtitle">${s.config_count} کانفیگ · بروزرسانی ${fmtDate(s.updated_at)}${s.note ? " · 📝 یادداشت داره" : ""}</div>
      </div>
      <span>›</span>
    </div>
  `).join("");

  setTimeout(() => {
    app.querySelectorAll("[data-open-sub]").forEach((el) => {
      el.addEventListener("click", () => openSub(parseInt(el.dataset.openSub)));
    });
    const addBtn = document.getElementById("add-sub-btn");
    if (addBtn) addBtn.addEventListener("click", openAddSubModal);
  });

  return `
    <div class="card">
      <button class="btn" id="add-sub-btn">➕ افزودن اشتراک</button>
    </div>
    ${state.subs.length ? items : '<div class="empty-state">هنوز اشتراکی اضافه نکردی.</div>'}
  `;
}

function openAddSubModal() {
  openModal(`
    <h2>➕ افزودن اشتراک</h2>
    <label>لینک اشتراک</label>
    <input type="url" id="f-url" placeholder="https://..."/>
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
      toast("اشتراک اضافه شد ✓");
      state.subs = await api("GET", "/api/subs");
      render();
    } catch (e) {
      toast(e.message, true);
      btn.disabled = false; btn.textContent = "افزودن";
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
      msBadge = ms === null
        ? '<span class="badge badge-dead">تایم‌اوت</span>'
        : `<span class="badge badge-ms">${Math.round(ms)}ms</span>`;
    }
    return `
      <div class="config-row">
        <input type="checkbox" class="cfg-check" data-idx="${c.index}" ${inCart ? "checked" : ""}/>
        <span class="badge">${esc(c.protocol)}</span>
        <span class="remark">${esc(c.remark || "(بدون نام)")}</span>
        ${msBadge}
        <div class="config-actions">
          <button class="btn-sm btn" data-rename="${c.index}">✏️</button>
        </div>
      </div>
    `;
  }).join("");

  setTimeout(() => bindSubDetailEvents(sub), 0);

  return `
    <div class="back-link" id="back-to-subs">« بازگشت به اشتراک‌ها</div>
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:10px">
        <div>
          <div class="title" style="font-size:1.1rem;font-weight:700">${esc(sub.name)}</div>
          <div class="subtitle">${sub.config_count} کانفیگ · بروزرسانی ${fmtDate(sub.updated_at)}</div>
        </div>
        <button class="btn-sm btn btn-danger" id="delete-sub-btn">🗑 حذف اشتراک</button>
      </div>
      ${sub.note ? `<div style="margin-top:10px;white-space:pre-wrap;background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:10px;font-size:.85rem;color:var(--muted)">📝 ${esc(sub.note)}</div>` : ""}
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:14px">
        <button class="btn-sm btn" id="refresh-sub-btn">🔄 بروزرسانی</button>
        <button class="btn-sm btn" id="ping-sub-btn">📶 پینگ همه</button>
        <button class="btn-sm btn" id="dead-sub-btn">🧹 حذف مرده‌ها</button>
        <button class="btn-sm btn" id="export-sub-btn">📤 خروجی</button>
        <button class="btn-sm btn" id="note-sub-btn">📝 یادداشت</button>
      </div>
    </div>
    <div class="card">
      ${rows || '<div class="empty-state">این اشتراک کانفیگی نداره.</div>'}
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
    toast("بروزرسانی شد ✓");
  } catch (e) { toast(e.message, true); }
}

function confirmDeleteSub(sub) {
  openModal(`
    <h2>حذف اشتراک</h2>
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
      toast("حذف شد ✓");
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
    toast("پینگ تموم شد ✓");
  } catch (e) { toast(e.message, true); }
}

async function previewDeadConfigs(sub) {
  toast("در حال بررسی کانفیگ‌های مرده...");
  let data;
  try {
    data = await api("POST", `/api/subs/${sub.id}/delete-dead`, {});
  } catch (e) { toast(e.message, true); return; }

  if (!data.dead || data.dead.length === 0) {
    toast("همه‌ی کانفیگ‌ها زنده‌ان ✓");
    return;
  }

  const list = data.dead.slice(0, 30).map((d) => `<div class="config-row"><span class="badge">${esc(d.protocol)}</span><span class="remark">${esc(d.remark || "(بدون نام)")}</span></div>`).join("");
  const indices = data.dead.map((d) => d.index);

  openModal(`
    <h2>🧹 ${data.dead.length} کانفیگ مرده پیدا شد</h2>
    <div style="max-height:260px;overflow-y:auto;margin:10px 0">${list}</div>
    ${data.dead.length > 30 ? `<p class="muted">و ${data.dead.length - 30} تای دیگه...</p>` : ""}
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
      toast(`${result.removed} کانفیگ حذف شد ✓`);
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
    <h2>📤 خروجی اشتراک</h2>
    <p class="muted">این متن base64 آماده‌ی وارد کردن (import) به هر کلاینت VPNه.</p>
    <textarea id="export-text" rows="8" readonly>${esc(data.content)}</textarea>
    <div class="modal-actions">
      <button class="btn-outline btn" id="close-btn">بستن</button>
      <button class="btn" id="copy-btn">کپی</button>
    </div>
  `);
  document.getElementById("close-btn").addEventListener("click", closeModal);
  document.getElementById("copy-btn").addEventListener("click", () => copyText(data.content));
}

function openNoteModal(sub) {
  openModal(`
    <h2>📝 یادداشت «${esc(sub.name)}»</h2>
    ${sub.note ? `<div class="muted" style="white-space:pre-wrap;background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:10px;font-size:.85rem;margin-bottom:10px">${esc(sub.note)}</div>` : ""}
    <label>${sub.note ? "چیزی که بفرستی اضافه میشه (پاک نمیشه)" : "یادداشت جدید"}</label>
    <textarea id="note-text"></textarea>
    <div class="modal-actions">
      ${sub.note ? '<button class="btn-outline btn btn-danger" id="clear-btn">🗑 حذف کامل</button>' : ""}
      <button class="btn-outline btn" id="cancel-btn">انصراف</button>
      <button class="btn" id="save-btn">ذخیره</button>
    </div>
  `);
  document.getElementById("cancel-btn").addEventListener("click", closeModal);
  const clearBtn = document.getElementById("clear-btn");
  if (clearBtn) clearBtn.addEventListener("click", async () => {
    try {
      state.currentSub = await api("POST", `/api/subs/${sub.id}/note`, { clear: true });
      closeModal();
      toast("یادداشت حذف شد ✓");
      render();
    } catch (e) { toast(e.message, true); }
  });
  document.getElementById("save-btn").addEventListener("click", async () => {
    const text = document.getElementById("note-text").value.trim();
    if (!text) { toast("متن یادداشت خالیه.", true); return; }
    try {
      state.currentSub = await api("POST", `/api/subs/${sub.id}/note`, { note: text });
      closeModal();
      toast("یادداشت ذخیره شد ✓");
      render();
    } catch (e) { toast(e.message, true); }
  });
}

function openRenameModal(sub, idx) {
  const cfg = sub.configs.find((c) => c.index === idx);
  openModal(`
    <h2>✏️ رنیم کانفیگ</h2>
    <p class="muted">اسم فعلی: ${esc(cfg ? cfg.remark : "")}</p>
    <label>اسم جدید</label>
    <input type="text" id="rename-text"/>
    <p class="muted" style="margin-top:10px">این تغییر فقط برای گرفتن یه خروجی موقته و روی خودِ اشتراک ذخیره نمیشه؛ برای ذخیره‌ی دائمی از «ساخت اشتراک سفارشی» (تیک بزن و پایین صفحه «بساز») استفاده کن.</p>
    <div class="modal-actions">
      <button class="btn-outline btn" id="cancel-btn">انصراف</button>
      <button class="btn" id="submit-btn">دریافت خروجی</button>
    </div>
  `);
  document.getElementById("cancel-btn").addEventListener("click", closeModal);
  document.getElementById("submit-btn").addEventListener("click", async () => {
    const name = document.getElementById("rename-text").value.trim();
    if (!name) { toast("اسم نمی‌تونه خالی باشه.", true); return; }
    try {
      const res = await api("POST", `/api/subs/${sub.id}/configs/${idx}/rename`, { name });
      openModal(`
        <h2>✅ کانفیگ رنیم‌شده</h2>
        <textarea id="renamed-text" rows="5" readonly>${esc(res.renamed)}</textarea>
        <div class="modal-actions">
          <button class="btn-outline btn" id="close-btn">بستن</button>
          <button class="btn" id="copy-btn">کپی</button>
        </div>
      `);
      document.getElementById("close-btn").addEventListener("click", closeModal);
      document.getElementById("copy-btn").addEventListener("click", () => copyText(res.renamed));
    } catch (e) { toast(e.message, true); }
  });
}

// ---------- سبد انتخاب (برای ساخت اشتراک سفارشی) ----------

function toggleCart(sub, idx, checked) {
  const cfg = sub.configs.find((c) => c.index === idx);
  if (checked) {
    state.cart.push({ sub_id: sub.id, sub_name: sub.name, index: idx, remark: cfg.remark, protocol: cfg.protocol, name: "" });
  } else {
    state.cart = state.cart.filter((it) => !(it.sub_id === sub.id && it.index === idx));
  }
  render();
}

function renderCartBar() {
  if (state.cart.length === 0) return '<div class="cart-bar"></div>';
  return `
    <div class="cart-bar show">
      <span>🛒 ${state.cart.length} کانفیگ انتخاب شده</span>
      <button class="btn-sm btn" id="cart-clear-btn">پاک کردن</button>
      <button class="btn" id="cart-build-btn">🛠 ساخت اشتراک سفارشی</button>
    </div>
  `;
}

function bindCartBarEvents() {
  const clearBtn = document.getElementById("cart-clear-btn");
  if (clearBtn) clearBtn.addEventListener("click", () => {
    state.cart = [];
    render();
  });
  const buildBtn = document.getElementById("cart-build-btn");
  if (buildBtn) buildBtn.addEventListener("click", openBuildModal);
}

function openBuildModal() {
  const itemsHtml = state.cart.map((it, i) => `
    <div class="card-row">
      <div>
        <div style="font-size:.85rem">${esc(it.remark || "(بدون نام)")}</div>
        <div class="muted" style="font-size:.72rem">از: ${esc(it.sub_name)}</div>
      </div>
      <input type="text" class="cart-name" data-i="${i}" placeholder="اسم دلخواه (اختیاری)" style="max-width:160px"/>
    </div>
  `).join("");

  openModal(`
    <h2>🛠 ساخت اشتراک سفارشی</h2>
    <label>اسم اشتراک</label>
    <input type="text" id="build-name" placeholder="مثلا: انتخابی من - گیمینگ"/>
    <label>مدت اعتبار</label>
    <select id="build-expiry" style="width:100%;background:var(--bg);border:1px solid var(--border);color:var(--text);padding:10px;border-radius:8px">
      <option value="0">بدون محدودیت</option>
      <option value="7">۷ روز</option>
      <option value="30">۳۰ روز</option>
      <option value="90">۹۰ روز</option>
      <option value="180">۱۸۰ روز</option>
    </select>
    <label>کانفیگ‌های انتخابی (اسم دلخواه اختیاریه)</label>
    <div style="max-height:220px;overflow-y:auto">${itemsHtml}</div>
    <div class="modal-actions">
      <button class="btn-outline btn" id="cancel-btn">انصراف</button>
      <button class="btn" id="submit-btn">بساز</button>
    </div>
  `);
  document.getElementById("cancel-btn").addEventListener("click", closeModal);
  document.getElementById("submit-btn").addEventListener("click", async () => {
    const name = document.getElementById("build-name").value.trim();
    if (!name) { toast("اسم اجباریه.", true); return; }
    const expiry_days = parseInt(document.getElementById("build-expiry").value);
    document.querySelectorAll(".cart-name").forEach((inp) => {
      state.cart[parseInt(inp.dataset.i)].name = inp.value.trim();
    });
    const items = state.cart.map((it) => ({ sub_id: it.sub_id, index: it.index, name: it.name || undefined }));

    const btn = document.getElementById("submit-btn");
    btn.disabled = true; btn.textContent = "در حال ساخت...";
    try {
      const gen = await api("POST", "/api/build-custom", { name, expiry_days, items });
      state.cart = [];
      closeModal();
      openModal(`
        <h2>✅ اشتراک سفارشی ساخته شد</h2>
        <p class="muted">${gen.config_count} کانفیگ</p>
        <label>لینک اشتراک</label>
        <code class="url">${esc(gen.url)}</code>
        <div class="modal-actions">
          <button class="btn-outline btn" id="close-btn">بستن</button>
          <button class="btn" id="copy-btn">کپی لینک</button>
        </div>
      `);
      document.getElementById("close-btn").addEventListener("click", () => { closeModal(); render(); });
      document.getElementById("copy-btn").addEventListener("click", () => copyText(gen.url));
    } catch (e) {
      toast(e.message, true);
      btn.disabled = false; btn.textContent = "بساز";
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
  if (!state.generated.length) {
    return '<div class="empty-state">هنوز اشتراک سفارشی نساختی. از داخل یه اشتراک، چندتا کانفیگ تیک بزن و «ساخت اشتراک سفارشی» رو بزن.</div>';
  }
  const items = state.generated.map((g) => `
    <div class="list-item" data-open-gen="${g.id}">
      <div>
        <div class="title">${esc(g.name)} ${g.expired ? '<span class="badge badge-expired">منقضی</span>' : ""}</div>
        <div class="subtitle">${g.config_count} کانفیگ · ساخته‌شده ${fmtDate(g.created_at)}</div>
      </div>
      <span>›</span>
    </div>
  `).join("");

  setTimeout(() => {
    app.querySelectorAll("[data-open-gen]").forEach((el) => {
      el.addEventListener("click", () => openGenerated(parseInt(el.dataset.openGen)));
    });
  });

  return items;
}

async function openGenerated(id) {
  try {
    state.currentGen = await api("GET", `/api/generated/${id}`);
    state.view = "gen-detail";
    render();
  } catch (e) { toast(e.message, true); }
}

function renderGenDetail(gen) {
  const rows = gen.configs.map((c) => `
    <div class="config-row">
      <span class="badge">${esc(c.protocol)}</span>
      <span class="remark">${esc(c.remark || "(بدون نام)")}</span>
    </div>
  `).join("");

  setTimeout(() => {
    document.getElementById("back-to-gens").addEventListener("click", () => {
      state.view = "list"; state.currentGen = null; render();
    });
    document.getElementById("copy-gen-url").addEventListener("click", () => copyText(gen.url));
    document.getElementById("delete-gen-btn").addEventListener("click", () => confirmDeleteGen(gen));
  }, 0);

  const expLabel = gen.expires_at
    ? (gen.expired ? `منقضی‌شده (${fmtDate(gen.expires_at)})` : fmtDate(gen.expires_at))
    : "بدون محدودیت";

  return `
    <div class="back-link" id="back-to-gens">« بازگشت به اشتراک‌های سفارشی</div>
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:10px">
        <div>
          <div class="title" style="font-size:1.1rem;font-weight:700">${esc(gen.name)}</div>
          <div class="subtitle">${gen.config_count} کانفیگ · ساخته‌شده ${fmtDate(gen.created_at)} · انقضا: ${expLabel}</div>
        </div>
        <button class="btn-sm btn btn-danger" id="delete-gen-btn">🗑 حذف</button>
      </div>
      <label>لینک اشتراک</label>
      <code class="url">${esc(gen.url)}</code>
      <div style="margin-top:10px">
        <button class="btn-sm btn" id="copy-gen-url">کپی لینک</button>
      </div>
    </div>
    <div class="card">${rows}</div>
  `;
}

function confirmDeleteGen(gen) {
  openModal(`
    <h2>حذف اشتراک سفارشی</h2>
    <p class="muted">مطمئنی می‌خوای «${esc(gen.name)}» حذف بشه؟ لینکش دیگه کار نمی‌کنه.</p>
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
      toast("حذف شد ✓");
      state.view = "list";
      state.currentGen = null;
      await loadGenerated();
    } catch (e) { toast(e.message, true); }
  });
}

boot();

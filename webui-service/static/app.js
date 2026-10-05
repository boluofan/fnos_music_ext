/* fnmusic-ext WebUI — 原生 JS，无框架无外部资产。 */
"use strict";

// 飞牛桌面用 HTTPS 打开管理窗，页面必须挂在同源路径 /app/fnmusic-ext 下。
// WebUI 自己也会剥掉这个前缀。管理接口只认飞牛网关注入的管理员头。
const APP_BASE = "/app/fnmusic-ext";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const PROVIDER_LABEL = { musicdl: "musicdl 聚合音源", musicbox: "网易云音乐盒子", lxmusic: "洛雪自定义源", none: "未配置" };
const PROC_LABEL = { musicdl: "musicdl", musicbox: "musicbox", lxmusic: "lxmusic", webui: "WebUI" };

let configValues = {};   // GET /api/config 的 values
let platforms = { enabled: [], registered: [] };
let dirty = false;
let qrTimer = null;
let lxVerifiedUrl = null; // 已通过测试的 lx URL（保存时免二次校验提示用）

async function api(path, options) {
  const resp = await fetch(APP_BASE + path, options);
  let body = {};
  try { body = await resp.json(); } catch (_) { /* 非 JSON */ }
  if (!resp.ok) throw new Error(body.error || body.detail || `HTTP ${resp.status}`);
  return body;
}

function toast(message, kind) {
  const el = $("#toast");
  el.textContent = message;
  el.className = "toast " + (kind || "");
  el.hidden = false;
  clearTimeout(el._t);
  el._t = setTimeout(() => { el.hidden = true; }, 3800);
}

function markDirty(note) {
  dirty = true;
  const bar = $("#save-bar");
  if (bar) bar.classList.add("dirty");
  const noteEl = $("#save-note");
  if (noteEl) noteEl.textContent = note || "⚠️ 有未保存的修改";
}

function clearDirty() {
  dirty = false;
  const bar = $("#save-bar");
  if (bar) bar.classList.remove("dirty");
  const noteEl = $("#save-note");
  if (noteEl) noteEl.textContent = "已保存，配置已生效";
}

/* -------------------------------------------------------------- 导航 */
function switchPage(page) {
  $$(".page").forEach((el) => el.classList.toggle("active", el.id === "page-" + page));
  $$("[data-page]").forEach((el) => el.classList.toggle("active", el.dataset.page === page));
}
$$("[data-page]").forEach((btn) => btn.addEventListener("click", () => switchPage(btn.dataset.page)));

/* -------------------------------------------------------------- 概览 */
async function loadStatus() {
  try {
    const st = await api("/api/status");
    $("#brand-version").textContent = `v${st.version}`;
    $("#sidebar-foot").textContent = `${PROVIDER_LABEL[st.current_provider] || st.current_provider}`;
    $("#ov-provider-body").innerHTML =
      `<span class="state-line"><span class="dot ok"></span>${PROVIDER_LABEL[st.current_provider] || st.current_provider}</span>`;
    $("#ov-processes").innerHTML = Object.entries(st.processes).map(([name, p]) => {
      const ok = p.state === "RUNNING";
      const cls = ok ? "ok" : p.state === "FATAL" ? "err" : "";
      return `<span class="chip"><span class="dot ${cls}"></span>${PROC_LABEL[name] || name} · ${p.state}</span>`;
    }).join("");
    $("#ov-services").innerHTML = Object.entries(st.services).map(([name, s]) => {
      if (!s.reachable && s.note) return `<span class="state-line"><span class="dot"></span>${PROC_LABEL[name]}：${s.note}</span>`;
      return `<span class="state-line"><span class="dot ${s.reachable ? "ok" : "err"}"></span>${PROC_LABEL[name]}：${s.reachable ? "正常" : "不可达"}</span>`;
    }).join("");
    const lxCard = $("#ov-lx-card");
    if (st.current_provider === "lxmusic" && st.lx_source) {
      lxCard.hidden = false;
      const s = st.lx_source.source;
      const rows = [];
      rows.push(`<div class="kv"><b>状态</b>${st.lx_source.initialized ? "已加载" : "未加载"}</div>`);
      if (s) {
        rows.push(`<div class="kv"><b>源名称</b>${s.name || "-"} ${s.version ? "v" + s.version : ""}</div>`);
        rows.push(`<div class="kv"><b>平台</b>${Object.keys(s.platforms || {}).join("、") || "-"}</div>`);
        rows.push(`<div class="kv"><b>运行</b>${s.running ? "是" : "否"}</div>`);
      }
      if (st.lx_source.last_error) rows.push(`<div class="kv"><b>错误</b>${st.lx_source.last_error}</div>`);
      $("#ov-lx").innerHTML = rows.join("");
    } else {
      lxCard.hidden = true;
    }
  } catch (exc) {
    $("#ov-provider-body").textContent = "状态加载失败：" + exc.message;
  }
}

/* -------------------------------------------------------------- 配置读写 */
async function loadConfig() {
  const cfg = await api("/api/config");
  configValues = cfg.values;
  applyConfigToForm();
  clearDirty();
}

function applyConfigToForm() {
  const v = configValues;
  const provider = v.FNMUSIC_NETEASE_ENABLED === "true" ? "musicbox"
    : v.FNMUSIC_MUSICDL_ENABLED === "true" ? "musicdl"
    : v.FNMUSIC_LX_ENABLED === "true" ? "lxmusic" : "";
  $$("input[name=provider]").forEach((el) => { el.checked = el.value === provider; });
  syncProviderPanels(provider);
  if (provider === "musicbox") syncNeteaseAccount();
  const quality = v.FNMUSIC_QUALITY_MODE || "high";
  $$("input[name=quality]").forEach((el) => { el.checked = el.value === quality; });
  $("#recommend-hot").checked = v.FNMUSIC_RECOMMEND_HOT === "true";
  $("#recommend-daily").checked = v.FNMUSIC_RECOMMEND_DAILY === "true";
  $("#tee-enabled").checked = v.FNMUSIC_TEE_SAVE_ENABLED === "true";
  $("#auto-cover").checked = v.FNMUSIC_AUTO_COVER !== "false";
  $("#lyric-auto-dl").checked = v.FNMUSIC_LYRIC_AUTO_DL !== "false";
  $("#fav-autobind").checked = v.FNMUSIC_FAV_AUTO_BIND === "true";
  if ($("#filename-format")) $("#filename-format").value = v.FNMUSIC_FILENAME_FORMAT || "title-artist";
  $("#tee-dir").value = v.FNMUSIC_TEE_SAVE_DIR || "";
  $("#tee-max").value = v.FNMUSIC_TEE_CACHE_MAX || "2";
  $("#bind-timeout").value = v.FNMUSIC_OFFICIAL_BIND_TIMEOUT_S || "120";
  $("#handoff-max").value = v.FNMUSIC_TEE_HANDOFF_MAX != null ? v.FNMUSIC_TEE_HANDOFF_MAX : "3";
  $("#scan-path").value = v.FNMUSIC_LIBRARY_SCAN_PATH || "";
  updateTeeCountLabel();
  updateBindTimeoutLabel();
  $("#llm-base").value = v.FNMUSIC_LLM_BASE_URL || "";
  $("#llm-key").value = v.FNMUSIC_LLM_API_KEY || "";
  $("#llm-model").value = v.FNMUSIC_LLM_MODEL || "";
  $("#search-timeout").value = v.FNMUSIC_SEARCH_TIMEOUT || "15";
  $("#search-probe").checked = v.FNMUSIC_SEARCH_PROBE === "true";
  $("#netease-my-playlists").checked = v.FNMUSIC_NETEASE_MY_PLAYLISTS === "true";
  $("#lx-url").value = v.LX_SOURCE_URL || "";
  $("#lx-url-2").value = v.LX_SOURCE_URL_2 || "";
  $("#lx-url-3").value = v.LX_SOURCE_URL_3 || "";
  lxVerifiedUrl = v.LX_SOURCE_URL || null;
  renderPlatformChips();
}

function collectConfig() {
  const provider = ($$("input[name=provider]").find((el) => el.checked) || {}).value || "";
  const values = {
    FNMUSIC_MUSICDL_ENABLED: provider === "musicdl",
    FNMUSIC_NETEASE_ENABLED: provider === "musicbox",
    FNMUSIC_LX_ENABLED: provider === "lxmusic",
    FNMUSIC_QUALITY_MODE: ($$("input[name=quality]").find((el) => el.checked) || {}).value || "high",
    FNMUSIC_RECOMMEND_HOT: $("#recommend-hot").checked,
    FNMUSIC_RECOMMEND_DAILY: $("#recommend-daily").checked,
    FNMUSIC_TEE_SAVE_ENABLED: $("#tee-enabled").checked,
    FNMUSIC_AUTO_COVER: $("#auto-cover").checked,
    FNMUSIC_LYRIC_AUTO_DL: $("#lyric-auto-dl").checked,
    FNMUSIC_FAV_AUTO_BIND: $("#fav-autobind").checked,
    FNMUSIC_FILENAME_FORMAT: ($("#filename-format") ? $("#filename-format").value : "title-artist") || "title-artist",
    FNMUSIC_TEE_SAVE_DIR: $("#tee-dir").value.trim(),
    FNMUSIC_TEE_CACHE_MAX: parseInt($("#tee-max").value || "2", 10),
    FNMUSIC_OFFICIAL_BIND_TIMEOUT_S: parseInt($("#bind-timeout").value || "120", 10) || 120,
    FNMUSIC_TEE_HANDOFF_MAX: parseInt($("#handoff-max").value || "3", 10) || 0,
    FNMUSIC_LIBRARY_SCAN_PATH: $("#scan-path").value.trim(),
    FNMUSIC_LLM_BASE_URL: $("#llm-base").value.trim(),
    FNMUSIC_LLM_API_KEY: $("#llm-key").value.trim(),
    FNMUSIC_LLM_MODEL: $("#llm-model").value.trim(),
    FNMUSIC_SEARCH_TIMEOUT: parseInt($("#search-timeout").value || "15", 10) || 15,
    FNMUSIC_SEARCH_PROBE: $("#search-probe").checked,
    FNMUSIC_NETEASE_MY_PLAYLISTS: $("#netease-my-playlists").checked,
  };
  if ($("#charts-master-switch")) {
    values.FNMUSIC_RECOMMEND_CHARTS = $("#charts-master-switch").checked;
    const allChartInputs = $$("#kg-charts-grid input, #wy-charts-grid input");
    const checkedInputs = allChartInputs.filter((el) => el.checked);
    if (checkedInputs.length === allChartInputs.length) {
      values.FNMUSIC_ENABLED_CHARTS = "";
    } else if (checkedInputs.length === 0) {
      values.FNMUSIC_ENABLED_CHARTS = "none";
    } else {
      values.FNMUSIC_ENABLED_CHARTS = checkedInputs.map((el) => el.closest(".chart-item").dataset.id).join(",");
    }
  }
  if (provider === "musicdl") {
    values.FNMUSIC_ONLINE_SOURCES = platforms.enabled.join(",");
    values.MUSICDL_SOURCES = platforms.enabled.join(",");
  }
  if (provider === "lxmusic") {
    const url = $("#lx-url").value.trim();
    const url2 = $("#lx-url-2").value.trim();
    const url3 = $("#lx-url-3").value.trim();
    if (!url && !url2 && !url3) throw new Error("洛雪源至少需要填写一个脚本 URL（主音源）");
    values.LX_SOURCE_URL = url;
    values.LX_SOURCE_URL_2 = url2;
    values.LX_SOURCE_URL_3 = url3;
  }
  return values;
}

async function saveConfig() {
  let values;
  try { values = collectConfig(); } catch (exc) { toast(exc.message, "fail"); return; }
  const btn = $("#save-btn");
  btn.disabled = true;
  btn.textContent = "保存中…";
  try {
    const result = await api("/api/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ values }),
    });
    const failed = (result.actions || []).filter((a) => !a.ok);
    const parts = [];
    if (result.changed && result.changed.length) parts.push(`已保存 ${result.changed.length} 项`);
    (result.actions || []).forEach((a) => {
      if (a.kind === "process") parts.push(`进程 ${a.program} ${a.op} ${a.ok ? "成功" : "失败"}`);
      if (a.kind === "lx_activate") parts.push(`洛雪源激活${a.ok ? "成功" : "失败"}`);
    });
    if (failed.length) {
      toast((parts.join("；") || "") + ` —— ${failed.map((f) => f.error).join("；")}`, "fail");
    } else {
      toast(parts.join("；") || "配置无变化", "ok");
    }
    clearDirty();
    await loadConfig();
    await loadStatus();
  } catch (exc) {
    toast("保存失败：" + exc.message, "fail");
  } finally {
    btn.disabled = false;
    btn.textContent = "保存并生效";
  }
}
$("#save-btn").addEventListener("click", saveConfig);

/* -------------------------------------------------------------- 音源选择 */
function savedProvider() {
  const v = configValues;
  return v.FNMUSIC_NETEASE_ENABLED === "true" ? "musicbox"
    : v.FNMUSIC_MUSICDL_ENABLED === "true" ? "musicdl"
    : v.FNMUSIC_LX_ENABLED === "true" ? "lxmusic" : "";
}

// 点选未启用的音源：临时拉起其进程供预览（不写配置；5 分钟内未保存自动停止）
async function startPreview(provider) {
  try {
    const r = await api("/api/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider }),
    });
    if (r.preview) toast(`已临时启动 ${PROVIDER_LABEL[provider]}（预览）：5 分钟内未保存将自动停止`);
    return true;
  } catch (exc) {
    toast(`临时启动 ${PROVIDER_LABEL[provider]} 失败：${exc.message}`, "fail");
    return false;
  }
}

function syncProviderPanels(provider) {
  $$(".provider-card").forEach((el) => el.classList.toggle("selected", el.dataset.provider === provider));
  $("#panel-musicbox").hidden = provider !== "musicbox";
  $("#panel-musicdl").hidden = provider !== "musicdl";
  $("#panel-lxmusic").hidden = provider !== "lxmusic";
}
$$("input[name=provider]").forEach((el) =>
  el.addEventListener("change", async () => {
    syncProviderPanels(el.value);
    if (el.value === "musicbox") syncNeteaseAccount();
    markDirty("音源切换需保存后生效");
    if (el.value && el.value !== savedProvider() && await startPreview(el.value)) {
      if (el.value === "musicdl") await loadPlatforms(false);
    }
  }));

/* -------------------------------------------------------------- musicdl 平台 */
function setPlatformFallback() {
  platforms = { enabled: (configValues.FNMUSIC_ONLINE_SOURCES || "").split(",").filter(Boolean), registered: [] };
  $("#platform-note").textContent = "musicdl 进程未运行，暂无法获取平台列表（点选 musicdl 音源可临时启动预览）";
  renderPlatformChips();
}

async function fetchPlatforms() {
  const data = await api("/api/platforms");
  platforms = { enabled: data.enabled || [], registered: data.registered || [] };
  $("#platform-note").textContent = `共 ${platforms.registered.length} 个注册平台，已启用 ${platforms.enabled.length} 个`;
  renderPlatformChips();
}

async function loadPlatforms(autoPreview = true) {
  try {
    await fetchPlatforms();
  } catch (_) {
    // 进程未运行：autoPreview（用户点选/刷新触发）时临时拉起后重试；页面加载不自动拉起
    if (!autoPreview) { setPlatformFallback(); return; }
    try {
      if (await startPreview("musicdl")) await fetchPlatforms();
      else setPlatformFallback();
    } catch (_) {
      setPlatformFallback();
    }
  }
}

function renderPlatformChips() {
  const keyword = $("#platform-search").value.trim().toLowerCase();
  const container = $("#platform-list");
  const enabledSet = new Set(platforms.enabled);
  const items = platforms.registered.filter((p) => !keyword || p.toLowerCase().includes(keyword));
  container.innerHTML = items.length
    ? items.map((p) => `<span class="chip ${enabledSet.has(p) ? "on" : ""}" data-platform="${p}">${p}</span>`).join("")
    : `<span class="muted">无匹配平台</span>`;
  container.querySelectorAll(".chip[data-platform]").forEach((chip) =>
    chip.addEventListener("click", () => {
      const p = chip.dataset.platform;
      const set = new Set(platforms.enabled);
      if (set.has(p)) { set.delete(p); chip.classList.remove("on"); }
      else { set.add(p); chip.classList.add("on"); }
      platforms.enabled = Array.from(set);
      markDirty("musicdl 平台已修改");
    }));
}
$("#platform-search").addEventListener("input", renderPlatformChips);
$("#platform-reload").addEventListener("click", loadPlatforms);

/* -------------------------------------------------------------- 网易扫码 */
async function syncNeteaseAccount(retries = 0) {
  // 把网易账号态同步到 #qr-check（已登录显示昵称；未登录/失败清空）
  for (let i = 0; ; i++) {
    try {
      const st = await api("/api/netease/auth/status");
      const d = st.data || st;
      if (d.logged_in) {
        $("#qr-check").textContent = `当前登录：${d.nickname || d.user_id || "已登录用户"}`;
        return;
      }
    } catch (_) { /* status 不可达视为未登录 */ }
    if (i >= retries) { $("#qr-check").textContent = ""; return; }
    await new Promise((r) => setTimeout(r, 1500));
  }
}

async function startQrLogin() {
  stopQrPolling();
  $("#qr-status").textContent = "正在生成二维码…";
  $("#qr-img").hidden = true;
  syncNeteaseAccount(); // 生成前同步当前账号态（非致命，不阻塞出码）
  const callLogin = () => api("/api/netease/auth/login", { method: "POST" });
  try {
    let data;
    try {
      data = await callLogin();
    } catch (exc) {
      // musicbox 进程未运行（未点选/预览过期）：临时拉起后重试一次
      if (!await startPreview("musicbox")) throw exc;
      data = await callLogin();
    }
    const unikey = data.unikey || data.codekey || (data.data && (data.data.unikey || data.data.codekey)) || "";
    if (!unikey) throw new Error("未获取到 unikey");
    $("#qr-img").src = `${APP_BASE}/api/netease/qr?unikey=${encodeURIComponent(unikey)}`;
    $("#qr-img").hidden = false;
    $("#qr-status").textContent = "请用手机网易云音乐 App 扫码";
    pollQr(unikey);
  } catch (exc) {
    $("#qr-status").textContent = "生成失败：" + exc.message;
  }
}

async function checkQrStatus(unikey) {
  const st = await api(`/api/netease/auth/login/check?unikey=${encodeURIComponent(unikey)}`);
  // musicbox CLI 返回 {ok, data:{code}} 信封结构；兼容扁平 {code}
  const code = st?.data?.code ?? st?.code;
  if (code === 803) {
    stopQrPolling();
    $("#qr-status").textContent = "登录成功 ✓";
    syncNeteaseAccount(2); // 803 后 cookie 落盘需要一点时间，带重试取昵称
    toast("网易账号登录成功", "ok");
  } else if (code === 802) {
    $("#qr-status").textContent = "已扫码，请在手机上确认";
  } else if (code === 800) {
    stopQrPolling();
    $("#qr-status").textContent = "二维码已过期，请重新生成";
  } else {
    $("#qr-status").textContent = "等待扫码…";
  }
}

function pollQr(unikey, intervalMs = 2000) {
  stopQrPolling();
  qrTimer = setInterval(() => { checkQrStatus(unikey).catch(() => {}); }, intervalMs);
}

function stopQrPolling() {
  if (qrTimer) { clearInterval(qrTimer); qrTimer = null; }
}
$("#qr-btn").addEventListener("click", startQrLogin);

/* -------------------------------------------------- lx 源：文件上传 / NAS 选择 */
async function lxUploadScript(filename, script) {
  // 先确保 lxmusic 进程可用（预览拉起），再转发落盘
  const callUpload = () => api("/api/lx/upload", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename, script }),
  });
  try {
    return await callUpload();
  } catch (exc) {
    if (!await startPreview("lxmusic")) throw exc;
    return await callUpload();
  }
}

let lxTrimSdk = null;
async function lxLoadTrimSdk() {
  if (lxTrimSdk !== null) return lxTrimSdk;
  try {
    const mod = await import("/app/fnmusic-ext/static/vendor/trim-web-app.js");
    lxTrimSdk = new mod.TrimApp();
  } catch (_) {
    lxTrimSdk = false;
  }
  return lxTrimSdk;
}

/* -------------------------------------------------------------- 3 槽位配置绑定 */
function setupLxSlot(slotIndex, inputSel, testBtnSel, uploadBtnSel, fileSel, pickBtnSel, reportSel, noteSel, slotLabel) {
  const inputEl = $(inputSel);
  const testBtn = $(testBtnSel);
  const uploadBtn = $(uploadBtnSel);
  const fileEl = $(fileSel);
  const pickBtn = $(pickBtnSel);
  const reportBox = $(reportSel);
  const noteEl = $(noteSel);

  if (!inputEl || !testBtn) return;

  // 测试按钮
  testBtn.addEventListener("click", async () => {
    const url = inputEl.value.trim();
    if (!url) { toast(`请先填写${slotLabel} URL`, "fail"); return; }
    reportBox.hidden = false;
    reportBox.className = "report";
    reportBox.textContent = `测试中（下载脚本 → 沙箱初始化 → 多首抽样搜索/解析/探活）…`;
    const callVerify = () => api("/api/lx/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ values: { url } }),
    });
    try {
      let r;
      try {
        r = await callVerify();
      } catch (exc) {
        if (!await startPreview("lxmusic")) throw exc;
        r = await callVerify();
      }
      if (r.ok) {
        const d = r.data || {};
        const meta = d.meta || {};
        const probe = d.probe || {};
        reportBox.className = "report ok";
        reportBox.innerHTML =
          `<div class="kv"><b>源名称</b>${meta.name || "-"} ${meta.version ? "v" + meta.version : ""}（${meta.author || "未知作者"}）</div>` +
          `<div class="kv"><b>可用平台</b>${(d.platforms || []).join("、")}</div>` +
          (probe.title ? `<div class="kv"><b>实测</b>${probe.title} - ${probe.artist} [${probe.platform}/${probe.quality}] ${probe.content_type || ""}</div>` : "") +
          `<div class="kv"><b>结论</b>可用 ✓（保存后生效）</div>`;
      } else {
        const d = r.data || {};
        reportBox.className = "report fail";
        reportBox.innerHTML = `<div class="kv"><b>不可用</b>${d.message || r.error || "校验失败"}</div>`;
      }
    } catch (exc) {
      reportBox.className = "report fail";
      reportBox.textContent = "测试失败：" + exc.message;
    }
  });

  // 上传按钮
  if (uploadBtn && fileEl) {
    uploadBtn.addEventListener("click", () => fileEl.click());
    fileEl.addEventListener("change", async () => {
      const file = fileEl.files && fileEl.files[0];
      if (!file) return;
      if (!file.name.toLowerCase().endsWith(".js")) { toast("只支持 .js 后缀文件", "fail"); return; }
      if (file.size > 9_000_000) { toast("脚本超过 9MB 上限", "fail"); return; }
      if (noteEl) noteEl.textContent = "读取并上传中…";
      try {
        const script = await file.text();
        const r = await lxUploadScript(file.name, script);
        const d = r.data || {};
        inputEl.value = d.url || "";
        if (noteEl) noteEl.textContent = d.meta && d.meta.name ? `已上传：${d.meta.name}` : "已上传";
        markDirty(`${slotLabel}已更新为上传脚本，测试后保存生效`);
        toast("脚本已上传，请点「测试」验证后保存", "ok");
      } catch (exc) {
        if (noteEl) noteEl.textContent = "";
        toast("上传失败：" + exc.message, "fail");
      } finally {
        fileEl.value = "";
      }
    });
  }

  // NAS 文件选择
  if (pickBtn) {
    pickBtn.addEventListener("click", async () => {
      const sdk = await lxLoadTrimSdk();
      if (!sdk) { toast("当前环境不支持 NAS 文件选择（直连 8774 时请用上传或 URL）", "fail"); return; }
      try {
        const result = await sdk.pickUserFile({
          directory: false,
          accept: [".js"],
          title: `选择${slotLabel}脚本`,
          okText: "选择",
          sidebarGroup: ["myFiles", "otherShare", "favorites"],
        });
        const paths = (result && result.data) || [];
        if (!paths.length) return;
        const hostPath = paths[0];
        if (noteEl) noteEl.textContent = "读取 NAS 文件中…";
        const resp = await fetch(APP_BASE + "/api/host-file", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path: hostPath }),
        });
        let body = {};
        try { body = await resp.json(); } catch (_) {}
        if (!resp.ok) throw new Error(body.error || body.detail || `HTTP ${resp.status}`);
        const filename = hostPath.split("/").pop() || "source.js";
        const r = await lxUploadScript(filename, body.script || "");
        const d = r.data || {};
        inputEl.value = d.url || "";
        if (noteEl) noteEl.textContent = d.meta && d.meta.name ? `已上传：${d.meta.name}` : "已上传";
        markDirty(`${slotLabel}已更新为上传脚本，测试后保存生效`);
        toast("脚本已上传，请点「测试」验证后保存", "ok");
      } catch (exc) {
        if (noteEl) noteEl.textContent = "";
        toast("NAS 选择失败：" + exc.message, "fail");
      }
    });
  }
}

setupLxSlot(0, "#lx-url", "#lx-test", "#lx-upload", "#lx-file", "#lx-pick", "#lx-report", "#lx-upload-note", "主音源");
setupLxSlot(1, "#lx-url-2", "#lx-test-2", "#lx-upload-2", "#lx-file-2", "#lx-pick-2", "#lx-report-2", "#lx-upload-note-2", "备用音源 1");
setupLxSlot(2, "#lx-url-3", "#lx-test-3", "#lx-upload-3", "#lx-file-3", "#lx-pick-3", "#lx-report-3", "#lx-upload-note-3", "备用音源 2");

(async function detectNasPicker() {
  const pathname = (typeof window !== "undefined" && window.location && window.location.pathname) || "";
  if (pathname.startsWith("/app/")) {
    const sdk = await lxLoadTrimSdk();
    if (sdk) {
      if ($("#lx-pick")) $("#lx-pick").hidden = false;
      if ($("#lx-pick-2")) $("#lx-pick-2").hidden = false;
      if ($("#lx-pick-3")) $("#lx-pick-3").hidden = false;
    }
  }
})();

/* -------------------------------------------------------------- 表单脏标记 */
["#tee-dir", "#tee-max", "#llm-base", "#llm-key", "#llm-model", "#lx-url", "#lx-url-2", "#lx-url-3", "#search-timeout", "#bind-timeout", "#handoff-max", "#scan-path"].forEach((sel) => {
  const el = $(sel);
  if (el) el.addEventListener("input", () => markDirty());
});
$$("input[name=quality]").forEach((el) => el.addEventListener("change", () => markDirty("音质偏好需保存后生效")));
["#recommend-hot", "#recommend-daily", "#search-probe", "#tee-enabled", "#fav-autobind", "#auto-cover", "#lyric-auto-dl", "#netease-my-playlists", "#filename-format"].forEach((sel) => {
  const el = $(sel);
  if (el) el.addEventListener("change", () => markDirty());
});

function updateTeeCountLabel() {
  const n = $("#tee-max").value || configValues.FNMUSIC_TEE_CACHE_MAX || "2";
  $("#tee-count-label").textContent = `（缓存数 ${n} 首）`;
}
$("#tee-max").addEventListener("input", updateTeeCountLabel);

function updateBindTimeoutLabel() {
  const n = $("#bind-timeout").value || configValues.FNMUSIC_OFFICIAL_BIND_TIMEOUT_S || "120";
  $("#bind-timeout-label").textContent = `（${n} 秒）`;
}
$("#bind-timeout").addEventListener("input", updateBindTimeoutLabel);

window.addEventListener("beforeunload", (ev) => {
  if (dirty) ev.preventDefault();
});

/* -------------------------------------------------------------- 榜单管理 */
const FALLBACK_KG_CHARTS = [
  { id: "kg_8888", name: "TOP500", source: "kg", cover: "https://imge.kugou.com/mcommon/400/20201208/20201208143249767677.png" },
  { id: "kg_52144", name: "国潮音乐榜", source: "kg", cover: "https://imge.kugou.com/mcommon/400/20210929/20210929175404555896.png" },
  { id: "kg_52767", name: "视频号热歌酷狗榜", source: "kg", cover: "https://imge.kugou.com/mcommon/400/20220112/20220112105954605929.png" },
  { id: "kg_31313", name: "民谣榜", source: "kg", cover: "https://imge.kugou.com/mcommon/400/20201208/20201208143615367389.png" },
  { id: "kg_33161", name: "纯音乐榜", source: "kg", cover: "https://imge.kugou.com/mcommon/400/20201208/20201208143627918342.png" },
  { id: "kg_33162", name: "电音榜", source: "kg", cover: "https://imge.kugou.com/mcommon/400/20201208/20201208143639943265.png" },
  { id: "kg_23784", name: "网络热歌榜", source: "kg", cover: "https://imge.kugou.com/mcommon/400/20201208/20201208143303648439.png" },
  { id: "kg_6666", name: "飙升榜", source: "kg", cover: "https://imge.kugou.com/mcommon/400/20201208/20201208143314995483.png" },
  { id: "kg_52055", name: "短视频热歌榜", source: "kg", cover: "https://imge.kugou.com/mcommon/400/20210929/20210929175440620612.png" },
  { id: "kg_46908", name: "摇滚榜", source: "kg", cover: "https://imge.kugou.com/mcommon/400/20201208/20201208143715876542.png" },
  { id: "kg_24971", name: "DJ热歌榜", source: "kg", cover: "https://imge.kugou.com/mcommon/400/20201208/20201208143727938472.png" },
  { id: "kg_54884", name: "国乐榜", source: "kg", cover: "https://imge.kugou.com/mcommon/400/20220926/20220926183354316719.png" },
  { id: "kg_52054", name: "百万收藏榜", source: "kg", cover: "https://imge.kugou.com/mcommon/400/20210929/20210929175422894563.png" },
  { id: "kg_59717", name: "短视频收藏人气榜", source: "kg", cover: "https://imge.kugou.com/mcommon/400/20230607/20230607172031123456.png" },
  { id: "kg_24306", name: "新歌榜", source: "kg", cover: "https://imge.kugou.com/mcommon/400/20201208/20201208143325674823.png" },
  { id: "kg_52895", name: "名品堂", source: "kg", cover: "https://imge.kugou.com/mcommon/400/20220223/20220223164928123456.png" },
  { id: "kg_31308", name: "内地榜", source: "kg", cover: "https://imge.kugou.com/mcommon/400/20201208/20201208143415123456.png" },
  { id: "kg_33163", name: "粤语金曲榜", source: "kg", cover: "https://imge.kugou.com/mcommon/400/20201208/20201208143438123456.png" },
  { id: "kg_31310", name: "欧美榜", source: "kg", cover: "https://imge.kugou.com/mcommon/400/20201208/20201208143501123456.png" },
  { id: "kg_30972", name: "伤感榜", source: "kg", cover: "https://imge.kugou.com/mcommon/400/20201208/20201208143524123456.png" },
];

const FALLBACK_WY_CHARTS = [
  { id: "wy_19723756", name: "飙升榜", source: "wy", cover: "https://p1.music.126.net/pcYHpMkdStnvXZTzkVa-TmA==/109951166952713766.jpg" },
  { id: "wy_3779629", name: "新歌榜", source: "wy", cover: "https://p1.music.126.net/wVmyNSOnn_jhOPkiagllMw==/109951166952686384.jpg" },
  { id: "wy_2884035", name: "原创榜", source: "wy", cover: "https://p1.music.126.net/iFZ_nw2VaeK2UzsDH-winQ==/109951166961388699.jpg" },
  { id: "wy_3778678", name: "热歌榜", source: "wy", cover: "https://p1.music.126.net/GhhuF6Ep5Tmanih7Gt6Q==/109951166952688017.jpg" },
  { id: "wy_71385702", name: "网易云古典榜", source: "wy", cover: "https://p1.music.126.net/vctb1OSyD64x80p0_R112A==/109951168172715456.jpg" },
  { id: "wy_1978921795", name: "网易云电音榜", source: "wy", cover: "https://p1.music.126.net/5405105260334812/109951168172728956.jpg" },
  { id: "wy_991319590", name: "网易云中文说唱榜", source: "wy", cover: "https://p1.music.126.net/3G2w7b_V0A27mY_bZ14A==/109951168172737890.jpg" },
  { id: "wy_5338990334", name: "实时分享榜", source: "wy", cover: "https://p1.music.126.net/J0m0vP0-gUa3H-B7n8M8vw==/109951168172745678.jpg" },
  { id: "wy_21845217", name: "网易云全球说唱榜", source: "wy", cover: "https://p1.music.126.net/j_z3P6Vz0K4vQ9L7e0U_5A==/109951168172754321.jpg" },
  { id: "wy_60198", name: "潮流风向榜", source: "wy", cover: "https://p1.music.126.net/G67_1K_8B7j7e5Q7E5y7A==/109951168172765432.jpg" },
  { id: "wy_5059632704", name: "音乐合伙人推荐榜", source: "wy", cover: "https://p1.music.126.net/U9X7Xj1x8b0fG-X2-V3tqQ==/109951168172778901.jpg" },
  { id: "wy_5059642708", name: "音乐合伙人热歌榜", source: "wy", cover: "https://p1.music.126.net/z8j7H6k5L4v3M2n1P0q9Rw==/109951168172789012.jpg" },
  { id: "wy_5059644681", name: "音乐合伙人留名榜", source: "wy", cover: "https://p1.music.126.net/K3n4M5v6L7k8P9q0R1s2Tw==/109951168172790123.jpg" },
  { id: "wy_5312894314", name: "音乐合伙人高分新歌榜", source: "wy", cover: "https://p1.music.126.net/P1q2R3s4T5u6V7w8X9y0Zw==/109951168172801234.jpg" },
  { id: "wy_5312895267", name: "音乐合伙人高分榜", source: "wy", cover: "https://p1.music.126.net/A1b2C3d4E5f6G7h8I9j0Kw==/109951168172812345.jpg" },
  { id: "wy_5453912201", name: "黑胶VIP爱听榜", source: "wy", cover: "https://p1.music.126.net/L1m2N3o4P5q6R7s8T9u0Vw==/109951168172823456.jpg" },
  { id: "wy_71384707", name: "网易云ACG榜", source: "wy", cover: "https://p1.music.126.net/W1x2Y3z4A5b6C7d8E9f0Gw==/109951168172834567.jpg" },
  { id: "wy_745956260", name: "网易云韩语榜", source: "wy", cover: "https://p1.music.126.net/H1i2J3k4L5m6N7o8P9q0Rw==/109951168172845678.jpg" },
];

let chartsData = { charts_enabled: true, kg: [], wy: [], total: 0 };

function applyFallbackCharts() {
  const enabledStr = (configValues.FNMUSIC_ENABLED_CHARTS || "").trim();
  const enabledSet = enabledStr ? new Set(enabledStr.split(",").map((s) => s.trim()).filter(Boolean)) : null;
  const isNone = enabledStr.toLowerCase() === "none";

  chartsData = {
    charts_enabled: configValues.FNMUSIC_RECOMMEND_CHARTS !== false,
    kg: FALLBACK_KG_CHARTS.map((c) => ({
      ...c,
      enabled: isNone ? false : (enabledSet === null || enabledSet.has(c.id)),
    })),
    wy: FALLBACK_WY_CHARTS.map((c) => ({
      ...c,
      enabled: isNone ? false : (enabledSet === null || enabledSet.has(c.id)),
    })),
    total: FALLBACK_KG_CHARTS.length + FALLBACK_WY_CHARTS.length,
  };
}

async function loadCharts() {
  try {
    const res = await api("/api/charts");
    if (res && res.ok && Array.isArray(res.kg) && res.kg.length > 0) {
      chartsData = res;
    } else {
      applyFallbackCharts();
    }
  } catch (exc) {
    console.warn("加载榜单列表失败，使用本地默认榜单:", exc);
    applyFallbackCharts();
  }
  renderChartsUI();
}

function updateSelectedCount() {
  const all = $$("#kg-charts-grid input, #wy-charts-grid input");
  const checked = all.filter((el) => el.checked).length;
  const countEl = $("#charts-selected-count");
  if (countEl) countEl.textContent = `已选 ${checked} / ${all.length}`;
}

function setModeBtnActive(btnId) {
  ["#btn-charts-select-all", "#btn-charts-deselect-all", "#btn-charts-select-kg", "#btn-charts-select-wy"].forEach((id) => {
    const b = $(id);
    if (b) b.classList.toggle("active", id === btnId);
  });
}

function updateModeButtons() {
  const kgInputs = $$("#kg-charts-grid input");
  const wyInputs = $$("#wy-charts-grid input");
  const allInputs = [...kgInputs, ...wyInputs];
  if (allInputs.length === 0) return;

  const checkedKg = kgInputs.filter((el) => el.checked).length;
  const checkedWy = wyInputs.filter((el) => el.checked).length;
  const totalChecked = checkedKg + checkedWy;

  if (totalChecked === allInputs.length) {
    setModeBtnActive("#btn-charts-select-all");
  } else if (totalChecked === 0) {
    setModeBtnActive("#btn-charts-deselect-all");
  } else if (checkedKg === kgInputs.length && checkedWy === 0) {
    setModeBtnActive("#btn-charts-select-kg");
  } else if (checkedWy === wyInputs.length && checkedKg === 0) {
    setModeBtnActive("#btn-charts-select-wy");
  } else {
    setModeBtnActive(null);
  }
}

function renderChartsUI() {
  const masterSwitch = $("#charts-master-switch");
  if (masterSwitch) {
    masterSwitch.checked = chartsData.charts_enabled !== false;
    masterSwitch.addEventListener("change", () => markDirty("榜单启用开关已修改"));
  }

  const renderGrid = (gridId, list) => {
    const el = $(gridId);
    if (!el) return;
    el.innerHTML = list.map((item) => {
      const checked = item.enabled ? "checked" : "";
      const selectedCls = item.enabled ? "selected" : "";
      return `
        <label class="chart-item ${selectedCls}" data-id="${item.id}">
          <input type="checkbox" class="chart-checkbox" ${checked}>
          <img class="chart-cover" src="${item.cover || ''}" loading="lazy" onerror="this.onerror=null;this.src='/app/fnmusic-ext/static/icon.png'">
          <div class="chart-info">
            <span class="chart-name" title="${item.name}">${item.name}</span>
            <span class="chart-src">${item.source === 'kg' ? '酷狗音乐' : '网易云音乐'}</span>
          </div>
          <span class="chart-check-icon">✓</span>
        </label>
      `;
    }).join("");

    el.querySelectorAll(".chart-item").forEach((label) => {
      const input = label.querySelector("input");
      input.addEventListener("change", () => {
        label.classList.toggle("selected", input.checked);
        updateSelectedCount();
        updateModeButtons();
        markDirty("榜单勾选已修改");
      });
    });
  };

  renderGrid("#kg-charts-grid", chartsData.kg || []);
  renderGrid("#wy-charts-grid", chartsData.wy || []);
  updateSelectedCount();
  updateModeButtons();

  $("#btn-charts-select-all")?.addEventListener("click", () => {
    $$("#kg-charts-grid input, #wy-charts-grid input").forEach((input) => {
      input.checked = true;
      input.closest(".chart-item")?.classList.add("selected");
    });
    setModeBtnActive("#btn-charts-select-all");
    updateSelectedCount();
    markDirty("榜单已全部启用");
  });

  $("#btn-charts-deselect-all")?.addEventListener("click", () => {
    $$("#kg-charts-grid input, #wy-charts-grid input").forEach((input) => {
      input.checked = false;
      input.closest(".chart-item")?.classList.remove("selected");
    });
    setModeBtnActive("#btn-charts-deselect-all");
    updateSelectedCount();
    markDirty("榜单已全部关闭");
  });

  $("#btn-charts-select-kg")?.addEventListener("click", () => {
    $$("#kg-charts-grid input").forEach((input) => {
      input.checked = true;
      input.closest(".chart-item")?.classList.add("selected");
    });
    $$("#wy-charts-grid input").forEach((input) => {
      input.checked = false;
      input.closest(".chart-item")?.classList.remove("selected");
    });
    setModeBtnActive("#btn-charts-select-kg");
    updateSelectedCount();
    markDirty("仅启用酷狗榜单");
  });

  $("#btn-charts-select-wy")?.addEventListener("click", () => {
    $$("#wy-charts-grid input").forEach((input) => {
      input.checked = true;
      input.closest(".chart-item")?.classList.add("selected");
    });
    $$("#kg-charts-grid input").forEach((input) => {
      input.checked = false;
      input.closest(".chart-item")?.classList.remove("selected");
    });
    setModeBtnActive("#btn-charts-select-wy");
    updateSelectedCount();
    markDirty("仅启用网易云榜单");
  });

  $("#btn-kg-all")?.addEventListener("click", () => {
    $$("#kg-charts-grid input").forEach((input) => {
      input.checked = true;
      input.closest(".chart-item")?.classList.add("selected");
    });
    updateSelectedCount();
    updateModeButtons();
    markDirty("已全选酷狗榜单");
  });
  $("#btn-kg-none")?.addEventListener("click", () => {
    $$("#kg-charts-grid input").forEach((input) => {
      input.checked = false;
      input.closest(".chart-item")?.classList.remove("selected");
    });
    updateSelectedCount();
    updateModeButtons();
    markDirty("已清空酷狗榜单");
  });

  $("#btn-wy-all")?.addEventListener("click", () => {
    $$("#wy-charts-grid input").forEach((input) => {
      input.checked = true;
      input.closest(".chart-item")?.classList.add("selected");
    });
    updateSelectedCount();
    updateModeButtons();
    markDirty("已全选网易云榜单");
  });
  $("#btn-wy-none")?.addEventListener("click", () => {
    $$("#wy-charts-grid input").forEach((input) => {
      input.checked = false;
      input.closest(".chart-item")?.classList.remove("selected");
    });
    updateSelectedCount();
    updateModeButtons();
    markDirty("已清空网易云榜单");
  });
}

/* -------------------------------------------------------------- 启动 */
(async function boot() {
  await loadConfig();
  await loadStatus();
  await loadPlatforms(false);  // 页面加载不自动拉起预览进程，等用户点选音源
  await loadCharts();
  const note = $("#save-note");
  if (note && !dirty) note.textContent = "配置已就绪，可随时点击保存生效";
  setInterval(loadStatus, 15000);
})();

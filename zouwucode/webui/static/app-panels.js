/* ZOUWUCODE GUI — status, sessions, workspace, slash commands and theme. */
(() => {
  "use strict";
  const {
    $, chat, input, composer, sendBtn, interruptBtn, statusDot, statusText,
    slashMenu, attachBtn, attachInput, attachPreview, MAX_ATTACH,
    state, esc, now, toast, api,
    addAssistant, addSystem, addUser, autoGrow, clearMarkers, decide,
    emptyState, requestInterrupt, scroll, send, syncMarkers,
  } = window.ZWC;

  /* ── status ────────────────────────────────────────────────────────────── */
  async function loadStatus() {
    try {
      const s = await api("/api/status");
      $("#status-engine").textContent = s.engine || "—";
      $("#status-mode").textContent = s.mode || "—";
      $("#status-reasoning").textContent = s.reasoning || "—";
      $("#status-thinking").textContent = s.show_thinking ? "开" : "关";
      $("#status-cache").textContent = s.cache_hit_rate || "—";
      $("#status-req").textContent = s.total_requests ?? "—";
      $("#status-cost").textContent = s.total_cost || "—";
      const used = Number(s.context_tokens || 0);
      const max = Number(s.max_context_tokens || 0) || 1;
      $("#status-context").textContent = `${used.toLocaleString()} / ${max.toLocaleString()}`;
      $("#context-meter").style.width = Math.min(100, (used / max) * 100).toFixed(1) + "%";
      $("#status-rules").textContent = s.rules_loaded ? "已加载" : "无";
      $("#status-skills").textContent = (s.skills_loaded || []).join(", ") || "无";
      $("#status-modules").textContent = (s.modules || []).join(", ") || "无";
      if ($("#mode-select").value !== s.mode && s.mode) $("#mode-select").value = s.mode;
      if ($("#reasoning-select").value !== s.reasoning && s.reasoning) $("#reasoning-select").value = s.reasoning;
      state.showThinking = !!s.show_thinking;
      $("#thinking-toggle").setAttribute("aria-pressed", String(state.showThinking));
      document.title = s.running ? "● ZOUWUCODE — 执行中" : "ZOUWUCODE";
    } catch { /* status is best-effort */ }
  }

  $("#mode-select").addEventListener("change", async (e) => {
    try { await api("/api/mode", { mode: e.target.value }); toast("模式：" + e.target.value, "ok"); }
    catch (err) { toast("切换模式失败：" + err.message, "error"); }
  });
  $("#reasoning-select").addEventListener("change", async (e) => {
    try { await api("/api/reasoning", { reasoning: e.target.value }); toast("推理：" + e.target.value, "ok"); }
    catch (err) { toast("切换推理失败：" + err.message, "error"); }
  });
  $("#thinking-toggle").addEventListener("click", async () => {
    try {
      const r = await api("/api/thinking", {});
      state.showThinking = !!r.show_thinking;
      $("#thinking-toggle").setAttribute("aria-pressed", String(state.showThinking));
    } catch (err) { toast("切换失败：" + err.message, "error"); }
  });

  /* ── workspace ─────────────────────────────────────────────────────────── */
  const workspaceName = $("#workspace-name");
  let workspaceState = { current: "", recent: [] };

  function setWorkspaceLabel(path) {
    const name = (path || "").split(/[\\/]/).filter(Boolean).pop() || path || "工作空间";
    workspaceName.textContent = name;
    $("#workspace-btn").title = path || "切换工作空间";
  }

  async function loadWorkspace() {
    try {
      const data = await api("/api/workspaces");
      workspaceState = { current: data.current || "", recent: data.recent || [] };
      setWorkspaceLabel(workspaceState.current);
    } catch { /* best effort */ }
  }

  function openWorkspaceModal() {
    const list = $("#workspace-list");
    const items = [...new Set([workspaceState.current, ...workspaceState.recent].filter(Boolean))];
    list.innerHTML = items.length ? "" : `<li>没有最近的工作空间</li>`;
    items.forEach((p) => {
      const li = document.createElement("li");
      li.textContent = p;
      li.title = p;
      if (p === workspaceState.current) li.className = "current";
      li.addEventListener("click", () => switchWorkspace(p));
      list.appendChild(li);
    });
    $("#workspace-path").value = "";
    $("#workspace-modal").hidden = false;
    $("#workspace-path").focus();
  }

  async function switchWorkspace(path) {
    const target = (path || "").trim();
    if (!target) { toast("请填写目录路径", "error"); return; }
    if (state.busy) { toast("任务执行中，先打断再切换", "error"); return; }
    try {
      const r = await api("/api/workspace/switch", { path: target });
      if (!r.ok) { toast("切换失败：" + (r.error || "unknown"), "error"); return; }
      $("#workspace-modal").hidden = true;
      setWorkspaceLabel(r.workspace);
      newChat();
      if (r.note) addSystem("ℹ️ " + r.note);
      toast("工作空间：" + r.workspace, "ok");
      loadSessions();
      loadWorkspace();
    } catch (err) { toast("切换失败：" + err.message, "error"); }
  }

  $("#workspace-btn").addEventListener("click", openWorkspaceModal);
  $("#workspace-cancel").addEventListener("click", () => { $("#workspace-modal").hidden = true; });
  $("#workspace-open").addEventListener("click", () => switchWorkspace($("#workspace-path").value));
  $("#workspace-path").addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") { ev.preventDefault(); switchWorkspace(ev.target.value); }
  });

  /* ── sessions ──────────────────────────────────────────────────────────── */
  function sessionRow(item, { archived = false } = {}) {
    const row = document.createElement("div");
    row.className = "session-row";
    const btn = document.createElement("button");
    btn.className = "session-item" + (item.id === state.sessionId ? " active" : "");
    btn.title = item.id;
    btn.innerHTML = `<b>${esc(item.title || item.id)}</b><span>${esc(item.updated || "")} · ${item.turns ?? 0} 轮</span>`;
    btn.addEventListener("click", () => (archived ? sessionAction("unarchive", item.id) : resumeSession(item.id, btn)));

    const acts = document.createElement("div");
    acts.className = "session-actions";
    const actions = archived
      ? [["rename", "✎", "重命名"], ["unarchive", "↩", "恢复"], ["delete", "✕", "删除"]]
      : [["rename", "✎", "重命名"], ["fork", "⑂", "分支"], ["archive", "⇩", "归档"], ["delete", "✕", "删除"]];
    actions.forEach(([act, glyph, tip]) => {
      const a = document.createElement("button");
      a.type = "button";
      a.dataset.act = act;
      a.title = tip;
      a.textContent = glyph;
      a.addEventListener("click", (ev) => { ev.stopPropagation(); sessionAction(act, item.id); });
      acts.appendChild(a);
    });
    row.appendChild(btn);
    row.appendChild(acts);
    return row;
  }

  async function loadSessions() {
    try {
      const [active, archived] = await Promise.all([
        api("/api/sessions"),
        api("/api/sessions/archived").catch(() => ({ sessions: [] })),
      ]);
      const list = $("#session-list");
      const items = active.sessions || [];
      list.innerHTML = "";
      if (!items.length) list.innerHTML = `<div class="placeholder">还没有保存的会话</div>`;
      items.forEach((s) => list.appendChild(sessionRow(s)));

      const archivedItems = archived.sessions || [];
      $("#archived-summary").textContent = `已归档 (${archivedItems.length})`;
      const archivedList = $("#archived-list");
      archivedList.innerHTML = "";
      archivedItems.forEach((s) => archivedList.appendChild(sessionRow(s, { archived: true })));
    } catch { /* best-effort */ }
  }

  async function sessionAction(act, id) {
    if (act === "delete") {
      if (!window.confirm(`删除会话 ${id}？此操作不可撤销。`)) return;
    }
    if (state.busy) { toast("任务执行中，先打断再操作会话", "error"); return; }
    if (act === "rename") {
      const current = document.querySelector(`.session-item[title="${CSS.escape(id)}"] b`);
      const next = window.prompt("会话名称", current ? current.textContent : "");
      if (next === null) return;
      const title = next.trim();
      if (!title) { toast("名称不能为空", "error"); return; }
      try {
        const r = await api("/api/session/rename", { id, title });
        if (!r.ok) { toast("重命名失败：" + (r.error || "unknown"), "error"); return; }
        toast("已重命名：" + title, "ok");
        loadSessions();
      } catch (err) { toast("重命名失败：" + err.message, "error"); }
      return;
    }
    const path = { delete: "/api/session/delete", archive: "/api/session/archive",
                   unarchive: "/api/session/unarchive", fork: "/api/session/fork" }[act];
    if (!path) return;
    try {
      const r = await api(path, { id });
      if (!r.ok) { toast("操作失败：" + (r.error || "unknown"), "error"); return; }
      const label = { delete: "已删除", archive: "已归档", unarchive: "已恢复", fork: "已分支" }[act];
      toast(`${label}：${act === "fork" ? r.id : id}`, "ok");
      if (act === "fork" && r.id) resumeSession(r.id, null);
      else if (act === "delete" && id === state.sessionId) { newChat(); }
      else loadSessions();
    } catch (err) { toast("操作失败：" + err.message, "error"); }
  }

  function newChat() {
    chat.innerHTML = "";
    clearMarkers();
    emptyState();
    state.sessionId = null;
    document.querySelectorAll(".session-item").forEach((el) => el.classList.remove("active"));
    input.focus();
  }

  function renderTurns(turns) {
    chat.innerHTML = "";
    clearMarkers();
    (turns || []).forEach((t) => {
      if (t.role === "user") addUser(t.content);
      else addAssistant(t.content, true);
    });
    scroll(true);
    syncMarkers();
  }

  async function resumeSession(id, btn) {
    if (state.busy) { toast("任务执行中，先打断再切换会话", "error"); return; }
    try {
      const data = await api("/api/session/resume", { id });
      if (!data.ok) { toast("恢复失败：" + (data.error || "unknown"), "error"); return; }
      state.sessionId = id;
      renderTurns(data.turns || []);
      document.querySelectorAll(".session-item").forEach((el) => el.classList.remove("active"));
      if (btn) btn.classList.add("active");
      closeDrawer();
      toast("已恢复会话 " + id, "ok");
    } catch (err) { toast("恢复失败：" + err.message, "error"); }
  }

  /* Fork at a specific message (hover ⑂ on any row). */
  async function forkAt(index, total) {
    if (!state.sessionId) { toast("当前没有可分支的会话（先发一轮对话）", "error"); return; }
    try {
      const r = await api("/api/session/fork", { id: state.sessionId, turn: Number(index) });
      if (!r.ok) { toast("分支失败：" + (r.error || "unknown"), "error"); return; }
      toast("已从该消息分支：" + r.id, "ok");
      state.sessionId = r.id;
      renderTurns((await api("/api/session/resume", { id: r.id })).turns || []);
      loadSessions();
    } catch (err) { toast("分支失败：" + err.message, "error"); }
  }
  chat.addEventListener("click", (ev) => {
    const fork = ev.target.closest(".fork-here");
    if (!fork) return;
    const row = fork.closest(".msg");
    if (row) forkAt(row.dataset.index);
  });

  /* Restore the conversation the user left off in (the "sessions vanished"
     complaint): prefer the live session, else the newest one with turns. */
  async function bootstrapSession() {
    try {
      const current = await api("/api/session/current");
      if (current.session_id && (current.turns || []).length) {
        state.sessionId = current.session_id;
        renderTurns(current.turns);
        return;
      }
      const data = await api("/api/sessions");
      const latest = (data.sessions || []).find((s) => (s.turns || 0) > 0);
      if (latest) {
        await resumeSession(latest.id, null);
        toast("已恢复上次会话 " + latest.id, "ok");
      }
    } catch { /* stay on the empty state */ }
  }

  $("#new-session").addEventListener("click", () => {
    newChat();
    closeDrawer();
    toast("已开新对话（会话在下次提问时建立）");
  });

  /* ── narrow-screen drawer (session list is unreachable otherwise) ──────── */
  function closeDrawer() { $("#sidebar").classList.remove("open"); }
  $("#sidebar-toggle").addEventListener("click", () => {
    $("#sidebar").classList.toggle("open");
  });
  document.addEventListener("click", (ev) => {
    const sidebar = $("#sidebar");
    if (!sidebar.classList.contains("open")) return;
    if (sidebar.contains(ev.target) || ev.target.closest("#sidebar-toggle")) return;
    closeDrawer();
  });

  /* ── commands ──────────────────────────────────────────────────────────── */
  const COMMANDS = [
    { cmd: "/help", desc: "列出可用命令", run: () => showHelp() },
    { cmd: "/clear", desc: "清空当前对话显示", run: () => { chat.innerHTML = ""; clearMarkers(); emptyState(); } },
    { cmd: "/plan", desc: "切换到只读 Plan 模式", run: () => setMode("plan") },
    { cmd: "/agent", desc: "切换到交互 Agent 模式", run: () => setMode("agent") },
    { cmd: "/yolo", desc: "切换到自动 YOLO 模式", run: () => setMode("yolo") },
    { cmd: "/thinking", desc: "开关思考流显示", run: () => $("#thinking-toggle").click() },
    { cmd: "/theme", desc: "切换深浅主题", run: () => toggleTheme() },
    { cmd: "/sessions", desc: "刷新会话列表", run: () => loadSessions() },
    { cmd: "/usage", desc: "查看用量、缓存节省与花费", run: () => showUsage() },
    { cmd: "/context", desc: "查看上下文占用", run: () => showContext() },
    { cmd: "/oracle", desc: "/oracle <问题> — 只读第二意见复核", run: (arg) => askOracle(arg) },
    { cmd: "/resume", desc: "/resume <会话id> — 恢复历史会话", run: (arg) => resumeArg(arg) },
    { cmd: "/rewind", desc: "/rewind [n] — 回退最近 n 轮文件改动", run: (arg) => rewind(arg) },
    { cmd: "/msg", desc: "/msg <对端> <消息> — 跨会话消息", run: (arg) => sendPeer(arg) },
    { cmd: "/share", desc: "导出当前会话为 Markdown", run: () => shareSession() },
    { cmd: "/memory", desc: "查看项目记忆（目标/决策/状态）", run: () => showJson("/api/memory", "项目记忆") },
    { cmd: "/decide", desc: "/decide <文本> — 记录一条决策", run: (arg) => recordDecision(arg) },
    { cmd: "/goal", desc: "/goal <目标> — 设置项目目标", run: (arg) => setGoal(arg) },
    { cmd: "/compress", desc: "查看上下文压缩配置", run: () => showJson("/api/compress", "压缩") },
    { cmd: "/agents", desc: "列出可用 Agent", run: () => showJson("/api/agents", "Agent") },
    { cmd: "/inbox", desc: "查看跨会话消息", run: () => showJson("/api/inbox", "收件箱") },
    { cmd: "/peers", desc: "列出本机其他会话", run: () => showJson("/api/peers", "Peers") },
  ];

  async function showJson(path, title) {
    try {
      const data = await api(path);
      const body = JSON.stringify(data, null, 1);
      addSystem(`${title}：\n${body.slice(0, 1600)}`);
    } catch (err) { toast(`读取${title}失败：` + err.message, "error"); }
  }

  async function recordDecision(text) {
    const value = (text || "").trim();
    if (!value) { addSystem("用法：/decide <文本>"); return; }
    try {
      const r = await api("/api/decide", { text: value });
      addSystem(r.ok ? "已记录决策" : "记录失败：" + (r.error || "unknown"));
    } catch (err) { toast("记录失败：" + err.message, "error"); }
  }

  async function setGoal(goal) {
    const value = (goal || "").trim();
    if (!value) { addSystem("用法：/goal <目标>"); return; }
    try {
      const r = await api("/api/goal", { goal: value });
      addSystem(r.ok === false ? "设置失败：" + (r.error || "unknown") : "目标已设置：" + value);
    } catch (err) { toast("设置失败：" + err.message, "error"); }
  }

  async function setMode(mode) {
    $("#mode-select").value = mode;
    try { await api("/api/mode", { mode }); toast("模式：" + mode, "ok"); }
    catch (err) { toast("切换模式失败：" + err.message, "error"); }
  }

  function showHelp() {
    addSystem("命令：" + COMMANDS.map((c) => `${c.cmd} — ${c.desc}`).join("　|　"));
  }

  async function showUsage() {
    try {
      const u = await api("/api/usage");
      const lines = Object.entries(u || {}).map(([k, v]) => `${k}: ${v}`);
      addSystem(lines.length ? "用量 — " + lines.join(" · ") : "暂无用量数据");
    } catch (err) { toast("读取用量失败：" + err.message, "error"); }
  }

  async function showContext() {
    try {
      const c = await api("/api/context");
      addSystem(`上下文：${Number(c.prompt_tokens || 0).toLocaleString()} / ` +
        `${Number(c.max_context_tokens || 0).toLocaleString()}` +
        ` · 压缩：${c.enabled ? "开" : "关"}`);
    } catch (err) { toast("读取上下文失败：" + err.message, "error"); }
  }

  async function askOracle(question) {
    const q = (question || "").trim();
    if (!q) { addSystem("用法：/oracle <问题>"); return; }
    addSystem("Oracle 正在复核…");
    try {
      const r = await api("/api/oracle", { question: q });
      if (!r.ok) { addSystem("Oracle 不可用：" + (r.error || "unknown")); return; }
      addAssistant(`**Oracle（${r.model || "read-only"}）**\n\n${r.answer || "（无输出）"}`);
    } catch (err) { toast("Oracle 失败：" + err.message, "error"); }
  }

  function resumeArg(id) {
    const sid = (id || "").trim();
    if (!sid) { addSystem("用法：/resume <会话id>（可在左侧点选）"); return; }
    resumeSession(sid, null);
  }

  async function rewind(arg) {
    const turns = Math.max(0, parseInt(arg || "1", 10) || 1);
    try {
      const r = await api("/api/rewind", { index: turns, scope: "both" });
      addSystem(r.ok ? `已回退 ${turns} 轮` : "回退不可用：" + (r.error || "unknown"));
    } catch (err) { toast("回退失败：" + err.message, "error"); }
  }

  async function sendPeer(arg) {
    const parts = (arg || "").trim().split(/\s+/);
    const to = parts.shift() || "";
    const text = parts.join(" ");
    if (!to || !text) { addSystem("用法：/msg <对端> <消息>"); return; }
    try {
      const r = await api("/api/msg", { to, text });
      addSystem(r.ok ? `已发送给 ${to}` : "发送失败：" + (r.error || "unknown"));
    } catch (err) { toast("发送失败：" + err.message, "error"); }
  }

  async function shareSession() {
    try {
      const r = await api("/api/share", {});
      addSystem(r.ok === false ? "导出不可用：" + (r.error || "unknown")
        : "会话已导出" + (r.path ? "：" + r.path : ""));
    } catch (err) { toast("导出失败：" + err.message, "error"); }
  }

  function renderSlash() {
    const value = input.value;
    if (!value.startsWith("/") || value.includes(" ")) { hideSlash(); return; }
    const matches = COMMANDS.filter((c) => c.cmd.startsWith(value));
    if (!matches.length) { hideSlash(); return; }
    slashMenu.innerHTML = matches.map((c, i) =>
      `<div class="slash-item${i === 0 ? " sel" : ""}" data-cmd="${c.cmd}"><b>${c.cmd}</b><span>${esc(c.desc)}</span></div>`).join("");
    slashMenu.hidden = false;
    slashMenu.querySelectorAll("[data-cmd]").forEach((el) => {
      el.addEventListener("click", () => {
        input.value = el.dataset.cmd;
        hideSlash();
        runCommand(input.value);
      });
    });
  }
  function hideSlash() { slashMenu.hidden = true; slashMenu.innerHTML = ""; }

  function runCommand(text) {
    const trimmed = (text || "").trim();
    const [command, ...rest] = trimmed.split(/\s+/);
    const arg = rest.join(" ");
    const hit = COMMANDS.find((c) => c.cmd === command);
    if (!hit) return false;
    hit.run(arg);
    input.value = "";
    autoGrow();
    return true;
  }

  composer.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const text = input.value.trim();
    if (!text && !state.attachments.length) return;
    if (text && runCommand(text)) return;
    send(text);
  });

  /* ── palette ───────────────────────────────────────────────────────────── */
  let paletteSel = 0;
  function openPalette() {
    $("#palette").hidden = false;
    const box = $("#palette-input");
    box.value = "";
    renderPalette("");
    box.focus();
  }
  function closePalette() { $("#palette").hidden = true; }

  function renderPalette(query) {
    const q = query.trim().toLowerCase();
    const list = COMMANDS.filter((c) => !q || c.cmd.includes(q) || c.desc.toLowerCase().includes(q));
    paletteSel = Math.min(paletteSel, Math.max(0, list.length - 1));
    $("#palette-list").innerHTML = list.map((c, i) =>
      `<li${i === paletteSel ? ' class="sel"' : ""} data-cmd="${c.cmd}"><b>${c.cmd}</b><span>${esc(c.desc)}</span></li>`).join("");
    $("#palette-list").querySelectorAll("li").forEach((li) => {
      li.addEventListener("click", () => { closePalette(); runCommand(li.dataset.cmd); });
    });
  }
  $("#palette-input").addEventListener("input", (e) => renderPalette(e.target.value));
  $("#palette-input").addEventListener("keydown", (ev) => {
    const items = [...$("#palette-list").querySelectorAll("li")];
    if (ev.key === "ArrowDown") { ev.preventDefault(); paletteSel = Math.min(paletteSel + 1, items.length - 1); renderPalette($("#palette-input").value); }
    else if (ev.key === "ArrowUp") { ev.preventDefault(); paletteSel = Math.max(paletteSel - 1, 0); renderPalette($("#palette-input").value); }
    else if (ev.key === "Enter") {
      ev.preventDefault();
      const cmd = items[paletteSel]?.dataset.cmd;
      closePalette();
      if (cmd) runCommand(cmd);
    } else if (ev.key === "Escape") { closePalette(); input.focus(); }
  });
  $("#palette-btn").addEventListener("click", openPalette);

  /* ── theme ─────────────────────────────────────────────────────────────── */
  function applyTheme() {
    document.documentElement.dataset.theme = state.theme;
    localStorage.setItem("zwc-theme", state.theme);
  }
  function toggleTheme() {
    state.theme = state.theme === "dark" ? "light" : "dark";
    applyTheme();
    toast(state.theme === "dark" ? "深色主题" : "浅色主题");
  }
  $("#theme-toggle").addEventListener("click", toggleTheme);

  /* ── global keys ───────────────────────────────────────────────────────── */
  document.addEventListener("keydown", (ev) => {
    const meta = ev.ctrlKey || ev.metaKey;
    if (meta && ev.key.toLowerCase() === "k") { ev.preventDefault(); openPalette(); return; }
    if (meta && ev.key.toLowerCase() === "l") { ev.preventDefault(); chat.innerHTML = ""; emptyState(); return; }
    if (meta && ev.key.toLowerCase() === "j") { ev.preventDefault(); toggleTheme(); return; }
    if (ev.key === "Escape") {
      if (!$("#palette").hidden) { closePalette(); return; }
      if (!$("#workspace-modal").hidden) { $("#workspace-modal").hidden = true; return; }
      if (!$("#interrupt-confirm").hidden) { $("#interrupt-confirm").hidden = true; return; }
      if (!$("#approval").hidden) { decide("n"); return; }
      if (!slashMenu.hidden) { hideSlash(); return; }
      if (state.busy) { requestInterrupt(); return; }
    }
    if (meta && ev.key === "Enter" && document.activeElement === input) {
      ev.preventDefault();
      composer.requestSubmit();
    }
  });

  Object.assign(window.ZWC, {
    loadStatus, loadWorkspace, loadSessions, bootstrapSession, applyTheme,
    toggleTheme, openPalette, closePalette, renderSlash, hideSlash,
    runCommand, resumeSession, forkAt, newChat, sessionAction, rewind,
    sendPeer, shareSession, setGoal, recordDecision, setMode, showUsage,
    showContext, askOracle, showHelp, showJson, switchWorkspace,
    openWorkspaceModal, closeDrawer, renderTurns, sessionRow, setWorkspaceLabel,
    resumeArg,
  });
})();
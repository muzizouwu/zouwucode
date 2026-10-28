/* ZOUWUCODE GUI — no framework, no CDN, everything offline. */
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const chat = $("#chat");
  const input = $("#input");
  const composer = $("#composer");
  const sendBtn = $("#send-btn");
  const interruptBtn = $("#interrupt-btn");
  const statusDot = $("#status-dot");
  const statusText = $("#status-text");
  const slashMenu = $("#slash-menu");

  const state = {
    busy: false,
    theme: localStorage.getItem("zwc-theme") || "dark",
    showThinking: true,
    abort: null,
  };

  /* ── utils ─────────────────────────────────────────────────────────────── */
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
  const now = () => new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

  function toast(message, kind = "") {
    const el = document.createElement("div");
    el.className = "toast " + kind;
    el.textContent = message;
    $("#toasts").appendChild(el);
    setTimeout(() => el.remove(), 4200);
  }

  async function api(path, body) {
    const opts = body === undefined
      ? {}
      : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
    const resp = await fetch(path, opts);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const text = await resp.text();
    try { return JSON.parse(text); } catch { return {}; }
  }

  /* ── markdown-lite (safe: everything is escaped first) ─────────────────── */
  function renderMarkdown(src) {
    const fences = [];
    let text = String(src ?? "");
    text = text.replace(/```([\w+-]*)\n?([\s\S]*?)```/g, (_, lang, code) => {
      const i = fences.push({ lang: lang || "", code }) - 1;
      return `\u0000FENCE${i}\u0000`;
    });
    text = esc(text);
    text = text.replace(/^### (.+)$/gm, "<h3>$1</h3>")
      .replace(/^## (.+)$/gm, "<h2>$1</h2>")
      .replace(/^# (.+)$/gm, "<h1>$1</h1>")
      .replace(/^[-*] (.+)$/gm, "<li>$1</li>")
      .replace(/(<li>[\s\S]*?<\/li>)(?![\s\S]*<li>)/g, "<ul>$1</ul>")
      .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
      .replace(/`([^`\n]+)`/g, "<code>$1</code>")
      .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
    text = text.split(/\n{2,}/).map((block) => {
      const t = block.trim();
      if (!t) return "";
      if (/^<(h\d|ul|li|\u0000FENCE)/.test(t)) return t;
      return `<p>${t.replace(/\n/g, "<br>")}</p>`;
    }).join("");
    return text.replace(/\u0000FENCE(\d+)\u0000/g, (_, i) => {
      const { lang, code } = fences[Number(i)];
      return `<div class="code-block">${lang ? `<span class="lang">${esc(lang)}</span>` : ""}` +
        `<button class="copy" type="button">复制</button>` +
        `<pre><code>${esc(code.replace(/\n$/, ""))}</code></pre></div>`;
    });
  }

  chat.addEventListener("click", (ev) => {
    const btn = ev.target.closest(".code-block .copy");
    if (!btn) return;
    const code = btn.parentElement.querySelector("code").innerText;
    navigator.clipboard?.writeText(code).then(
      () => { btn.textContent = "已复制"; setTimeout(() => (btn.textContent = "复制"), 1200); },
      () => toast("复制失败", "error"),
    );
  });

  /* ── chat DOM ──────────────────────────────────────────────────────────── */
  function emptyState() {
    if (chat.children.length) return;
    chat.innerHTML = `
      <div class="empty-state">
        <h1>开始一个任务</h1>
        <p>描述目标，Agent 会规划、调用工具并汇报结果。</p>
        <div class="hints">
          <button data-prompt="解释这个仓库的结构，指出三个最值得改进的地方">解释仓库结构</button>
          <button data-prompt="运行测试，找出失败用例并修复">修复失败用例</button>
          <button data-prompt="/help">查看命令</button>
        </div>
      </div>`;
  }
  chat.addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-prompt]");
    if (!btn) return;
    input.value = btn.dataset.prompt;
    autoGrow();
    input.focus();
  });

  function clearEmpty() {
    const empty = chat.querySelector(".empty-state");
    if (empty) empty.remove();
  }

  function scroll(force = false) {
    const near = chat.scrollHeight - chat.scrollTop - chat.clientHeight < 160;
    if (force || near) chat.scrollTop = chat.scrollHeight;
  }

  function addMessage(role, label) {
    clearEmpty();
    const row = document.createElement("div");
    row.className = `msg ${role}`;
    const initial = { user: "你", assistant: "Z", system: "·", module: "M" }[role] || "·";
    row.innerHTML = `<div class="avatar">${initial}</div><div class="bubble">
      <div class="meta"><b>${esc(label || role)}</b><span>${now()}</span></div>
      <div class="body"></div></div>`;
    chat.appendChild(row);
    scroll(true);
    return row.querySelector(".body");
  }

  function addUser(text) {
    const body = addMessage("user", "你");
    body.textContent = text;
    return body;
  }

  function addAssistant(text, render = true) {
    const body = addMessage("assistant", "ZOUWUCODE");
    if (render && text) body.innerHTML = renderMarkdown(text);
    return body;
  }

  function addSystem(text) {
    const body = addMessage("system", "系统");
    body.textContent = text;
    return body;
  }

  function addThinking() {
    const body = addMessage("assistant", "ZOUWUCODE");
    const wrap = document.createElement("details");
    wrap.className = "thinking";
    wrap.open = true;
    wrap.innerHTML = `<summary>思考过程</summary><div class="think-body"></div>`;
    body.appendChild(wrap);
    return wrap.querySelector(".think-body");
  }

  function addToolCard(name, args, status = "") {
    const body = addMessage("assistant", "工具");
    const card = document.createElement("div");
    card.className = "tool-card " + status;
    card.innerHTML = `<span class="tool-ico">◆</span><div><b>${esc(name)}</b>
      <div class="tool-args">${esc(args || "")}</div></div>`;
    body.appendChild(card);
    return card;
  }

  /* ── composer ──────────────────────────────────────────────────────────── */
  function autoGrow() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 220) + "px";
  }
  input.addEventListener("input", () => { autoGrow(); renderSlash(); });
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && !ev.shiftKey) {
      ev.preventDefault();
      hideSlash();
      composer.requestSubmit();
    }
  });

  function setBusy(busy) {
    state.busy = busy;
    statusDot.dataset.state = busy ? "busy" : "ok";
    statusText.textContent = busy ? "执行中…" : "就绪";
    sendBtn.disabled = busy;
    interruptBtn.hidden = !busy;
    statusText.title = busy ? "按 Esc 可打断当前任务" : "";
  }

  async function send(text) {
    const message = (text ?? input.value).trim();
    if (!message || state.busy) return;
    input.value = "";
    autoGrow();
    hideSlash();
    addUser(message);
    setBusy(true);

    let thinkingNode = null;
    let contentNode = null;
    let streamedAny = false;
    const controller = new AbortController();
    state.abort = controller;

    try {
      const resp = await fetch("/api/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message }),
        signal: controller.signal,
      });
      if (!resp.ok || !resp.body) throw new Error("HTTP " + resp.status);

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf("\n\n")) !== -1) {
          const chunk = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          const line = chunk.split("\n").find((l) => l.startsWith("data: "));
          if (!line) continue;
          let evt;
          try { evt = JSON.parse(line.slice(6)); } catch { continue; }
          handleEvent(evt, () => {
            if (!thinkingNode) thinkingNode = addThinking();
            return thinkingNode;
          }, () => {
            if (!contentNode) { contentNode = addAssistant("", false); contentNode.parentElement.parentElement.classList.add("streaming"); }
            streamedAny = true;
            return contentNode;
          });
        }
        scroll();
      }
    } catch (err) {
      if (err.name !== "AbortError") {
        addSystem("⚠️ 连接失败：" + err.message);
        toast("连接失败：" + err.message, "error");
      }
    } finally {
      state.abort = null;
      setBusy(false);
      scroll(true);
      loadStatus();
    }
  }

  function handleEvent(evt, ensureThinking, ensureContent) {
    switch (evt.type) {
      case "thinking": {
        if (!state.showThinking) return;
        ensureThinking().textContent += evt.delta || "";
        return;
      }
      case "content": {
        const node = ensureContent();
        node.textContent += evt.delta || "";
        return;
      }
      case "approval_request": {
        showApproval(evt);
        return;
      }
      case "done": {
        document.querySelectorAll(".streaming").forEach((el) => el.classList.remove("streaming"));
        if (evt.tools && evt.tools.length) {
          evt.tools.forEach((t) => addToolCard(t.name || "tool", t.arguments || "", "done"));
        }
        if (evt.content) {
          const body = evt.streamed ? null : addAssistant(evt.content, true);
          if (!body && !evt.streamed) addAssistant(evt.content, true);
        }
        if (evt.cache_hit) {
          const last = chat.querySelector(".msg:last-child .meta");
          if (last) {
            const chip = document.createElement("span");
            chip.className = "chip hit";
            chip.textContent = "⚡ cache hit";
            last.appendChild(chip);
          }
        }
        if (evt.module) addSystem(`[${evt.module}] ${evt.plan || ""} ${evt.tasks_completed ?? ""}/${evt.tasks_total ?? ""}`);
        scroll();
        return;
      }
      case "interrupted": {
        document.querySelectorAll(".streaming").forEach((el) => el.classList.remove("streaming"));
        addSystem("⏹ 任务已打断 — 输入「继续」从断点恢复，或 /clear 放弃。");
        toast("任务已打断");
        return;
      }
      case "error": {
        document.querySelectorAll(".streaming").forEach((el) => el.classList.remove("streaming"));
        addSystem("⚠️ " + (evt.error || "未知错误"));
        toast(evt.error || "未知错误", "error");
        return;
      }
      default:
        return;
    }
  }

  /* ── approvals ─────────────────────────────────────────────────────────── */
  let pendingApproval = null;
  function showApproval(evt) {
    pendingApproval = evt.id;
    $("#approval-tool").textContent = evt.tool || "tool";
    $("#approval-preview").textContent = evt.preview || "(无参数预览)";
    $("#approval").hidden = false;
    $("#approve-once").focus();
  }

  async function decide(decision) {
    const id = pendingApproval;
    pendingApproval = null;
    $("#approval").hidden = true;
    if (!id) return;
    const label = decision === "a" ? "本会话始终允许" : decision === "y" ? "允许一次" : "已拒绝";
    addSystem(`🔐 ${label}`);
    try {
      await api("/api/approve", { id, decision });
    } catch (err) {
      toast("审批回传失败：" + err.message, "error");
    }
  }
  $("#approve-once").addEventListener("click", () => decide("y"));
  $("#approve-always").addEventListener("click", () => decide("a"));
  $("#approve-deny").addEventListener("click", () => decide("n"));

  /* ── interrupt (confirm first: Esc must never kill work by accident) ───── */
  function requestInterrupt() {
    if (!state.busy) return;
    $("#interrupt-confirm").hidden = false;
    $("#interrupt-ok").focus();
  }

  async function confirmInterrupt() {
    $("#interrupt-confirm").hidden = true;
    if (!state.busy) return;
    try {
      const res = await api("/api/interrupt", {});
      if (res.interrupting) toast("正在等待任务停止…");
      else toast(res.message || "没有正在执行的任务");
    } catch (err) {
      toast("打断失败：" + err.message, "error");
    }
  }
  interruptBtn.addEventListener("click", requestInterrupt);
  $("#interrupt-ok").addEventListener("click", confirmInterrupt);
  $("#interrupt-cancel").addEventListener("click", () => { $("#interrupt-confirm").hidden = true; });

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

  /* ── sessions ──────────────────────────────────────────────────────────── */
  async function loadSessions() {
    try {
      const data = await api("/api/sessions");
      const list = $("#session-list");
      const items = data.sessions || [];
      if (!items.length) { list.innerHTML = `<div class="placeholder">还没有保存的会话</div>`; return; }
      list.innerHTML = "";
      items.forEach((s) => {
        const btn = document.createElement("button");
        btn.className = "session-item";
        btn.innerHTML = `<b>${esc(s.id)}</b><span>${esc(s.updated || "")} · ${s.turns ?? 0} 轮</span>`;
        btn.addEventListener("click", () => resumeSession(s.id, btn));
        list.appendChild(btn);
      });
    } catch { /* best-effort */ }
  }

  async function resumeSession(id, btn) {
    if (state.busy) { toast("任务执行中，先打断再切换会话", "error"); return; }
    try {
      const data = await api("/api/session/resume", { id });
      if (!data.ok) { toast("恢复失败：" + (data.error || "unknown"), "error"); return; }
      chat.innerHTML = "";
      (data.turns || []).forEach((t) => {
        if (t.role === "user") addUser(t.content);
        else addAssistant(t.content, true);
      });
      document.querySelectorAll(".session-item").forEach((el) => el.classList.remove("active"));
      btn?.classList.add("active");
      toast("已恢复会话 " + id, "ok");
      scroll(true);
    } catch (err) { toast("恢复失败：" + err.message, "error"); }
  }
  $("#new-session").addEventListener("click", () => {
    chat.innerHTML = "";
    emptyState();
    input.focus();
    toast("已开新对话（会话在下次提问时建立）");
  });

  /* ── commands ──────────────────────────────────────────────────────────── */
  const COMMANDS = [
    { cmd: "/help", desc: "列出可用命令", run: () => showHelp() },
    { cmd: "/clear", desc: "清空当前对话显示", run: () => { chat.innerHTML = ""; emptyState(); } },
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
    if (!text) return;
    if (runCommand(text)) return;
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

  /* ── boot ──────────────────────────────────────────────────────────────── */
  applyTheme();
  emptyState();
  loadStatus();
  loadSessions();
  setInterval(loadStatus, 3000);
  setInterval(() => { if (!$("#palette").hidden) return; loadSessions(); }, 15000);
  input.focus();
})();

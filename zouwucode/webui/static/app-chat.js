/* ZOUWUCODE GUI — chat rendering, streaming and attachments (loaded second). */
(() => {
  "use strict";
  const {
    $, chat, input, composer, sendBtn, interruptBtn, statusDot, statusText,
    slashMenu, attachBtn, attachInput, attachPreview,
    MAX_ATTACH, MAX_ATTACH_BYTES, MAX_ATTACH_TOTAL,
    state, esc, now, toast, api,
  } = window.ZWC;
  // The panels script loads after this one; resolve those calls at runtime.
  const loadSessions = (...a) => window.ZWC.loadSessions(...a);
  const loadStatus = (...a) => window.ZWC.loadStatus(...a);
  const renderSlash = (...a) => window.ZWC.renderSlash(...a);
  const hideSlash = (...a) => window.ZWC.hideSlash(...a);

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
    const index = state.msgCount++;
    const row = document.createElement("div");
    row.className = `msg ${role}`;
    row.dataset.index = String(index);
    const initial = { user: "你", assistant: "Z", system: "·", module: "M" }[role] || "·";
    row.innerHTML = `<div class="avatar">${initial}</div><div class="bubble">
      <div class="meta"><b>${esc(label || role)}</b><span>${now()}</span>
        <button class="fork-here" type="button" title="从此处分支">⑂</button></div>
      <div class="body"></div></div>`;
    chat.appendChild(row);
    addMarker(row, role, label);
    scroll(true);
    return row.querySelector(".body");
  }

  /* ── trajectory markers (dot rail; click to jump) ───────────────────────── */
  const markers = $("#markers");
  const markerTip = $("#marker-tip");

  function markerLabel(row) {
    if (row.classList.contains("user")) return "你";
    if (row.classList.contains("system")) return "系统";
    if (row.classList.contains("module")) return "模块";
    if (row.querySelector(".tool-card")) return "工具";
    return "ZOUWUCODE";
  }

  function markerPreview(row) {
    const body = row.querySelector(".body");
    const text = (body?.innerText || "").replace(/\s+/g, " ").trim();
    return text.length > 180 ? text.slice(0, 179) + "…" : text;
  }

  function showMarkerTip(dot, row) {
    const head = document.createElement("b");
    head.textContent = markerLabel(row);
    const text = document.createElement("span");
    text.textContent = markerPreview(row) || "（空消息）";
    markerTip.replaceChildren(head, text);
    markerTip.hidden = false;
    const rect = dot.getBoundingClientRect();
    const top = Math.max(12, Math.min(
      rect.top + rect.height / 2 - markerTip.offsetHeight / 2,
      window.innerHeight - markerTip.offsetHeight - 12,
    ));
    markerTip.style.top = top + "px";
    markerTip.style.left = Math.max(12, rect.left - markerTip.offsetWidth - 14) + "px";
  }

  function hideMarkerTip() {
    markerTip.hidden = true;
  }

  function addMarker(row, role, label) {
    const dot = document.createElement("button");
    dot.type = "button";
    dot.className = "marker " + role;
    dot.dataset.index = row.dataset.index;
    dot.setAttribute("aria-label", (label || role) + "：点击跳转到该消息");
    dot.addEventListener("click", () => row.scrollIntoView({ block: "center" }));
    dot.addEventListener("mouseenter", () => showMarkerTip(dot, row));
    dot.addEventListener("focus", () => showMarkerTip(dot, row));
    dot.addEventListener("mouseleave", hideMarkerTip);
    dot.addEventListener("blur", hideMarkerTip);
    markers.appendChild(dot);
  }
  function clearMarkers() {
    markers.innerHTML = "";
    hideMarkerTip();
    state.msgCount = 0;
  }
  function syncMarkers() {
    if (!markers.childElementCount) return;
    const chatTop = chat.getBoundingClientRect().top + 80;
    let active = null;
    chat.querySelectorAll(".msg").forEach((row) => {
      if (row.getBoundingClientRect().top <= chatTop) active = row.dataset.index;
    });
    markers.querySelectorAll(".marker").forEach((dot) => {
      dot.classList.toggle("active", dot.dataset.index === active);
    });
  }
  chat.addEventListener("scroll", () => { hideMarkerTip(); syncMarkers(); });
  window.addEventListener("resize", hideMarkerTip);

  function addUser(text, files = []) {
    const body = addMessage("user", "你");
    if (text) {
      const line = document.createElement("div");
      line.textContent = text;
      body.appendChild(line);
    }
    if (files.length) {
      const list = document.createElement("div");
      list.className = "attach-list";
      files.forEach((file) => {
        const item = document.createElement("div");
        item.className = "attach-item";
        if ((file.mime || "").startsWith("image/")) {
          const img = document.createElement("img");
          img.src = file.data;
          img.alt = file.name;
          item.appendChild(img);
        }
        const cap = document.createElement("span");
        cap.textContent = file.name;
        item.appendChild(cap);
        list.appendChild(item);
      });
      body.appendChild(list);
    }
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

  /* ── attachments ───────────────────────────────────────────────────────── */
  function fmtSize(n) {
    return n >= 1024 * 1024 ? (n / 1048576).toFixed(1) + " MB"
      : Math.max(1, Math.round(n / 1024)) + " KB";
  }

  function readFile(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result || ""));
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(file);
    });
  }

  async function addAttachment(file) {
    if (!file) return;
    if (state.attachments.length >= MAX_ATTACH) {
      toast(`一次最多 ${MAX_ATTACH} 个附件`, "error");
      return;
    }
    if (file.size > MAX_ATTACH_BYTES) {
      toast(`${file.name} 超过 8 MB，已跳过`, "error");
      return;
    }
    const total = state.attachments.reduce((sum, a) => sum + a.size, 0);
    if (total + file.size > MAX_ATTACH_TOTAL) {
      toast("附件合计超过 32 MB，已跳过 " + file.name, "error");
      return;
    }
    try {
      const data = await readFile(file);
      state.attachments.push({
        id: "att-" + Math.random().toString(36).slice(2, 9),
        name: file.name || "file",
        mime: file.type || "application/octet-stream",
        size: file.size || 0,
        data,
      });
      renderAttachments();
    } catch {
      toast("读取失败：" + (file.name || "文件"), "error");
    }
  }

  function renderAttachments() {
    attachPreview.innerHTML = "";
    attachPreview.hidden = state.attachments.length === 0;
    state.attachments.forEach((att) => {
      const chip = document.createElement("div");
      chip.className = "attach-chip";
      if ((att.mime || "").startsWith("image/")) {
        const img = document.createElement("img");
        img.src = att.data;
        img.alt = att.name;
        chip.appendChild(img);
      } else {
        const ico = document.createElement("span");
        ico.className = "attach-ico";
        ico.textContent = "▤";
        chip.appendChild(ico);
      }
      const name = document.createElement("span");
      name.className = "attach-name";
      name.textContent = att.name;
      name.title = `${att.name} · ${fmtSize(att.size)}`;
      const meta = document.createElement("span");
      meta.className = "attach-size";
      meta.textContent = fmtSize(att.size);
      const del = document.createElement("button");
      del.type = "button";
      del.className = "attach-del";
      del.title = "移除";
      del.setAttribute("aria-label", "移除 " + att.name);
      del.textContent = "×";
      del.addEventListener("click", () => {
        state.attachments = state.attachments.filter((a) => a !== att);
        renderAttachments();
      });
      chip.append(name, meta, del);
      attachPreview.appendChild(chip);
    });
  }

  function clearAttachments() {
    state.attachments = [];
    renderAttachments();
  }

  async function addAttachmentFiles(files) {
    for (const file of Array.from(files || [])) await addAttachment(file);
  }

  attachBtn.addEventListener("click", () => attachInput.click());
  attachInput.addEventListener("change", async () => {
    await addAttachmentFiles(attachInput.files);
    attachInput.value = "";
  });
  input.addEventListener("paste", (ev) => {
    const files = ev.clipboardData?.files;
    if (files && files.length) {
      ev.preventDefault();
      addAttachmentFiles(files);
    }
  });
  ["dragenter", "dragover"].forEach((type) => {
    composer.addEventListener(type, (ev) => {
      ev.preventDefault();
      composer.classList.add("dragging");
    });
  });
  composer.addEventListener("dragleave", (ev) => {
    if (composer.contains(ev.relatedTarget)) return;
    composer.classList.remove("dragging");
  });
  composer.addEventListener("drop", (ev) => {
    ev.preventDefault();
    composer.classList.remove("dragging");
    addAttachmentFiles(ev.dataTransfer?.files);
  });

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
    const files = state.attachments.slice();
    if ((!message && !files.length) || state.busy) return;
    input.value = "";
    autoGrow();
    hideSlash();
    addUser(message, files);
    clearAttachments();
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
        body: JSON.stringify({
          message,
          attachments: files.map(({ name, mime, data }) => ({ name, mime, data })),
        }),
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
      case "attachments": {
        const saved = evt.saved || [];
        if (saved.length) {
          addSystem("附件已保存到工作区：" + saved.map((s) => s.relative).join("、"));
        }
        (evt.errors || []).forEach((err) => toast(err, "error"));
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
        loadSessions();   // first turn persisted → auto-title appears
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

  Object.assign(window.ZWC, {
    renderMarkdown, emptyState, clearEmpty, scroll, addMessage, addUser,
    addAssistant, addSystem, addThinking, addToolCard, addAttachment,
    addAttachmentFiles, renderAttachments, clearAttachments, fmtSize, readFile,
    autoGrow, setBusy, send, handleEvent, showApproval, decide,
    requestInterrupt, confirmInterrupt, addMarker, clearMarkers, syncMarkers,
    markerLabel, markerPreview, showMarkerTip, hideMarkerTip,
  });
})();

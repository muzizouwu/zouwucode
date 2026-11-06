/* ZOUWUCODE GUI — shared state, DOM refs and helpers (loaded first). */
(() => {
  "use strict";
  const ZWC = (window.ZWC = window.ZWC || {});

  const $ = (sel, root = document) => root.querySelector(sel);
  const chat = $("#chat");
  const input = $("#input");
  const composer = $("#composer");
  const sendBtn = $("#send-btn");
  const interruptBtn = $("#interrupt-btn");
  const statusDot = $("#status-dot");
  const statusText = $("#status-text");
  const slashMenu = $("#slash-menu");
  const attachBtn = $("#attach-btn");
  const attachInput = $("#attach-input");
  const attachPreview = $("#attach-preview");

  const MAX_ATTACH = 5;
  const MAX_ATTACH_BYTES = 8 * 1024 * 1024;
  const MAX_ATTACH_TOTAL = 32 * 1024 * 1024;  // matches the server-side cap

  const state = {
    busy: false,
    theme: localStorage.getItem("zwc-theme") || "dark",
    showThinking: true,
    abort: null,
    sessionId: null,
    msgCount: 0,
    attachments: [],
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

  Object.assign(ZWC, {
    $, chat, input, composer, sendBtn, interruptBtn, statusDot, statusText,
    slashMenu, attachBtn, attachInput, attachPreview,
    MAX_ATTACH, MAX_ATTACH_BYTES, MAX_ATTACH_TOTAL,
    state, esc, now, toast, api,
  });
})();
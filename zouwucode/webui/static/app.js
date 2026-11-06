/* ZOUWUCODE GUI — boot sequence (loaded last; core/chat/panels define the API). */
(() => {
  "use strict";
  const {
    $, input, applyTheme, emptyState, loadStatus, loadSessions, loadWorkspace,
    bootstrapSession,
  } = window.ZWC;

  /* ── boot ──────────────────────────────────────────────────────────────── */
  applyTheme();
  emptyState();
  loadStatus();
  loadSessions();
  loadWorkspace();
  bootstrapSession();
  setInterval(loadStatus, 3000);
  setInterval(() => { if (!$("#palette").hidden) return; loadSessions(); }, 15000);
  input.focus();
})();

"""Local web server providing a DeepCode-inspired browser-based UI for ZOUWUCODE.

Usage:
    zouwucode --web              # Start with web UI on port 8080
    zouwucode --web --port 3000  # Custom port
"""

import asyncio
import json
import time
import uuid
import traceback
from typing import Optional

from ..engine.loop import EngineLoop, TaskInterrupted
from ..tools.registry import ToolRegistry
from ..agent.coordinator import AgentCoordinator
from ..config import ZOUWUCODEConfig


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ZOUWUCODE - AI Coding Agent</title>
<style>
  /* ── DeepCode-inspired Dark Theme ──────────────────────────────────── */
  :root {
    --bg-primary: #0a0e27;
    --bg-secondary: #1a1f3a;
    --bg-surface: #121630;
    --bg-input: #0d1130;
    --border: #2a2f4a;
    --text-primary: #e8eaf0;
    --text-secondary: #8892b0;
    --text-muted: #5a6280;
    --accent-blue: #64b5f6;
    --accent-cyan: #4dd0e1;
    --accent-green: #81c784;
    --accent-purple: #ba68c8;
    --accent-orange: #ffb74d;
    --accent-red: #e57373;
    --neon-glow: 0 0 20px rgba(100, 181, 246, 0.15);
    --font-mono: 'Cascadia Code', 'Fira Code', 'JetBrains Mono', 'Consolas', monospace;
    --font-sans: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
    --radius: 10px;
    --radius-sm: 6px;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: var(--font-sans);
    background: var(--bg-primary);
    color: var(--text-primary);
    height: 100vh;
    overflow: hidden;
    display: flex;
    flex-direction: column;
  }
  ::-webkit-scrollbar { width: 6px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
  ::-webkit-scrollbar-thumb:hover { background: var(--text-muted); }

  /* ── Header ────────────────────────────────────────────────────────── */
  #header {
    background: var(--bg-secondary);
    border-bottom: 1px solid var(--border);
    padding: 0 24px;
    height: 52px;
    display: flex;
    align-items: center;
    gap: 16px;
    flex-shrink: 0;
  }
  #header .logo {
    font-family: var(--font-mono);
    font-size: 15px;
    font-weight: 700;
    letter-spacing: 3px;
    background: linear-gradient(135deg, var(--accent-blue), var(--accent-cyan));
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
  }
  #header .badge {
    font-size: 10px;
    color: var(--text-muted);
    background: var(--bg-primary);
    padding: 2px 8px;
    border-radius: 10px;
    border: 1px solid var(--border);
    font-family: var(--font-mono);
  }
  #header .header-right {
    margin-left: auto;
    display: flex;
    align-items: center;
    gap: 12px;
  }

  /* ── Responsive (narrow screens) ─────────────────────────────────── */
  @media (max-width: 560px) {
    #header {
      flex-wrap: wrap;
      height: auto;
      min-height: 52px;
      padding: 8px 16px;
      row-gap: 4px;
    }
    #header .header-right { margin-left: 0; }
    #status-bar { padding: 0 12px; }
    #chat { padding: 16px; }
    .msg { max-width: 96%; }
  }

  /* ── Mode Tabs ─────────────────────────────────────────────────────── */
  #mode-tabs {
    display: flex;
    gap: 2px;
    background: var(--bg-primary);
    border-radius: var(--radius-sm);
    padding: 2px;
  }
  .mode-tab {
    padding: 4px 14px;
    border: none;
    background: transparent;
    color: var(--text-muted);
    cursor: pointer;
    border-radius: 4px;
    font-size: 12px;
    font-family: var(--font-mono);
    transition: all 0.15s;
  }
  .mode-tab:hover { color: var(--text-secondary); }
  .mode-tab.active {
    background: var(--accent-blue);
    color: #fff;
    box-shadow: 0 0 12px rgba(100, 181, 246, 0.3);
  }
  .mode-tab[data-mode="plan"].active { background: var(--accent-purple); box-shadow: 0 0 12px rgba(186, 104, 200, 0.3); }
  .mode-tab[data-mode="yolo"].active { background: var(--accent-orange); box-shadow: 0 0 12px rgba(255, 183, 77, 0.3); }

  /* ── Status Bar ────────────────────────────────────────────────────── */
  #status-bar {
    background: var(--bg-secondary);
    border-bottom: 1px solid var(--border);
    padding: 0 24px;
    height: 32px;
    display: flex;
    align-items: center;
    gap: 16px;
    font-size: 11px;
    color: var(--text-muted);
    font-family: var(--font-mono);
    flex-shrink: 0;
    overflow-x: auto;
  }
  #status-bar .status-dot {
    width: 6px; height: 6px;
    border-radius: 50%;
    display: inline-block;
    flex-shrink: 0;
  }
  #status-bar .status-dot.online { background: var(--accent-green); box-shadow: 0 0 6px rgba(129, 199, 132, 0.5); }
  #status-bar .status-dot.offline { background: var(--accent-red); }
  #status-bar .stat-item { display: flex; align-items: center; gap: 6px; white-space: nowrap; }
  #status-bar .stat-value { color: var(--text-secondary); }
  #status-bar .stat-sep { color: var(--border); }

  /* ── Chat Container ────────────────────────────────────────────────── */
  #chat-container {
    flex: 1;
    display: flex;
    overflow: hidden;
  }

  /* ── Sidebar ───────────────────────────────────────────────────────── */
  #sidebar {
    width: 260px;
    background: var(--bg-surface);
    border-right: 1px solid var(--border);
    padding: 12px;
    overflow-y: auto;
    flex-shrink: 0;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  #sidebar .sidebar-section {
    background: var(--bg-secondary);
    border-radius: var(--radius-sm);
    border: 1px solid var(--border);
    overflow: hidden;
  }
  #sidebar .sidebar-section-header {
    padding: 8px 10px;
    font-size: 10px;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 1.2px;
    font-weight: 600;
    background: rgba(255,255,255,0.02);
    border-bottom: 1px solid var(--border);
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: space-between;
    user-select: none;
  }
  #sidebar .sidebar-section-header:hover { color: var(--text-secondary); }
  #sidebar .sidebar-section-header .toggle { font-size: 9px; transition: transform 0.15s; }
  #sidebar .sidebar-section-header .toggle.collapsed { transform: rotate(-90deg); }
  #sidebar .sidebar-section-body {
    padding: 6px;
    max-height: 200px;
    overflow-y: auto;
  }
  #sidebar .sidebar-section-body.collapsed { display: none; }
  #sidebar .session-item {
    padding: 6px 8px;
    border-radius: 4px;
    cursor: pointer;
    font-size: 11px;
    color: var(--text-secondary);
    transition: all 0.12s;
    border: 1px solid transparent;
    line-height: 1.3;
    overflow-wrap: anywhere;
  }
  #sidebar .session-item:hover {
    background: var(--bg-primary);
    border-color: var(--border);
  }
  #sidebar .session-item .session-time {
    font-size: 10px;
    color: var(--text-muted);
  }
  #sidebar .session-btn {
    padding: 5px 8px;
    border: 1px solid var(--border);
    background: transparent;
    color: var(--text-secondary);
    cursor: pointer;
    border-radius: 4px;
    font-size: 10px;
    font-family: var(--font-mono);
    transition: all 0.12s;
    width: 100%;
    text-align: center;
    margin-top: 4px;
  }
  #sidebar .session-btn:hover {
    background: var(--bg-primary);
    border-color: var(--accent-blue);
    color: var(--text-primary);
  }
  #sidebar .skill-item {
    padding: 4px 8px;
    font-size: 11px;
    color: var(--text-secondary);
    display: flex;
    align-items: center;
    gap: 4px;
  }
  #sidebar .skill-item .skill-dot {
    width: 4px; height: 4px;
    border-radius: 50%;
    background: var(--accent-green);
    flex-shrink: 0;
  }
  #sidebar .skill-item .skill-unload {
    margin-left: auto;
    cursor: pointer;
    color: var(--text-muted);
    font-size: 10px;
    padding: 0 4px;
  }
  #sidebar .skill-item .skill-unload:hover { color: var(--accent-red); }
  #sidebar .rules-content {
    padding: 6px 8px;
    font-size: 11px;
    color: var(--text-secondary);
    line-height: 1.5;
    max-height: 120px;
    overflow-y: auto;
    overflow-x: hidden;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  #sidebar .rules-content.empty { color: var(--text-muted); font-style: italic; }
  #sidebar .config-notice {
    padding: 8px;
    background: rgba(255, 183, 77, 0.08);
    border: 1px solid rgba(255, 183, 77, 0.15);
    border-radius: var(--radius-sm);
    font-size: 10px;
    color: var(--accent-orange);
    line-height: 1.4;
  }
  #sidebar .config-notice.ok {
    background: rgba(129, 199, 132, 0.08);
    border-color: rgba(129, 199, 132, 0.15);
    color: var(--accent-green);
  }
  #sidebar .config-notice code {
    font-family: var(--font-mono);
    font-size: 10px;
  }

  /* ── Chat Area ─────────────────────────────────────────────────────── */
  #chat {
    flex: 1;
    overflow-y: auto;
    padding: 24px;
    display: flex;
    flex-direction: column;
    gap: 16px;
  }
  .msg {
    max-width: 88%;
    padding: 12px 16px;
    border-radius: var(--radius);
    line-height: 1.65;
    font-size: 13.5px;
    white-space: pre-wrap;
    word-break: break-word;
    position: relative;
    animation: msgIn 0.2s ease-out;
  }
  @keyframes msgIn { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: translateY(0); } }
  .msg.user {
    align-self: flex-end;
    background: linear-gradient(135deg, #1a3a6a, #1a2f5a);
    border: 1px solid rgba(100, 181, 246, 0.2);
    border-bottom-right-radius: 2px;
  }
  .msg.assistant {
    align-self: flex-start;
    background: var(--bg-surface);
    border: 1px solid var(--border);
    border-bottom-left-radius: 2px;
  }
  .msg.system {
    align-self: center;
    background: transparent;
    color: var(--text-muted);
    font-size: 11px;
    padding: 4px 12px;
    max-width: 100%;
  }
  .msg.thinking {
    align-self: flex-start;
    background: transparent;
    border-left: 2px solid var(--accent-cyan);
    color: var(--text-secondary);
    font-style: italic;
    font-size: 12.5px;
    padding: 8px 16px;
    max-width: 100%;
  }
  .msg.thinking details { width: 100%; }
  .msg.thinking summary {
    cursor: pointer;
    color: var(--accent-cyan);
    font-family: var(--font-mono);
    font-style: normal;
    user-select: none;
    outline: none;
  }
  .msg.thinking .thinking-body {
    margin-top: 6px;
    white-space: pre-wrap;
    color: var(--text-secondary);
    max-height: 240px;
    overflow-y: auto;
  }
  .msg .tool-line {
    font-family: var(--font-mono);
    font-size: 12px;
    color: var(--accent-cyan);
    padding: 2px 0;
    white-space: pre-wrap;
    word-break: break-all;
  }
  .msg .tool-line .tool-arrow { color: var(--accent-purple); font-weight: 700; }
  .msg .msg-label {
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 1px;
    margin-bottom: 4px;
  }
  .msg.user .msg-label { color: var(--accent-blue); }
  .msg.assistant .msg-label { color: var(--accent-green); }
  .msg .cache-hit {
    font-size: 10px;
    color: var(--accent-green);
    margin-top: 6px;
    display: flex;
    align-items: center;
    gap: 4px;
  }
  .msg .error-detail {
    font-size: 11px;
    color: var(--accent-red);
    margin-top: 6px;
    padding: 6px 8px;
    background: rgba(229, 115, 115, 0.1);
    border-radius: 4px;
  }

  /* ── Welcome Screen ────────────────────────────────────────────────── */
  #welcome {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    color: var(--text-muted);
    gap: 12px;
  }
  #welcome .welcome-logo {
    font-family: var(--font-mono);
    font-size: 32px;
    font-weight: 700;
    letter-spacing: 6px;
    background: linear-gradient(135deg, var(--accent-blue), var(--accent-cyan));
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    margin-bottom: 8px;
  }
  #welcome .welcome-sub {
    font-size: 13px;
    color: var(--text-secondary);
  }
  #welcome .welcome-hint {
    font-size: 12px;
    color: var(--text-muted);
    font-family: var(--font-mono);
    margin-top: 8px;
  }
  #welcome .welcome-features {
    display: flex;
    gap: 24px;
    margin-top: 16px;
    font-size: 12px;
    flex-wrap: wrap;
    justify-content: center;
  }
  #welcome .welcome-features span {
    display: flex;
    align-items: center;
    gap: 6px;
  }
  #welcome .welcome-art {
    font-family: var(--font-mono);
    font-size: 11px;
    line-height: 1.15;
    color: var(--accent-cyan);
    letter-spacing: 0;
    /* 保留 ASCII 艺术字换行结构，防止换行符被折叠导致横幅纵向压缩 */
    white-space: pre;
    max-width: 100%;
    overflow-x: auto;
    text-shadow: 0 0 12px rgba(77, 208, 225, 0.22);
    text-align: center;
    user-select: none;
  }
  #welcome .welcome-modules {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    justify-content: center;
    margin-top: 10px;
  }
  #welcome .welcome-modules .module-chip {
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--accent-purple);
    background: var(--bg-surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 3px 10px;
  }

  /* ── Status progress bar ───────────────────────────────────────────── */
  #status-progress {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--text-secondary);
  }
  #status-progress .prog-blocks { color: var(--accent-green); letter-spacing: 1px; }

  /* ── Input Area ────────────────────────────────────────────────────── */
  #input-area {
    background: var(--bg-secondary);
    border-top: 1px solid var(--border);
    padding: 12px 24px 16px;
    flex-shrink: 0;
  }
  #input-row {
    display: flex;
    gap: 10px;
    align-items: flex-end;
    background: var(--bg-input);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 4px;
    transition: border-color 0.2s;
  }
  #input-row:focus-within { border-color: var(--accent-blue); box-shadow: var(--neon-glow); }
  #input-row textarea {
    flex: 1;
    background: transparent;
    border: none;
    color: var(--text-primary);
    padding: 8px 12px;
    resize: none;
    font-family: var(--font-mono);
    font-size: 13px;
    min-height: 22px;
    max-height: 120px;
    outline: none;
    line-height: 1.5;
  }
  #input-row textarea::placeholder { color: var(--text-muted); }
  #input-row #send-btn {
    padding: 8px 18px;
    background: var(--accent-blue);
    color: #fff;
    border: none;
    border-radius: var(--radius-sm);
    cursor: pointer;
    font-size: 12px;
    font-weight: 600;
    font-family: var(--font-sans);
    transition: all 0.15s;
    white-space: nowrap;
  }
  #input-row #send-btn:hover { opacity: 0.9; box-shadow: 0 0 16px rgba(100, 181, 246, 0.3); }
  #input-row #send-btn:disabled { opacity: 0.4; cursor: not-allowed; box-shadow: none; }
  #input-row #send-btn .key-hint {
    font-size: 10px;
    opacity: 0.6;
    margin-left: 4px;
  }
  #input-hint {
    font-size: 10px;
    color: var(--text-muted);
    margin-top: 6px;
    padding-left: 4px;
    font-family: var(--font-mono);
  }

  /* ── Loading ───────────────────────────────────────────────────────── */
  .loading-msg {
    align-self: flex-start;
    background: var(--bg-surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 14px 18px;
    display: flex;
    align-items: center;
    gap: 10px;
    animation: msgIn 0.2s ease-out;
  }
  .loading-dots {
    display: flex;
    gap: 4px;
  }
  .loading-dots span {
    width: 6px; height: 6px;
    border-radius: 50%;
    background: var(--accent-blue);
    animation: dotBounce 1.2s infinite;
  }
  .loading-dots span:nth-child(2) { animation-delay: 0.2s; }
  .loading-dots span:nth-child(3) { animation-delay: 0.4s; }
  @keyframes dotBounce {
    0%, 80%, 100% { transform: scale(0.6); opacity: 0.4; }
    40% { transform: scale(1); opacity: 1; }
  }
  .loading-msg .loading-text {
    color: var(--text-secondary);
    font-size: 12.5px;
  }

  /* ── Reasoning selector ────────────────────────────────────────────── */
  #reasoning-selector {
    display: flex;
    align-items: center;
    gap: 6px;
  }
  #reasoning-selector select {
    background: var(--bg-primary);
    border: 1px solid var(--border);
    color: var(--text-secondary);
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 11px;
    font-family: var(--font-mono);
    cursor: pointer;
    outline: none;
  }
  #reasoning-selector select:focus { border-color: var(--accent-blue); }
  #reasoning-selector label {
    color: var(--text-muted);
    font-size: 11px;
  }
</style>
</head>
<body>
<div id="header">
  <span class="logo">ZOUWUCODE</span>
  <span class="badge">v1.0.0</span>
  <div class="header-right">
    <div id="mode-tabs">
      <button class="mode-tab" data-mode="plan" onclick="setMode('plan')">Plan</button>
      <button class="mode-tab active" data-mode="agent" onclick="setMode('agent')">Agent</button>
      <button class="mode-tab" data-mode="yolo" onclick="setMode('yolo')">YOLO</button>
    </div>
    <div id="reasoning-selector">
      <label>Reasoning</label>
      <select onchange="setReasoning(this.value)">
        <option value="low">Low</option>
        <option value="medium" selected>Medium</option>
        <option value="max">Max</option>
      </select>
    </div>
  </div>
</div>
<div id="status-bar">
  <span class="stat-item">
    <span class="status-dot online" id="status-dot"></span>
    <span id="status-text">Ready</span>
  </span>
  <span class="stat-sep">|</span>
  <span class="stat-item">Mode: <span class="stat-value" id="status-mode">AGENT</span></span>
  <span class="stat-item">Reasoning: <span class="stat-value" id="status-reasoning">MEDIUM</span></span>
  <span class="stat-item">Thinking: <span class="stat-value" id="status-thinking">ON</span></span>
  <span class="stat-item">Cache: <span class="stat-value" id="status-cache">0%</span></span>
  <span class="stat-item">Cost: <span class="stat-value" id="status-cost">$0.0000</span></span>
  <span class="stat-item">Req: <span class="stat-value" id="status-req">0</span></span>
  <span class="stat-sep">|</span>
  <span class="stat-item">Context: <span class="stat-value" id="status-context-tokens">0</span></span>
  <span class="stat-item">Rules: <span class="stat-value" id="status-rules">No</span></span>
  <span class="stat-item">Skills: <span class="stat-value" id="status-skills">0</span></span>
  <span class="stat-item">Modules: <span class="stat-value" id="status-modules">—</span></span>
  <span class="stat-sep">|</span>
  <span class="stat-item" id="status-progress"><span class="prog-blocks" id="status-prog-blocks">⬝⬝⬝⬝⬝⬝⬝⬝⬝⬝</span><span id="status-prog-pct">0%</span></span>
</div>
<div id="chat-container">
  <div id="sidebar">
    <!-- Sessions -->
    <div class="sidebar-section">
      <div class="sidebar-section-header" onclick="toggleSection(this)">
        <span>Sessions</span>
        <span class="toggle">▼</span>
      </div>
      <div class="sidebar-section-body" id="session-list">
        <div style="font-size:11px;color:var(--text-muted);padding:4px 8px;">Loading...</div>
      </div>
      <div style="padding:4px 6px 6px;">
        <button class="session-btn" onclick="newSession()">+ New Session</button>
      </div>
    </div>

    <!-- Rules -->
    <div class="sidebar-section">
      <div class="sidebar-section-header" onclick="toggleSection(this)">
        <span>Rules (.zouwucode/rules.md)</span>
        <span class="toggle">▼</span>
      </div>
      <div class="sidebar-section-body" id="rules-section">
        <div class="rules-content empty" id="rules-content">No rules loaded.</div>
      </div>
    </div>

    <!-- Skills -->
    <div class="sidebar-section">
      <div class="sidebar-section-header" onclick="toggleSection(this)">
        <span>Skills</span>
        <span class="toggle">▼</span>
      </div>
      <div class="sidebar-section-body" id="skills-section">
        <div style="font-size:11px;color:var(--text-muted);padding:4px 8px;">Loading...</div>
      </div>
    </div>

    <!-- API Status -->
    <div class="config-notice" id="api-notice">
      ⚡ Configure API key in <code>config.yaml</code>
    </div>
  </div>
  <div id="chat">
    <div id="welcome">
      <div class="welcome-art">███████╗ ██████╗ ██╗   ██╗██╗   ██╗██╗  ██╗
╚══███╔╝██╔═══██╗██║   ██║██║   ██║██║ ██╔╝
  ███╔╝ ██║   ██║██║   ██║██║   ██║█████╔╝
 ███╔╝  ██║   ██║██║   ██║██║   ██║██╔═██╗
███████╗╚██████╔╝╚██████╔╝╚██████╔╝██║  ██╗
╚══════╝ ╚═════╝  ╚═════╝  ╚═════╝ ╚═╝  ╚═╝</div>
      <div class="welcome-sub">DeepSeek-native AI Coding Agent — 1M Context</div>
      <div class="welcome-hint">Type a message to start coding. Ctrl+Enter to send.</div>
      <div class="welcome-features">
        <span>⚡ Cache-First</span>
        <span>🔧 9+ Tools</span>
        <span>🧠 3 Modes</span>
        <span>🎯 Reasoning</span>
        <span>📜 1M Context</span>
        <span>🔒 Sandbox</span>
        <span>📦 Skills</span>
        <span>📋 Rules</span>
      </div>
      <div class="welcome-modules" id="welcome-modules"></div>
    </div>
  </div>
</div>
<div id="input-area">
  <div id="input-row">
    <textarea id="input" rows="1" placeholder="Type a message... (Ctrl+Enter to send)" onkeydown="onInputKey(event)"></textarea>
    <button id="interrupt-btn" onclick="interruptTask()" title="打断当前任务 (Esc)" style="display:none">⏹ 打断</button>
    <button id="send-btn" onclick="send()">Send <span class="key-hint">↵</span></button>
  </div>
  <div id="input-hint">/help · /clear · /hello-plan · /hello-status · Ctrl+S:Cycle Mode · Ctrl+R:Reasoning · Esc:Interrupt</div>
</div>
<script>
  const chat = document.getElementById('chat');
  const input = document.getElementById('input');
  const sendBtn = document.getElementById('send-btn');
  let processing = false;
  let sessionId = null;

  // ── Auto-resize textarea ──────────────────────────────────────────────
  input.addEventListener('input', () => {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 120) + 'px';
  });

  // ── Key handling ──────────────────────────────────────────────────────
  function onInputKey(e) {
    if (e.ctrlKey && e.key === 'Enter') { e.preventDefault(); send(); }
    if (e.ctrlKey && e.key === 's') { e.preventDefault(); cycleMode(); }
    if (e.ctrlKey && e.key === 'l') { e.preventDefault(); clearChat(); }
    if (e.key === 'Escape') { e.preventDefault(); interruptTask(); }
  }

  // ── Interrupt running task (Esc / ⏹ button) ──────────────────────────
  async function interruptTask() {
    if (!processing) { return; }
    // Confirmation — prevent accidental interrupts
    if (!confirm('确认打断当前任务？\n\n打断后可输入「继续」从断点恢复，或 /clear 放弃。')) {
      return;
    }
    try {
      const resp = await fetch('/api/interrupt', { method: 'POST' });
      const data = await resp.json();
      if (data.interrupting) {
        updateStatus('Interrupting... (等待任务停止)', 'offline');
        addMsg('system', '⏹ 已发送打断请求，正在等待任务安全停止…');
      } else {
        addMsg('system', 'ⓘ ' + (data.message || '当前没有正在执行的任务'));
      }
    } catch (e) {
      addMsg('system', '⚠️ 打断请求失败: ' + e.message);
    }
  }

  // ── Send Message ──────────────────────────────────────────────────────
  async function send() {
    const text = input.value.trim();
    if (!text || processing) return;

    // Handle slash commands
    if (text.startsWith('/')) {
      handleCommand(text);
      input.value = '';
      input.style.height = 'auto';
      return;
    }

    input.value = '';
    input.style.height = 'auto';
    hideWelcome();
    addMsg('user', text);
    processing = true;
    sendBtn.disabled = true;
    document.getElementById('interrupt-btn').style.display = '';
    updateStatus('Processing... (Esc 打断)', 'offline');
    showLoading();

    // Ultrawork keyword → hello-my-zouwucode module pipeline
    const uwMatch = text.match(/^(?:ultrawork|ulw)\b[:\s]*(.*)$/i);
    if (uwMatch) {
      try {
        const resp = await fetch('/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message: text }),
        });
        const data = await resp.json();
        removeLoading();
        if (data.error) {
          addMsg('system', '⚠️ Error: ' + data.error);
          updateStatus('Error', 'offline');
        } else {
          addMsg('module', '🤖 [hello-my-zouwucode] Ultrawork: ' + (uwMatch[1] || text));
          if (data.content) addMsg('assistant', data.content);
          if (data.plan) addMsg('system', '📋 Plan: ' + data.plan + '  |  Tasks: ' + data.tasks_completed + '/' + data.tasks_total);
          updateStatus('Ready', 'online');
        }
      } catch (e) {
        removeLoading();
        addMsg('system', '⚠️ Network Error: ' + e.message);
        updateStatus('Disconnected', 'offline');
      }
      processing = false;
      sendBtn.disabled = false;
      document.getElementById('interrupt-btn').style.display = 'none';
      input.focus();
      return;
    }

    try {
      const resp = await fetch('/api/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text }),
      });
      removeLoading();
      if (!resp.ok || !resp.body) throw new Error('HTTP ' + resp.status);

      // Consume SSE events — thinking deltas render live in a collapsible block
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      let thinkingBody = null;
      let sseError = false;
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf('\n\n')) !== -1) {
          const chunk = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          const line = chunk.split('\n').find(l => l.startsWith('data: '));
          if (!line) continue;
          let evt;
          try { evt = JSON.parse(line.slice(6)); } catch (e) { continue; }
          if (evt.type === 'thinking') {
            if (!thinkingBody) thinkingBody = addThinkingStream();
            thinkingBody.textContent += evt.delta;
            chat.scrollTop = chat.scrollHeight;
          } else if (evt.type === 'done') {
            if (evt.module) {
              addMsg('module', '🤖 [hello-my-zouwucode] ' + (evt.module || 'module'));
              if (evt.plan) addMsg('system', '📋 Plan: ' + evt.plan + '  |  Tasks: ' + evt.tasks_completed + '/' + evt.tasks_total);
            }
            if (evt.tools) addToolLines(evt.tools);
            if (evt.content) addMsg('assistant', evt.content);
            if (evt.cache_hit) {
              const last = chat.lastElementChild;
              if (last && last.classList.contains('msg')) {
                const tag = document.createElement('div');
                tag.className = 'cache-hit';
                tag.textContent = '⚡ Cache hit';
                last.appendChild(tag);
              }
            }
            if (evt.stats) {
              document.getElementById('status-cache').textContent = evt.stats;
            }
          } else if (evt.type === 'interrupted') {
            sseError = true;
            addMsg('system', '⏹ 任务已打断 — 输入「继续」从断点恢复，或 /clear 放弃本次任务。');
            updateStatus('Interrupted (已打断 — 可输入「继续」恢复)', 'offline');
          } else if (evt.type === 'error') {
            sseError = true;
            addMsg('system', '⚠️ Error: ' + evt.error);
            updateStatus('Error', 'offline');
          }
        }
      }
      if (!sseError) updateStatus('Ready', 'online');
    } catch (e) {
      removeLoading();
      addMsg('system', '⚠️ Network Error: ' + e.message);
      addMsg('system', 'The API key may not be configured yet. Please edit config.yaml and set your DeepSeek API key, then restart the server.');
      updateStatus('Disconnected', 'offline');
    }
    processing = false;
    sendBtn.disabled = false;
    document.getElementById('interrupt-btn').style.display = 'none';
    input.focus();
  }

  // ── Commands ──────────────────────────────────────────────────────────
  function handleCommand(cmd) {
    hideWelcome();
    if (cmd === '/clear') { clearChat(); return; }
    if (cmd === '/help') {
      addMsg('system', '━━━ Available Commands ━━━');
      addMsg('system', '  /clear        - Clear chat');
      addMsg('system', '  /help         - Show this help');
      addMsg('system', '  /reasoning    - Show current reasoning level');
      addMsg('system', '  /reasoning <low|medium|max> - Set reasoning');
      addMsg('system', '  /thinking     - Toggle live thinking display');
      addMsg('system', '  /rules        - Show project rules');
      addMsg('system', '  /skill list   - List available skills');
      addMsg('system', '  /skill load <name> - Load a skill');
      addMsg('system', '  /skill unload <name> - Unload a skill');
      addMsg('system', '  /sessions     - List sessions');
      addMsg('system', '  /mode         - Show current mode');
      addMsg('system', 'hello-my-zouwucode (multi-agent orchestration):');
      addMsg('system', '  /hello-ultrawork <task> - Full-autonomy pipeline');
      addMsg('system', '  /hello-plan <task>      - Prometheus planning');
      addMsg('system', '  /hello-start-work       - Atlas executes the active plan');
      addMsg('system', '  /hello-status           - Show boulder + notepad status');
      addMsg('system', '  /hello-agents           - List the 11 built-in agents');
      addMsg('system', '  /hello-categories       - List task categories');
      addMsg('system', 'Tip: prefix any message with "ultrawork" or "ulw" to run it autonomously');
      addMsg('system', 'Keyboard: Ctrl+S=Cycle Mode  Ctrl+R=Reasoning  Ctrl+L=Clear');
      return;
    }
    if (cmd.startsWith('/hello-ultrawork')) {
      const task = cmd.split(' ').slice(1).join(' ').trim();
      if (!task) { addMsg('system', 'Usage: /hello-ultrawork <task>'); return; }
      runModule('ultrawork', task, '🤖 [hello-my-zouwucode] Ultrawork: ' + task);
      return;
    }
    if (cmd.startsWith('/hello-plan ') && cmd.length > 12) {
      runModule('plan', cmd.slice(12), '📋 [hello-my-zouwucode] Prometheus planning: ' + cmd.slice(12));
      return;
    }
    if (cmd === '/hello-start-work') {
      runModule('start-work', '', '🏃 [hello-my-zouwucode] Atlas executing active plan...');
      return;
    }
    if (cmd === '/hello-status') {
      runModuleStatus();
      return;
    }
    if (cmd === '/hello-agents') {
      addMsg('system', 'Use /help or run /hello-status in a terminal for the full agent inventory.');
      fetch('/api/hello-my-zouwucode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'status' }),
      }).then(r => r.json()).then(d => {
        if (d.agents) addMsg('system', '🤖 ' + d.agents + ' built-in agents (Sisyphus, Hephaestus, Prometheus, Atlas, Oracle, Librarian, Explore, Multimodal-Looker, Metis, Momus, Sisyphus-Junior)');
      }).catch(() => {});
      return;
    }
    if (cmd === '/hello-categories') {
      addMsg('system', 'Task categories: visual-engineering, artistry, ultrabrain, deep, quick, unspecified-low/high, writing, quick-rust, quick-zig, git');
      return;
    }
    if (cmd.startsWith('/reasoning')) {
      const parts = cmd.split(' ');
      if (parts[1] && ['low','medium','max'].includes(parts[1])) {
        setReasoning(parts[1]);
        document.querySelector('#reasoning-selector select').value = parts[1];
        addMsg('system', '✓ Reasoning set to ' + parts[1].toUpperCase());
      } else {
        const current = document.querySelector('#reasoning-selector select').value;
        addMsg('system', 'Current reasoning: ' + current.toUpperCase());
        addMsg('system', 'Usage: /reasoning <low|medium|max>');
      }
      return;
    }
    if (cmd === '/thinking') {
      toggleThinking();
      return;
    }
    if (cmd === '/rules') {
      fetchRules();
      return;
    }
    if (cmd.startsWith('/skill')) {
      const parts = cmd.split(' ');
      if (parts[1] === 'list') { fetchSkills(); return; }
      if (parts[1] === 'load' && parts[2]) { loadSkill(parts[2]); return; }
      if (parts[1] === 'unload' && parts[2]) { unloadSkill(parts[2]); return; }
      addMsg('system', 'Usage: /skill list | load <name> | unload <name>');
      return;
    }
    if (cmd === '/sessions') {
      pollSessions();
      return;
    }
    if (cmd === '/mode') {
      const mode = document.getElementById('status-mode').textContent;
      addMsg('system', 'Current mode: ' + mode);
      return;
    }
    addMsg('system', 'Unknown command: ' + cmd + ' (type /help for commands)');
  }

  // ── hello-my-zouwucode module ─────────────────────────────────────────
  async function runModule(action, task, banner) {
    hideWelcome();
    if (banner) addMsg('module', banner);
    updateStatus('hello-my-zouwucode working...', 'offline');
    showLoading();
    try {
      const resp = await fetch('/api/hello-my-zouwucode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, task }),
      });
      const data = await resp.json();
      removeLoading();
      if (data.error) {
        addMsg('system', '⚠️ [hello-my-zouwucode] ' + data.error);
        updateStatus('Error', 'offline');
      } else {
        if (data.content) addMsg('assistant', data.content);
        if (data.plan) addMsg('system', '📋 Plan: ' + data.plan + '  |  Tasks: ' + data.tasks_completed + '/' + data.tasks_total);
        if (data.details && data.details.length) {
          addMsg('system', 'Details:\n  ' + data.details.join('\n  '));
        }
        updateStatus('Ready', 'online');
      }
    } catch (e) {
      removeLoading();
      addMsg('system', '⚠️ [hello-my-zouwucode] Network Error: ' + e.message);
      updateStatus('Disconnected', 'offline');
    }
  }

  async function runModuleStatus() {
    try {
      const resp = await fetch('/api/hello-my-zouwucode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'status' }),
      });
      const data = await resp.json();
      if (data.error) {
        addMsg('system', '⚠️ [hello-my-zouwucode] ' + data.error);
      } else {
        addMsg('module', '━━━ hello-my-zouwucode ━━━\n' + data.boulder + '\n\n' + data.notepad);
      }
    } catch (e) {
      addMsg('system', '⚠️ [hello-my-zouwucode] Network Error: ' + e.message);
    }
  }

  // ── Rules ─────────────────────────────────────────────────────────────
  async function fetchRules() {
    try {
      const resp = await fetch('/api/rules');
      const data = await resp.json();
      const content = data.content || '(no rules)';
      addMsg('system', '━━━ Rules (.zouwucode/rules.md) ━━━\n' + content);
      document.getElementById('rules-content').textContent = content || 'No rules loaded.';
      document.getElementById('rules-content').className = 'rules-content' + (content ? '' : ' empty');
    } catch (e) {
      addMsg('system', '⚠️ Failed to fetch rules');
    }
  }

  // ── Skills ────────────────────────────────────────────────────────────
  async function fetchSkills() {
    try {
      const resp = await fetch('/api/skills');
      const data = await resp.json();
      let msg = '━━━ Skills ━━━\n';
      msg += 'Available: ' + (data.available?.join(', ') || '(none)') + '\n';
      msg += 'Loaded: ' + (data.loaded?.join(', ') || '(none)');
      addMsg('system', msg);
      updateSkillsSidebar(data);
    } catch (e) {
      addMsg('system', '⚠️ Failed to fetch skills');
    }
  }
  async function loadSkill(name) {
    try {
      const resp = await fetch('/api/skills', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'load', name }),
      });
      const data = await resp.json();
      if (data.error) { addMsg('system', '⚠️ ' + data.error); }
      else { addMsg('system', '✓ Skill "' + name + '" loaded'); fetchSkills(); }
    } catch (e) { addMsg('system', '⚠️ Failed to load skill'); }
  }
  async function unloadSkill(name) {
    try {
      const resp = await fetch('/api/skills', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'unload', name }),
      });
      const data = await resp.json();
      if (data.error) { addMsg('system', '⚠️ ' + data.error); }
      else { addMsg('system', '✓ Skill "' + name + '" unloaded'); fetchSkills(); }
    } catch (e) { addMsg('system', '⚠️ Failed to unload skill'); }
  }
  function updateSkillsSidebar(data) {
    const el = document.getElementById('skills-section');
    const loaded = data.loaded || [];
    if (loaded.length === 0) {
      el.innerHTML = '<div style="font-size:11px;color:var(--text-muted);padding:4px 8px;">No skills loaded.</div>';
    } else {
      el.innerHTML = loaded.map(n =>
        '<div class="skill-item"><span class="skill-dot"></span>' + n +
        '<span class="skill-unload" onclick="unloadSkill(\'' + n + '\')">✕</span></div>'
      ).join('');
    }
  }

  // ── Mode Switching ────────────────────────────────────────────────────
  // Ctrl+S cycles plan → agent → yolo → plan (kept in sync with TUI).
  const MODES = ['plan', 'agent', 'yolo'];
  function cycleMode() {
    const active = document.querySelector('.mode-tab.active');
    const cur = active ? active.dataset.mode : 'agent';
    const idx = MODES.indexOf(cur);
    const next = MODES[(idx + 1) % MODES.length];
    setMode(next);
  }
  async function setMode(mode) {
    document.querySelectorAll('.mode-tab').forEach(b => {
      b.classList.toggle('active', b.dataset.mode === mode);
    });
    try {
      await fetch('/api/mode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode }),
      });
    } catch (e) {}
    document.getElementById('status-mode').textContent = mode.toUpperCase();
    if (chat.children.length > 0) {
      addMsg('system', 'Switched to ' + mode.toUpperCase() + ' mode');
    }
  }

  // ── Reasoning ─────────────────────────────────────────────────────────
  async function setReasoning(level) {
    try {
      await fetch('/api/reasoning', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reasoning: level }),
      });
    } catch (e) {}
    document.getElementById('status-reasoning').textContent = level.toUpperCase();
  }

  // Toggle live thinking display (mirrors /thinking on CLI/TUI)
  async function toggleThinking() {
    try {
      const resp = await fetch('/api/thinking', { method: 'POST' });
      const data = await resp.json();
      addMsg('system', '💭 Thinking display: ' + (data.show_thinking ? 'ON' : 'OFF'));
      if (data.show_thinking !== undefined) {
        document.getElementById('status-thinking').textContent = data.show_thinking ? 'ON' : 'OFF';
      }
    } catch (e) {
      addMsg('system', '⚠️ Failed to toggle thinking display');
    }
  }

  // ── Sidebar toggle ────────────────────────────────────────────────────
  function toggleSection(header) {
    const body = header.nextElementSibling;
    const toggle = header.querySelector('.toggle');
    if (body && toggle) {
      body.classList.toggle('collapsed');
      toggle.classList.toggle('collapsed');
    }
  }

  // ── Chat Helpers ──────────────────────────────────────────────────────
  function hideWelcome() {
    const w = document.getElementById('welcome');
    if (w) w.remove();
  }
  function clearChat() {
    chat.innerHTML = '';
    input.focus();
  }
  function addMsg(role, content) {
    const div = document.createElement('div');
    div.className = 'msg ' + role;
    if (role !== 'system' && role !== 'thinking') {
      const label = document.createElement('div');
      label.className = 'msg-label';
      label.textContent = role === 'user' ? 'You' : 'ZOUWUCODE';
      div.appendChild(label);
    }
    if (role === 'thinking') {
      // opencode-style collapsible thinking block
      const details = document.createElement('details');
      details.open = true;
      const summary = document.createElement('summary');
      summary.textContent = '💭 Thinking';
      details.appendChild(summary);
      const body = document.createElement('div');
      body.className = 'thinking-body';
      body.textContent = content;
      details.appendChild(body);
      div.appendChild(details);
    } else {
      const contentDiv = document.createElement('div');
      contentDiv.textContent = content;
      div.appendChild(contentDiv);
    }
    chat.appendChild(div);
    chat.scrollTop = chat.scrollHeight;
  }

  // opencode-style live thinking block — returns the body element so deltas
  // can be appended incrementally while the model is still reasoning.
  function addThinkingStream() {
    const div = document.createElement('div');
    div.className = 'msg thinking';
    const details = document.createElement('details');
    details.open = true;
    const summary = document.createElement('summary');
    summary.textContent = '💭 Thinking';
    details.appendChild(summary);
    const body = document.createElement('div');
    body.className = 'thinking-body';
    body.textContent = '';
    details.appendChild(body);
    div.appendChild(details);
    chat.appendChild(div);
    chat.scrollTop = chat.scrollHeight;
    return body;
  }
  function addToolLines(tools) {
    if (!tools || !tools.length) return;
    const div = document.createElement('div');
    div.className = 'msg system';
    tools.forEach(t => {
      const line = document.createElement('div');
      line.className = 'tool-line';
      const arrow = document.createElement('span');
      arrow.className = 'tool-arrow';
      arrow.textContent = '→';
      line.appendChild(arrow);
      line.appendChild(document.createTextNode(' ' + t.name + (t.arguments ? ' ' + t.arguments : '')));
      div.appendChild(line);
    });
    chat.appendChild(div);
    chat.scrollTop = chat.scrollHeight;
  }
  function showLoading() {
    const div = document.createElement('div');
    div.className = 'loading-msg';
    div.id = 'loading';
    div.innerHTML = '<div class="loading-dots"><span></span><span></span><span></span></div><span class="loading-text">Thinking...</span>';
    chat.appendChild(div);
    chat.scrollTop = chat.scrollHeight;
  }
  function removeLoading() {
    const el = document.getElementById('loading');
    if (el) el.remove();
  }
  function updateStatus(text, state) {
    document.getElementById('status-text').textContent = text;
    document.getElementById('status-dot').className = 'status-dot ' + (state || 'online');
  }
  function newSession() {
    clearChat();
    addMsg('system', 'New session started');
  }

  // ── Poll Status ───────────────────────────────────────────────────────
  async function pollStatus() {
    try {
      const resp = await fetch('/api/status');
      const data = await resp.json();
      if (data.mode) {
        document.getElementById('status-mode').textContent = data.mode.toUpperCase();
        document.querySelectorAll('.mode-tab').forEach(b => {
          b.classList.toggle('active', b.dataset.mode === data.mode);
        });
      }
      if (data.reasoning) {
        document.getElementById('status-reasoning').textContent = data.reasoning.toUpperCase();
        document.querySelector('#reasoning-selector select').value = data.reasoning;
      }
      if (data.show_thinking !== undefined) {
        document.getElementById('status-thinking').textContent = data.show_thinking ? 'ON' : 'OFF';
      }
      if (data.cache_hit_rate) document.getElementById('status-cache').textContent = data.cache_hit_rate;
      if (data.total_cost) document.getElementById('status-cost').textContent = data.total_cost;
      if (data.total_requests !== undefined) document.getElementById('status-req').textContent = data.total_requests;
      if (data.context_tokens !== undefined) document.getElementById('status-context-tokens').textContent = data.context_tokens;
      document.getElementById('status-rules').textContent = data.rules_loaded ? 'Yes' : 'No';
      if (data.skills_loaded !== undefined) {
        document.getElementById('status-skills').textContent = data.skills_loaded.length;
        updateSkillsSidebar({ loaded: data.skills_loaded });
      }
      if (data.modules !== undefined) {
        const mods = data.modules || [];
        document.getElementById('status-modules').textContent = mods.length ? mods.join(', ') : '—';
        const wm = document.getElementById('welcome-modules');
        if (wm) {
          wm.innerHTML = mods.map(m => '<span class="module-chip">🧩 ' + m + '</span>').join('');
        }
      }
      if (data.context_tokens !== undefined && data.max_context_tokens) {
        const pct = Math.min(1, data.context_tokens / data.max_context_tokens);
        const n = Math.round(pct * 10);
        document.getElementById('status-prog-blocks').textContent =
          '■'.repeat(n) + '⬝'.repeat(10 - n);
        document.getElementById('status-prog-pct').textContent = Math.round(pct * 100) + '%';
      }
      if (data.engine === 'running') {
        updateStatus('Ready', 'online');
        document.getElementById('api-notice').className = 'config-notice ok';
        document.getElementById('api-notice').innerHTML = '✓ API configured';
      }
    } catch (e) {
      updateStatus('Disconnected', 'offline');
    }
  }
  async function pollSessions() {
    try {
      const resp = await fetch('/api/sessions');
      const data = await resp.json();
      const el = document.getElementById('session-list');
      const sessions = data.sessions || [];
      if (sessions.length === 0) {
        el.innerHTML = '<div style="font-size:11px;color:var(--text-muted);padding:4px 8px;">No sessions.</div>';
      } else {
        el.innerHTML = sessions.map(s =>
          '<div class="session-item"><span class="session-time">' + (s.updated || '') + '</span><br>' + (s.turns || 0) + ' turns</div>'
        ).join('');
      }
    } catch (e) {}
  }
  async function pollRules() {
    try {
      const resp = await fetch('/api/rules');
      const data = await resp.json();
      const content = data.content || '';
      document.getElementById('rules-content').textContent = content || 'No rules loaded.';
      document.getElementById('rules-content').className = 'rules-content' + (content ? '' : ' empty');
    } catch (e) {}
  }

  setInterval(pollStatus, 5000);
  setInterval(pollSessions, 15000);
  pollStatus();
  pollSessions();
  pollRules();

  input.focus();
</script>
</body>
</html>"""


class WebUIServer:
    """Simple local web server for browser-based UI."""

    def __init__(
        self,
        engine: EngineLoop,
        tools: ToolRegistry,
        coordinator: AgentCoordinator,
        config: ZOUWUCODEConfig,
        port: int = 8080,
        host: str = "127.0.0.1",
        skills_manager=None,
        rule_loader=None,
        modules=None,
    ):
        self.engine = engine
        self.tools = tools
        self.coordinator = coordinator
        self.config = config
        self.port = port
        self.host = host
        self._server = None
        self._skills = skills_manager
        self._rules = rule_loader
        self.modules = modules
        # opencode-style tool-call presentation ("→ Read file …")
        self._tool_buffer: list[dict] = []
        # The engine owns a single PrefixCache and single-slot streaming
        # callbacks — only one chat turn may run at a time.
        self._chat_busy = False
        if self.engine is not None:
            self.engine.on_tool_event = self._on_tool_event

    def _on_tool_event(self, tool_call) -> None:
        """Buffer tool calls for the current /api/chat request."""
        try:
            name = getattr(tool_call, "name", "tool")
            args = getattr(tool_call, "arguments", "") or ""
            if isinstance(args, dict):
                args = json.dumps(args, ensure_ascii=False)
            self._tool_buffer.append({"name": name, "arguments": str(args)[:200]})
        except Exception:
            pass

    async def _sse_send(self, writer, obj: dict) -> None:
        """Write one SSE `data:` event, swallowing broken-pipe errors."""
        try:
            payload = json.dumps(obj, ensure_ascii=False)
            writer.write(f"data: {payload}\n\n".encode("utf-8"))
            await writer.drain()
        except Exception:
            pass

    async def _handle_chat_stream(self, writer, body: str) -> None:
        """SSE streaming chat handler — streams thinking deltas live.

        Events:
          {"type": "thinking", "delta": "..."}  incremental reasoning text
          {"type": "done", ...}                 final content / tools / stats
          {"type": "error", "error": "..."}     turn failed
        """
        try:
            data = json.loads(body)
            message = data.get("message", "")
        except json.JSONDecodeError:
            await self._sse_send(writer, {"type": "error", "error": "Invalid JSON"})
            writer.close()
            return

        # One chat turn at a time — the engine is a shared singleton.
        if self._chat_busy:
            await self._sse_send(writer, {
                "type": "error",
                "error": "另一个任务正在执行中，请等待完成或先按 Esc 打断当前任务。",
            })
            writer.close()
            return
        self._chat_busy = True

        # SSE headers — no Content-Length, connection stays open while streaming
        writer.write((
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: text/event-stream\r\n"
            "Cache-Control: no-cache\r\n"
            "Connection: keep-alive\r\n"
            "Access-Control-Allow-Origin: *\r\n"
            "\r\n"
        ).encode("utf-8"))
        await writer.drain()

        # Modules may intercept messages ("ultrawork …" / "ulw …" prefix)
        self._tool_buffer.clear()
        if self.modules is not None:
            module_result = await self.modules.dispatch_message(message)
            if module_result is not None:
                await self._sse_send(writer, {
                    "type": "done",
                    "module": module_result.get("title", "module"),
                    "content": module_result.get("content", ""),
                    "plan": module_result.get("plan_name"),
                    "tasks_completed": module_result.get("tasks_completed"),
                    "tasks_total": module_result.get("tasks_total"),
                })
                writer.close()
                return

        queue: asyncio.Queue = asyncio.Queue()
        show_thinking = getattr(self.config, "show_thinking", True)

        def _on_thinking(delta: str) -> None:
            """Engine callback (sync, same event loop) → SSE queue."""
            if show_thinking:
                try:
                    queue.put_nowait(delta)
                except Exception:
                    pass

        self.engine.on_thinking_delta = _on_thinking
        try:
            pm = getattr(self.coordinator, "project_memory", None)
            project_ctx = pm.get_full_context() if pm else ""
            messages = []
            if project_ctx:
                messages.append({"role": "system", "content": project_ctx})
            messages.append({"role": "user", "content": message})

            task = asyncio.create_task(self.engine.run(
                messages=messages,
                tools=self.tools.get_schemas(),
            ))

            # Drain the delta queue until the turn completes
            while True:
                if task.done() and queue.empty():
                    break
                try:
                    delta = await asyncio.wait_for(queue.get(), timeout=0.2)
                    await self._sse_send(writer, {"type": "thinking", "delta": delta})
                except asyncio.TimeoutError:
                    continue

            response = task.result()
            await self._sse_send(writer, {
                "type": "done",
                "content": response.content,
                "thinking": response.thinking,
                "tools": list(self._tool_buffer),
                "cache_hit": response.cache_hit,
                "stats": f"Cache: {self.engine.stats.hit_rate * 100:.0f}% | Cost: ${self.engine.stats.total_cost:.4f}",
            })
        except TaskInterrupted as e:
            # User interrupted this turn — tell the client clearly (with
            # recovery options) instead of reporting a generic error.
            await self._sse_send(writer, {
                "type": "interrupted",
                "message": str(e),
            })
        except Exception as e:
            await self._sse_send(writer, {"type": "error", "error": str(e)})
        finally:
            self._chat_busy = False
            self.engine.on_thinking_delta = None
            writer.close()

    async def handle_request(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Handle an HTTP request."""
        # Read the full request. The body may arrive in a later packet than
        # the headers, so keep reading until Content-Length bytes are received.
        request_data = b""
        try:
            while True:
                chunk = await asyncio.wait_for(reader.read(65536), timeout=30)
                if not chunk:
                    break
                request_data += chunk
                if b"\r\n\r\n" in request_data:
                    head, _, _ = request_data.partition(b"\r\n\r\n")
                    clen = 0
                    for line in head.decode("utf-8", "replace").split("\r\n"):
                        if line.lower().startswith("content-length:"):
                            try:
                                clen = int(line.split(":", 1)[1].strip())
                            except ValueError:
                                clen = 0
                    if len(request_data) >= len(head) + 4 + clen:
                        break
        except (asyncio.TimeoutError, Exception):
            writer.close()
            return

        request_str = request_data.decode("utf-8", errors="replace")

        if not request_str:
            writer.close()
            return

        lines = request_str.split("\r\n")
        if not lines:
            writer.close()
            return

        method_path = lines[0].split()
        if len(method_path) < 2:
            writer.close()
            return

        method = method_path[0]
        path = method_path[1]

        # Read body
        body = ""
        body_start = request_str.find("\r\n\r\n")
        if body_start >= 0:
            body = request_str[body_start + 4:]

        # Streaming SSE endpoint — writes chunks incrementally, keeps the
        # connection open until the turn finishes (opencode-style thinking).
        if path == "/api/chat/stream" and method == "POST":
            await self._handle_chat_stream(writer, body)
            return

        response = await self._route(method, path, body)

        writer.write(response.encode("utf-8"))
        await writer.drain()
        writer.close()

    async def _route(self, method: str, path: str, body: str) -> str:
        """Route an HTTP request to the appropriate handler."""
        if path == "/" or path == "/index.html":
            return self._html_response(HTML_TEMPLATE)

        elif path == "/api/chat" and method == "POST":
            try:
                data = json.loads(body)
                message = data.get("message", "")
            except json.JSONDecodeError:
                return self._json_response({"error": "Invalid JSON"}, 400)

            # Modules may intercept messages ("ultrawork …" / "ulw …" prefix)
            self._tool_buffer.clear()
            if self.modules is not None:
                module_result = await self.modules.dispatch_message(message)
                if module_result is not None:
                    return self._json_response({
                        "content": module_result.get("content", ""),
                        "details": module_result.get("details") or [],
                        "plan": module_result.get("plan_name"),
                        "tasks_completed": module_result.get("tasks_completed"),
                        "tasks_total": module_result.get("tasks_total"),
                        "module": module_result.get("title", "module"),
                    })

            # One chat turn at a time — the engine is a shared singleton.
            if self._chat_busy:
                return self._json_response(
                    {"error": "另一个任务正在执行中，请等待完成或先按 Esc 打断当前任务。"},
                    409,
                )
            self._chat_busy = True
            try:
                project_ctx = self.coordinator.project_memory.get_full_context() if hasattr(self.coordinator, 'project_memory') else ""

                messages = []
                if project_ctx:
                    messages.append({"role": "system", "content": project_ctx})
                messages.append({"role": "user", "content": message})

                response = await self.engine.run(
                    messages=messages,
                    tools=self.tools.get_schemas(),
                )

                return self._json_response({
                    "content": response.content,
                    "thinking": response.thinking,
                    "tools": list(self._tool_buffer),
                    "cache_hit": response.cache_hit,
                    "stats": f"Cache: {self.engine.stats.hit_rate * 100:.0f}% | Cost: ${self.engine.stats.total_cost:.4f}",
                })
            except Exception as e:
                err_msg = str(e)
                tb = traceback.format_exc()
                return self._json_response({"error": err_msg, "detail": tb}, 500)
            finally:
                self._chat_busy = False

        elif path == "/api/interrupt" and method == "POST":
            # Interrupt the currently running task (TUI/WebUI parity).
            try:
                ok = self.engine.request_interrupt(reason="WebUI interrupt button")
            except Exception as e:
                return self._json_response({"error": str(e)}, 500)
            if ok:
                return self._json_response({"interrupting": True})
            return self._json_response(
                {"interrupting": False, "message": "当前没有正在执行的任务"}, 409
            )

        elif path == "/api/mode" and method == "POST":
            try:
                data = json.loads(body)
                mode = data.get("mode", "agent")
                self.engine.set_mode(mode)
                return self._json_response({"mode": mode})
            except Exception as e:
                return self._json_response({"error": str(e)}, 400)

        elif path == "/api/reasoning" and method == "POST":
            try:
                data = json.loads(body)
                level = data.get("reasoning", "medium")
                self.config.reasoning_intensity = level
                self.engine.set_reasoning_intensity(level)
                return self._json_response({"reasoning": level})
            except Exception as e:
                return self._json_response({"error": str(e)}, 400)

        elif path == "/api/thinking" and method == "POST":
            # Toggle live thinking display (mirrors /thinking on CLI/TUI)
            self.config.show_thinking = not getattr(self.config, "show_thinking", True)
            return self._json_response({"show_thinking": self.config.show_thinking})

        elif path == "/api/cache" and method == "GET":
            return self._json_response(self.engine.get_cache_summary())

        elif path == "/api/status" and method == "GET":
            skills_loaded = list(self._skills.list_loaded()) if self._skills else []
            has_rules = self._rules.exists() if self._rules else False
            # Context token estimate from engine stats
            ctx_tokens = getattr(getattr(self.engine, "stats", None),
                                 "total_prompt_tokens", 0)
            return self._json_response({
                "mode": self.engine.mode,
                "running": self.engine.is_running,
                "reasoning": self.config.reasoning_intensity,
                "show_thinking": getattr(self.config, "show_thinking", True),
                "engine": "running",
                "cache_hit_rate": f"{self.engine.stats.hit_rate * 100:.1f}%",
                "total_requests": self.engine.stats.total_requests,
                "total_cost": f"${self.engine.stats.total_cost:.4f}",
                "context_tokens": ctx_tokens,
                "max_context_tokens": 1048576,
                "rules_loaded": has_rules,
                "skills_loaded": skills_loaded,
                "modules": self.modules.loaded() if self.modules else [],
            })

        elif path == "/api/rules" and method == "GET":
            if self._rules:
                return self._json_response({"content": self._rules.load()})
            return self._json_response({"content": ""})

        elif path == "/api/skills" and method == "GET":
            if self._skills:
                return self._json_response({
                    "available": self._skills.list_available(),
                    "loaded": self._skills.list_loaded(),
                })
            return self._json_response({"available": [], "loaded": []})

        elif path == "/api/skills" and method == "POST":
            try:
                data = json.loads(body)
                action = data.get("action", "")
                name = data.get("name", "")
                if not self._skills:
                    return self._json_response({"error": "Skills system not available"}, 400)
                if action == "load":
                    self._skills.load(name)
                    return self._json_response({"success": True, "loaded": self._skills.list_loaded()})
                elif action == "unload":
                    self._skills.unload(name)
                    return self._json_response({"success": True, "loaded": self._skills.list_loaded()})
                else:
                    return self._json_response({"error": f"Unknown action: {action}"}, 400)
            except Exception as e:
                return self._json_response({"error": str(e)}, 400)

        elif path == "/api/sessions" and method == "GET":
            sessions_list = []
            if hasattr(self.engine, 'sessions'):
                for s in self.engine.sessions.list_sessions():
                    sessions_list.append({
                        "id": s.get("id", ""),
                        "turns": s.get("turns", 0),
                        "updated": time.strftime("%Y-%m-%d %H:%M", time.localtime(s.get("updated", 0))),
                    })
            return self._json_response({"sessions": sessions_list})

        elif path == "/api/hello-my-zouwucode" and method == "POST":
            if self.modules is None:
                return self._json_response(
                    {"error": "No modules loaded"}, 400)
            module = self.modules.module("hello-my-zouwucode") if self.modules else None
            if module is None:
                return self._json_response(
                    {"error": "hello-my-zouwucode module is not loaded (config.hello_my_zouwucode.enabled=false)"}, 400)
            try:
                data = json.loads(body)
                action = data.get("action", "")
                task = data.get("task", "")

                if action == "ultrawork":
                    if not task:
                        return self._json_response({"error": "Missing task"}, 400)
                    result = await module.handle_command("/hello-ultrawork", task)
                elif action == "plan":
                    if not task:
                        return self._json_response({"error": "Missing task"}, 400)
                    result = await module.handle_command("/hello-plan", task)
                elif action == "start-work":
                    result = await module.handle_command("/hello-start-work")
                elif action == "status":
                    from hello_my_zouwucode.notepad import Notepad

                    payload = module.status_payload()
                    plan_name = payload.get("plan")
                    notepad_summary = ""
                    if plan_name:
                        notepad_summary = Notepad(plan_name, module.orchestrator.omo_dir).summary()
                    return self._json_response({
                        "boulder": payload.get("boulder", ""),
                        "plan_name": plan_name,
                        "notepad": notepad_summary,
                        "agents": payload.get("agents", 0),
                        "categories": payload.get("categories", 0),
                    })
                else:
                    return self._json_response({"error": f"Unknown action: {action}"}, 400)

                return self._json_response({
                    "success": True,
                    "content": (result or {}).get("content", ""),
                    "details": (result or {}).get("details") or [],
                    "plan": (result or {}).get("plan_name"),
                    "tasks_completed": (result or {}).get("tasks_completed"),
                    "tasks_total": (result or {}).get("tasks_total"),
                })
            except Exception as e:
                return self._json_response({"error": str(e)}, 500)

        return self._html_response("<h1>404 Not Found</h1>", 404)

    def _html_response(self, content: str, status: int = 200) -> str:
        return (
            f"HTTP/1.1 {status} {'OK' if status == 200 else 'Not Found'}\r\n"
            f"Content-Type: text/html; charset=utf-8\r\n"
            f"Content-Length: {len(content.encode('utf-8'))}\r\n"
            f"Access-Control-Allow-Origin: *\r\n"
            f"\r\n"
            f"{content}"
        )

    def _json_response(self, data: dict, status: int = 200) -> str:
        content = json.dumps(data, ensure_ascii=False)
        return (
            f"HTTP/1.1 {status} {'OK' if status == 200 else 'Error'}\r\n"
            f"Content-Type: application/json; charset=utf-8\r\n"
            f"Content-Length: {len(content.encode('utf-8'))}\r\n"
            f"Access-Control-Allow-Origin: *\r\n"
            f"\r\n"
            f"{content}"
        )

    async def start(self):
        """Start the web server."""
        self._server = await asyncio.start_server(
            self.handle_request,
            self.host,
            self.port,
        )
        addr = self._server.sockets[0].getsockname()
        print(f"\n  🌐 ZOUWUCODE Web UI: http://{addr[0]}:{addr[1]}")
        print(f"  Press Ctrl+C to stop\n")

        async with self._server:
            await self._server.serve_forever()

    async def stop(self):
        """Stop the web server."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
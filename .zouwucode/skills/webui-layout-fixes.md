# WebUI 布局与样式修复指南

> 本 skill 沉淀 ZOUWUCODE Web UI（`zouwucode/webui/server.py` 内嵌 CSS/HTML）布局与样式问题的排查与修复经验。适用于：WebUI 出现文字变形、溢出、被裁剪、重叠、响应式异常等视觉问题。

## 一、高危陷阱：ASCII 艺术字换行被折叠（纵向压缩）

**现象**：欢迎屏等多行 ASCII 艺术字横幅被"纵向压缩"成 2~3 行、字符交错重叠、难以辨认。

**根因**：CSS 缺 `white-space: pre`。浏览器默认 `white-space: normal` 会把文本内的 `\n` 折叠成空格，多行横幅被当作连续文本自动折行。

**修复**：

```css
.banner-art {
  white-space: pre;      /* 保留换行结构 */
  max-width: 100%;       /* 窄屏不撑破容器 */
  overflow-x: auto;      /* 极窄屏可横向滚动兜底 */
}
```

**注意**：HTML 中横幅首行前、末行后的换行/缩进空格在 `pre` 下都会成为空行。首行应紧贴 `>` 标签，末行后紧跟 `</div>`。

**验证方法**：用 `Range.getClientRects()` 统计行盒数量——应为艺术字行数，而非折叠后的行数；`offsetHeight` 应 ≈ 行数 × line-height。

## 二、响应式断点

`body { height: 100vh; overflow: hidden; }` 会把横向溢出的内容直接裁掉（不产生滚动条），因此窄屏必须显式处理：

```css
@media (max-width: 560px) {
  #header { flex-wrap: wrap; height: auto; min-height: 52px; padding: 8px 16px; row-gap: 4px; }
  #header .header-right { margin-left: 0; }
  #status-bar { padding: 0 12px; }
  #chat { padding: 16px; }
  .msg { max-width: 96%; }
}
```

注意：`html { width: Xpx !important }` 这类注入法**无法激活** CSS 媒体查询（`matchMedia` 仍按真实视口判定），验证窄屏须用真实宽度 iframe 或 DevTools 视口。

## 三、长文本折行（overflow-wrap）

`white-space: pre-wrap` 只保留换行，**不会**折断超长无空格 token（URL、代码、长文件名）。需要叠加：

```css
overflow-wrap: anywhere;  /* 或 word-break: break-word */
```

配合 `max-height + overflow-y: auto` 时建议同时加 `overflow-x: hidden` 消除内部横滚条。

适用元素：规则内容（`.rules-content`）、会话名（`.session-item`）、消息体（`.msg`）。

## 四、可折叠块的滚动约束

思考块等 `details > div` 子体需显式限高，否则长内容会把整个聊天区撑破：

```css
.thinking-body { max-height: 240px; overflow-y: auto; }
```

## 五、浏览器实测清单（定位问题前先测）

1. `getComputedStyle(el).whiteSpace / overflow / transform / zoom` —— 排除折叠、裁剪、缩放。
2. `el.scrollWidth` vs `el.clientWidth`、`scrollHeight` vs `clientHeight` —— 找出溢出点。
3. `document.documentElement.scrollWidth` vs `window.innerWidth` —— 页面级横向滚动条。
4. 祖先链逐层检查 `transform` / `zoom` / `overflow`（`overflow: hidden` + 固定高度会静默裁剪）。
5. `document.fonts` + canvas 文本度量，确认实际生效字体与字形比例（排除字体回退问题）。

## 六、回归测试

`tests/test_webui.py` 通过正则解析 `HTML_TEMPLATE` 内嵌 CSS 断言关键属性存在（如 `white-space: pre`、`overflow-wrap: anywhere`、`@media (max-width: 560px)`），防止同类问题复发。修改 server.py 的 CSS 后必须同步更新这些断言。

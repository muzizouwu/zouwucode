"""Attachment intake — turn uploaded files into something an agent can use.

Why this exists: chat boxes are expected to accept images/files, but a coding
agent's value comes from *paths it can act on*. So every attachment is written
into the workspace first (``.zouwucode/uploads/``), and the prompt tells the
model where it landed. Vision-capable providers additionally get the image
inline as a ``data:`` URL; text-only providers (e.g. DeepSeek) still get the
file path instead of a silently dropped upload.
"""

import base64
import binascii
import re
import time
from pathlib import Path
from typing import Iterable

MAX_FILES = 5
MAX_BYTES = 8 * 1024 * 1024          # 8 MiB per file
UPLOAD_DIR = Path(".zouwucode") / "uploads"
IMAGE_MIME = {
    "image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif",
}
_SAFE = re.compile(r"[^A-Za-z0-9._\-一-龥]+")


def filename_for(name: str, index: int) -> str:
    """A safe, collision-free filename that keeps the original extension."""
    stem = Path(str(name or "file")).name
    suffix = Path(stem).suffix[:12]
    base = _SAFE.sub("_", Path(stem).stem)[:48].strip("_") or "file"
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{index}-{base}{suffix}"


def _decode(data: str) -> bytes:
    payload = data or ""
    if "," in payload and payload.lstrip().startswith("data:"):
        payload = payload.split(",", 1)[1]
    return base64.b64decode(payload, validate=False)


def save_attachments(
    items: Iterable[dict] | None,
    workspace: Path,
    *,
    subdir: Path = UPLOAD_DIR,
) -> tuple[list[dict], list[str]]:
    """Persist uploads; returns ``(saved, errors)``.

    Each ``saved`` entry is ``{"name", "path", "mime", "size", "relative"}``.
    Failures (too many, too big, bad base64, unwritable dir) are returned as
    readable strings instead of raising — one bad upload must not kill the turn.
    """
    entries = list(items or [])
    saved: list[dict] = []
    errors: list[str] = []
    if not entries:
        return saved, errors
    if len(entries) > MAX_FILES:
        errors.append(f"最多一次 {MAX_FILES} 个附件，已忽略多出的 {len(entries) - MAX_FILES} 个")
        entries = entries[:MAX_FILES]

    root = (Path(workspace).resolve() / subdir)
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return [], [f"无法创建上传目录：{exc}"]

    for index, item in enumerate(entries, start=1):
        if not isinstance(item, dict):
            errors.append("附件格式无效，已跳过")
            continue
        name = str(item.get("name") or "file")
        mime = str(item.get("mime") or "application/octet-stream")
        try:
            raw = _decode(str(item.get("data") or ""))
        except (binascii.Error, ValueError):
            errors.append(f"{name}: base64 解码失败，已跳过")
            continue
        if not raw:
            errors.append(f"{name}: 内容为空，已跳过")
            continue
        if len(raw) > MAX_BYTES:
            errors.append(f"{name}: 超过 {MAX_BYTES // (1024 * 1024)} MB，已跳过")
            continue
        target = root / filename_for(name, index)
        try:
            target.write_bytes(raw)
        except OSError as exc:
            errors.append(f"{name}: 写入失败（{exc}），已跳过")
            continue
        try:
            relative = target.relative_to(Path(workspace).resolve()).as_posix()
        except ValueError:
            relative = str(target)
        saved.append({
            "name": name, "path": str(target), "relative": relative,
            "mime": mime, "size": len(raw),
        })
    return saved, errors


def attachment_note(saved: list[dict], errors: list[str] | None = None) -> str:
    """Text appended to the user message so the model knows about the uploads."""
    lines = []
    for item in saved:
        lines.append(f"- {item['name']} → {item['relative']} ({item['mime']}, {item['size']} bytes)")
    for err in errors or []:
        lines.append(f"- ⚠ {err}")
    if not lines:
        return ""
    return ("\n\n[attachments — 已保存到工作区，可直接用 read/python_exec 等工具处理]\n"
            + "\n".join(lines))


def image_parts(saved: list[dict]) -> list[dict]:
    """Inline image content parts for vision-capable providers."""
    parts = []
    for item in saved:
        if item.get("mime") not in IMAGE_MIME:
            continue
        try:
            raw = Path(item["path"]).read_bytes()
        except OSError:
            continue
        encoded = base64.b64encode(raw).decode("ascii")
        parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:{item['mime']};base64,{encoded}"},
        })
    return parts

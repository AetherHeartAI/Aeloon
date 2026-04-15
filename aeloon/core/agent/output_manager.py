"""Centralized workspace output artifact manager.

Tracks files produced by the agent loop, plugins, and tools in a
structured ``outputs/`` directory with a JSONL manifest for discovery,
session correlation, and memory consolidation.

Also provides :class:`OutputTracker`, a lightweight ``file_policy``
callback that automatically records ``write_file`` / ``edit_file``
operations to the manifest for unified tracking.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from aeloon.utils.helpers import ensure_dir

if TYPE_CHECKING:
    from aeloon.core.session.manager import Session


_CATEGORY_BY_SUFFIX: dict[str, str] = {
    ".py": "code",
    ".js": "code",
    ".ts": "code",
    ".jsx": "code",
    ".tsx": "code",
    ".sh": "code",
    ".sql": "code",
    ".html": "code",
    ".css": "code",
    ".go": "code",
    ".rs": "code",
    ".java": "code",
    ".rb": "code",
    ".csv": "data",
    ".json": "data",
    ".jsonl": "data",
    ".ndjson": "data",
    ".xml": "data",
    ".yaml": "data",
    ".yml": "data",
    ".parquet": "data",
    ".png": "media",
    ".jpg": "media",
    ".jpeg": "media",
    ".svg": "media",
    ".gif": "media",
    ".webp": "media",
    ".mp3": "media",
    ".mp4": "media",
}

_REPORT_KEYWORDS = frozenset({"report", "summary", "analysis", "survey", "review", "digest"})

_SYSTEM_PREFIXES = ("sessions", "memory", "skills", "compiled_skills", ".aeloon")


def _slugify(raw: str, max_len: int = 72) -> str:
    """Turn a human title into a filename-safe slug (Unicode allowed)."""
    s = raw.strip().replace("\n", " ")
    s = re.sub(r'[/\\<>:"|?*\x00-\x1f]', "_", s)
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"_+", "_", s).strip("._ ")
    if len(s) > max_len:
        s = s[:max_len].rstrip("._ ")
    return s or "output"


def _infer_category(rel: str) -> str:
    """Infer an output category from a workspace-relative path string."""
    parts = rel.split("/")
    if len(parts) >= 2 and parts[0] == "outputs":
        return parts[1]

    suffix = Path(rel).suffix.lower()
    if suffix in _CATEGORY_BY_SUFFIX:
        return _CATEGORY_BY_SUFFIX[suffix]

    if suffix in (".md", ".txt", ".rst"):
        stem = Path(rel).stem.lower()
        if any(kw in stem for kw in _REPORT_KEYWORDS):
            return "reports"
        return "docs"

    return "misc"


class OutputManager:
    """Manage structured output artifacts under ``<workspace>/outputs/``."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.expanduser().resolve()
        self.outputs_dir = ensure_dir(self.workspace / "outputs")
        self.manifest_path = self.outputs_dir / "manifest.jsonl"

    def save_output(
        self,
        content: str,
        *,
        title: str,
        category: str = "general",
        filename: str | None = None,
        suffix: str = ".md",
        session: Session | None = None,
        extra_meta: dict[str, Any] | None = None,
    ) -> Path:
        """Write *content* to ``outputs/<category>/<filename>`` and record it."""
        cat_dir = ensure_dir(self.outputs_dir / category)
        if filename is None:
            slug = _slugify(title)
            ts_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{slug}_{ts_tag}{suffix}"
        dest = cat_dir / filename
        dest.write_text(content, encoding="utf-8")

        rel_path = str(dest.relative_to(self.workspace))
        ts_iso = datetime.now().isoformat()

        record: dict[str, Any] = {
            "ts": ts_iso,
            "category": category,
            "title": title,
            "path": rel_path,
        }
        if session is not None:
            record["session"] = session.key
        if extra_meta:
            record.update(extra_meta)

        self._append_manifest(record)

        if session is not None:
            session.metadata.setdefault("outputs", []).append(
                {
                    "path": rel_path,
                    "category": category,
                    "title": title,
                    "ts": ts_iso,
                }
            )

        logger.debug("Output saved: {} → {}", title, rel_path)
        return dest

    def list_recent(self, limit: int = 10) -> list[dict[str, Any]]:
        """Return the *limit* most recent manifest entries (newest first)."""
        if not self.manifest_path.exists():
            return []
        entries: list[dict[str, Any]] = []
        try:
            for line in self.manifest_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        except Exception:
            logger.warning("Failed to parse outputs manifest")
            return []
        return list(reversed(entries[-limit:]))

    def build_output_summary(
        self,
        session: Session | None = None,
        limit: int = 20,
    ) -> str:
        """Build a short markdown summary of outputs for memory consolidation."""
        items: list[dict[str, Any]]
        if session is not None:
            items = session.metadata.get("outputs", [])
        else:
            items = self.list_recent(limit=limit)

        if not items:
            return ""

        lines = []
        for item in items[-limit:]:
            path = item.get("path", "?")
            title = item.get("title", "")
            lines.append(f'- {path} — "{title}"')
        return "\n".join(lines)

    async def promote_to_wiki(self, output_path: Path, wiki_plugin: Any) -> str:
        """Import an output artifact into the Wiki knowledge base."""
        resolved = output_path if output_path.is_absolute() else (self.workspace / output_path)
        if not resolved.exists():
            return f"Error: file not found — {resolved}"

        ingest = getattr(wiki_plugin, "_ingest_service", None)
        if ingest is None:
            return "Error: Wiki plugin ingest service is not available."

        try:
            await ingest.ingest_file(resolved)
        except Exception as exc:
            logger.warning("Wiki ingest failed for {}: {}", resolved, exc)
            return f"Error: Wiki ingest failed — {exc}"

        self._update_manifest_field(str(resolved), "promoted_to_wiki", True)
        return f"Promoted `{resolved.name}` to Wiki knowledge base."

    def _append_manifest(self, record: dict[str, Any]) -> None:
        """Append one JSON record to ``manifest.jsonl``."""
        try:
            with open(self.manifest_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            logger.warning("Failed to append to outputs manifest")

    def record_tool_write(self, path: Path, op: str = "write") -> None:
        """Record a file written by ``write_file`` / ``edit_file`` to the manifest."""
        rel = self._try_relative(path)
        if rel is None or self._is_system_path(rel):
            return
        self._append_manifest(
            {
                "ts": datetime.now().isoformat(),
                "category": _infer_category(rel),
                "title": Path(rel).name,
                "path": rel,
                "source": "tool",
                "op": op,
            }
        )

    def _try_relative(self, path: Path) -> str | None:
        """Return workspace-relative string, or *None* if outside workspace."""
        try:
            return str(path.resolve().relative_to(self.workspace))
        except ValueError:
            return None

    @staticmethod
    def _is_system_path(rel: str) -> bool:
        """Return *True* for workspace-internal system directories."""
        return any(rel.startswith(prefix) for prefix in _SYSTEM_PREFIXES)

    def _update_manifest_field(self, path_str: str, key: str, value: Any) -> None:
        """In-place update of the *last* manifest entry matching *path_str*."""
        if not self.manifest_path.exists():
            return
        try:
            lines = self.manifest_path.read_text(encoding="utf-8").splitlines()
            updated = False
            for i in range(len(lines) - 1, -1, -1):
                line = lines[i].strip()
                if not line:
                    continue
                record = json.loads(line)
                if record.get("path", "").endswith(path_str) or path_str.endswith(
                    record.get("path", "\x00")
                ):
                    record[key] = value
                    lines[i] = json.dumps(record, ensure_ascii=False)
                    updated = True
                    break
            if updated:
                self.manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except Exception:
            logger.warning("Failed to update manifest field {} for {}", key, path_str)


class OutputTracker:
    """``file_policy`` callback that records tool writes to the manifest."""

    def __init__(self, output_manager: OutputManager) -> None:
        self._om = output_manager

    async def before_operation(
        self,
        op: str,
        target: str,
        **kwargs: Any,
    ) -> str | None:
        return None

    async def after_operation(
        self,
        op: str,
        target: str,
        result: str,
        **kwargs: Any,
    ) -> str:
        if op in ("write", "edit") and not result.startswith("Error"):
            try:
                self._om.record_tool_write(Path(target), op)
            except Exception:
                logger.debug("OutputTracker: failed to record {} on {}", op, target)
        return result

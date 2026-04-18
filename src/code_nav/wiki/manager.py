"""WikiManager — 三级 wiki 读写、路径映射。"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class WikiContent:
    level: str  # "project" | "package" | "module"
    path: str  # wiki 文件路径（相对于 project root）
    exists: bool
    content: str
    last_modified: float | None  # mtime
    is_stale: bool = False
    stale_reason: str = ""


class WikiManager:
    """三级 Wiki 的读写和路径管理。"""

    def __init__(self, project_path: str):
        self._project_path = project_path
        self._wiki_dir = os.path.join(project_path, ".code-nav", "wiki")

    def _resolve_path(
        self,
        module: str = "",
        package: str = "",
        level: str = "",
    ) -> tuple[str, str]:
        """解析 wiki 文件路径，返回 (absolute_path, level)。"""
        if level == "project" or (not module and not package and not level):
            return os.path.join(self._wiki_dir, "_project.md"), "project"

        if package or level == "package":
            pkg = package.replace(".", os.sep)
            return os.path.join(self._wiki_dir, pkg, "_package.md"), "package"

        if module or level == "module":
            # "util/odps_util.py" → "util/odps_util.md"
            mod = module.replace(".py", ".md")
            return os.path.join(self._wiki_dir, mod), "module"

        return os.path.join(self._wiki_dir, "_project.md"), "project"

    def read(
        self,
        module: str = "",
        package: str = "",
        level: str = "",
    ) -> WikiContent:
        """读取 wiki 内容。"""
        abs_path, resolved_level = self._resolve_path(module, package, level)
        rel_path = os.path.relpath(abs_path, self._project_path)

        if not os.path.exists(abs_path):
            return WikiContent(
                level=resolved_level,
                path=rel_path,
                exists=False,
                content="",
                last_modified=None,
            )

        content = Path(abs_path).read_text(encoding="utf-8")
        mtime = os.path.getmtime(abs_path)

        wc = WikiContent(
            level=resolved_level,
            path=rel_path,
            exists=True,
            content=content,
            last_modified=mtime,
        )

        # stale 检测
        if resolved_level == "module" and module:
            code_file = os.path.join(self._project_path, module)
            if os.path.exists(code_file):
                code_mtime = os.path.getmtime(code_file)
                if code_mtime > mtime:
                    wc.is_stale = True
                    wc.stale_reason = (
                        f"code changed since wiki was written "
                        f"(code: {time.strftime('%Y-%m-%d', time.localtime(code_mtime))}, "
                        f"wiki: {time.strftime('%Y-%m-%d', time.localtime(mtime))})"
                    )
        elif resolved_level == "package" and package:
            is_stale, reason = self.check_stale_package(package)
            if is_stale:
                wc.is_stale = True
                wc.stale_reason = reason

        return wc

    def write(
        self,
        content: str,
        module: str = "",
        package: str = "",
        level: str = "",
    ) -> str:
        """写入 wiki 内容，返回写入的文件路径（相对于 project root）。"""
        abs_path, _ = self._resolve_path(module, package, level)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        Path(abs_path).write_text(content, encoding="utf-8")
        return os.path.relpath(abs_path, self._project_path)

    def check_stale(self, module_file: str) -> tuple[bool, str]:
        """检查模块级 wiki 是否过时。返回 (is_stale, reason)。"""
        abs_wiki, _ = self._resolve_path(module=module_file)
        if not os.path.exists(abs_wiki):
            return False, ""

        code_path = os.path.join(self._project_path, module_file)
        if not os.path.exists(code_path):
            return False, ""

        code_mtime = os.path.getmtime(code_path)
        wiki_mtime = os.path.getmtime(abs_wiki)

        if code_mtime > wiki_mtime:
            reason = (
                f"code changed since wiki was written "
                f"(code: {time.strftime('%Y-%m-%d', time.localtime(code_mtime))}, "
                f"wiki: {time.strftime('%Y-%m-%d', time.localtime(wiki_mtime))})"
            )
            return True, reason
        return False, ""

    def check_stale_package(self, package: str) -> tuple[bool, str]:
        """检查 package 级 wiki 是否过时。

        扫描该 package 对应目录下所有 .py 文件，取最大 mtime 与 _package.md 比较。
        """
        abs_wiki, _ = self._resolve_path(package=package)
        if not os.path.exists(abs_wiki):
            return False, ""

        # package "src.code_nav.engines" → 目录 "src/code_nav/engines"
        pkg_dir = os.path.join(
            self._project_path, package.replace(".", os.sep)
        )
        if not os.path.isdir(pkg_dir):
            return False, ""

        wiki_mtime = os.path.getmtime(abs_wiki)
        max_code_mtime = 0.0
        for entry in os.scandir(pkg_dir):
            if entry.is_file() and entry.name.endswith(".py"):
                max_code_mtime = max(max_code_mtime, entry.stat().st_mtime)

        if max_code_mtime > wiki_mtime:
            reason = (
                f"code changed since package wiki was written "
                f"(latest code: {time.strftime('%Y-%m-%d', time.localtime(max_code_mtime))}, "
                f"wiki: {time.strftime('%Y-%m-%d', time.localtime(wiki_mtime))})"
            )
            return True, reason
        return False, ""

    def get_wiki_status(self, module_file: str) -> dict:
        """返回模块的 wiki 状态字典（供 query_module 使用）。"""
        abs_wiki, _ = self._resolve_path(module=module_file)
        rel_path = os.path.relpath(abs_wiki, self._project_path)

        if not os.path.exists(abs_wiki):
            return {"status": "not_generated"}

        is_stale, reason = self.check_stale(module_file)
        if is_stale:
            return {"status": "stale", "path": rel_path, "stale_reason": reason}

        return {"status": "available", "path": rel_path}

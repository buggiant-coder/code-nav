"""ast-grep (sg) 引擎封装。

通过 subprocess 调用 sg CLI，解析 JSON 输出。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path

from code_nav.engines.base import AnalysisEngine, PatternSearchEngine
from code_nav.models import (
    Reference, Definition, SymbolInfo, AstMatch,
    RefType, Confidence, SymbolType, SymbolScope,
)

logger = logging.getLogger(__name__)

# 文件扩展名 → ast-grep 语言标识
_EXT_TO_LANG: dict[str, str] = {
    ".py": "python", ".pyi": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "tsx", ".jsx": "jsx",
    ".go": "go", ".rs": "rust",
    ".java": "java", ".kt": "kotlin",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp", ".cc": "cpp",
}


class SgEngine(AnalysisEngine, PatternSearchEngine):
    """ast-grep 分析引擎。"""

    def __init__(self, sg_path: str = "sg"):
        self._sg_path = sg_path

    @property
    def name(self) -> str:
        return "ast-grep"

    @property
    def supported_languages(self) -> list[str]:
        return ["*"]

    # ----------------------------------------------------------------
    # PatternSearchEngine
    # ----------------------------------------------------------------

    async def search(
        self,
        pattern: str,
        language: str,
        path: str = ".",
        limit: int = 50,
    ) -> list[AstMatch]:
        raw = await self._run_sg(
            ["run", "--pattern", pattern, "--lang", language, "--json", path]
        )
        matches = self._parse_matches(raw)
        return matches[:limit]

    # ----------------------------------------------------------------
    # AnalysisEngine
    # ----------------------------------------------------------------

    async def find_references(
        self, file: str, line: int, column: int = 0,
    ) -> list[Reference]:
        symbol = await self._extract_symbol_at(file, line, column)
        if not symbol:
            return []
        language = self.detect_language(file)
        # 搜索范围限定到文件所在的项目根目录
        search_root = self._find_project_root(file)
        # 搜索函数/方法调用
        call_matches = await self.search(f"{symbol}($$$)", language, path=search_root)
        # 搜索直接名字引用（import、赋值等）
        name_matches = await self.search(symbol, language, path=search_root)
        # 合并去重
        seen: set[tuple[str, int]] = set()
        refs: list[Reference] = []
        for m in call_matches:
            key = (m.file, m.line)
            if key not in seen:
                seen.add(key)
                refs.append(self._match_to_reference(m, RefType.CALL))
        for m in name_matches:
            key = (m.file, m.line)
            if key not in seen:
                seen.add(key)
                refs.append(self._match_to_reference(m, RefType.OTHER))
        return refs

    async def go_to_definition(
        self, file: str, line: int, column: int = 0,
    ) -> list[Definition]:
        symbol = await self._extract_symbol_at(file, line, column)
        if not symbol:
            return []
        language = self.detect_language(file)
        search_root = self._find_project_root(file)
        patterns = self._build_definition_patterns(symbol, language)
        results: list[Definition] = []
        seen: set[tuple[str, int]] = set()
        for pat in patterns:
            matches = await self.search(pat, language, path=search_root)
            for m in matches:
                key = (m.file, m.line)
                if key not in seen:
                    seen.add(key)
                    results.append(self._match_to_definition(m))
        return results

    async def get_symbol_info(
        self, file: str, line: int, column: int = 0,
    ) -> SymbolInfo | None:
        symbol = await self._extract_symbol_at(file, line, column)
        if not symbol:
            return None
        line_text = self._read_line(file, line)
        symbol_type = self._guess_symbol_type(line_text, symbol)
        scope = self._guess_scope(symbol)
        return SymbolInfo(
            name=symbol,
            symbol_type=symbol_type,
            file=file,
            line=line,
            scope=scope,
        )

    # ----------------------------------------------------------------
    # 内部方法
    # ----------------------------------------------------------------

    @staticmethod
    def _find_project_root(file: str) -> str:
        """从文件路径向上查找项目根目录。"""
        markers = {"pyproject.toml", "setup.py", "setup.cfg", "package.json",
                    "go.mod", ".git", "requirements.txt", "Makefile"}
        path = Path(file).resolve().parent
        while path != path.parent:
            if any((path / m).exists() for m in markers):
                return str(path)
            path = path.parent
        # 找不到项目根，返回文件所在目录
        return str(Path(file).resolve().parent)

    async def _run_sg(self, args: list[str]) -> list[dict]:
        try:
            proc = await asyncio.create_subprocess_exec(
                self._sg_path, *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
        except FileNotFoundError:
            logger.error(
                "ast-grep (sg) not found. Install it: brew install ast-grep"
            )
            return []
        if proc.returncode != 0:
            logger.warning("sg failed (rc=%d): %s", proc.returncode, stderr.decode())
            return []
        if not stdout.strip():
            return []
        try:
            return json.loads(stdout.decode())
        except json.JSONDecodeError:
            logger.warning("sg returned invalid JSON: %s", stdout[:200])
            return []

    def _parse_matches(self, raw: list[dict]) -> list[AstMatch]:
        results: list[AstMatch] = []
        for item in raw:
            range_info = item.get("range", {})
            start = range_info.get("start", {})
            # ast-grep 的 metaVariables 可能是 dict 或 list
            meta = item.get("metaVariables", {})
            captures: dict[str, str] = {}
            if isinstance(meta, dict):
                for k, v in meta.items():
                    if isinstance(v, dict):
                        captures[k] = v.get("text", "")
                    elif isinstance(v, list) and v:
                        captures[k] = ", ".join(
                            sub.get("text", "") for sub in v if isinstance(sub, dict)
                        )
            results.append(AstMatch(
                file=item.get("file", ""),
                line=start.get("line", 0) + 1,  # ast-grep 0-based → 1-based
                column=start.get("column", 0),
                matched_text=item.get("text", ""),
                captures=captures,
            ))
        return results

    def _match_to_reference(self, m: AstMatch, ref_type: RefType) -> Reference:
        return Reference(
            file=m.file, line=m.line, column=m.column,
            context=m.matched_text,
            ref_type=ref_type,
            source=self.name,
            confidence=Confidence.MEDIUM,
        )

    def _match_to_definition(self, m: AstMatch) -> Definition:
        return Definition(
            file=m.file, line=m.line, column=m.column,
            context=m.matched_text,
            source=self.name,
            confidence=Confidence.MEDIUM,
        )

    async def _extract_symbol_at(
        self, file: str, line: int, column: int
    ) -> str | None:
        text = self._read_line(file, line)
        if not text:
            return None
        # 如果指定了 column，从 column 位置提取标识符
        if column > 0 and column < len(text):
            # 向前后扩展，找到完整标识符
            start = column
            while start > 0 and (text[start - 1].isalnum() or text[start - 1] == "_"):
                start -= 1
            end = column
            while end < len(text) and (text[end].isalnum() or text[end] == "_"):
                end += 1
            token = text[start:end]
            if token:
                return token
        # 否则用启发式提取该行的主要符号
        return self._extract_main_symbol(text)

    def _read_line(self, file: str, line: int) -> str:
        try:
            with open(file, encoding="utf-8") as f:
                for i, text in enumerate(f, 1):
                    if i == line:
                        return text.rstrip("\n")
        except (OSError, UnicodeDecodeError):
            pass
        return ""

    @staticmethod
    def _extract_main_symbol(line_text: str) -> str | None:
        """从一行代码中提取主要的符号名。"""
        # def func_name(...)
        m = re.match(r"\s*(?:async\s+)?def\s+(\w+)", line_text)
        if m:
            return m.group(1)
        # class ClassName(...)
        m = re.match(r"\s*class\s+(\w+)", line_text)
        if m:
            return m.group(1)
        # variable = ... 或 self.attr = ...
        m = re.match(r"\s*(?:self\.)?(\w+)\s*=", line_text)
        if m:
            return m.group(1)
        # 取第一个标识符
        m = re.search(r"\b([a-zA-Z_]\w*)\b", line_text)
        if m:
            return m.group(1)
        return None

    @staticmethod
    def detect_language(file: str) -> str:
        _, ext = os.path.splitext(file)
        return _EXT_TO_LANG.get(ext, "python")

    @staticmethod
    def _build_definition_patterns(symbol: str, language: str) -> list[str]:
        # ast-grep 模式不需要末尾的冒号，tree-sitter AST 不把冒号当作模式的一部分
        if language == "python":
            return [f"def {symbol}($$$)", f"class {symbol}($$$)", f"class {symbol}"]
        if language in ("javascript", "typescript", "tsx", "jsx"):
            return [
                f"function {symbol}($$$)",
                f"const {symbol} = ($$$) =>",
                f"class {symbol}",
            ]
        if language == "go":
            return [f"func {symbol}($$$)"]
        return [symbol]

    @staticmethod
    def _guess_symbol_type(line_text: str, symbol: str) -> SymbolType:
        if re.match(r"\s*(?:async\s+)?def\s+", line_text):
            return SymbolType.FUNCTION
        if re.match(r"\s*class\s+", line_text):
            return SymbolType.CLASS
        return SymbolType.OTHER

    @staticmethod
    def _guess_scope(symbol: str) -> SymbolScope:
        if symbol.startswith("__") and symbol.endswith("__"):
            return SymbolScope.MODULE_PUBLIC
        if symbol.startswith("_"):
            return SymbolScope.PRIVATE
        return SymbolScope.MODULE_PUBLIC

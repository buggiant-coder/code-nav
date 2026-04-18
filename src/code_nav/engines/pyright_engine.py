"""Pyright LSP 引擎封装。

通过 pyright-langserver --stdio 子进程提供 Python 语义分析。
比 Jedi 更准确：支持跨继承引用查找、re-export 穿透、完整类型推断。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from code_nav.engines.base import AnalysisEngine
from code_nav.models import (
    Reference, Definition, SymbolInfo, SymbolType, SymbolScope,
    RefType, Confidence,
)

logger = logging.getLogger(__name__)

_MAX_RESTART = 3
_INIT_TIMEOUT = 15.0
_REQUEST_TIMEOUT = 10.0


class PyrightEngine(AnalysisEngine):
    """Pyright LSP 语义分析引擎（仅 Python）。"""

    def __init__(self, project_path: str | None = None):
        self._project_path = project_path
        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._request_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._opened_files: set[str] = set()
        self._initialized = False
        self._restart_count = 0
        self._buf = b""
        self._lock = asyncio.Lock()

    @property
    def name(self) -> str:
        return "pyright"

    @property
    def supported_languages(self) -> list[str]:
        return ["python"]

    # ----------------------------------------------------------------
    # AnalysisEngine 接口
    # ----------------------------------------------------------------

    async def find_references(
        self, file: str, line: int, column: int = 0,
    ) -> list[Reference]:
        await self._ensure_ready()
        await self._open_project_files(file)
        uri = self._path_to_uri(file)
        result = await self._request("textDocument/references", {
            "textDocument": {"uri": uri},
            "position": {"line": line - 1, "character": column},
            "context": {"includeDeclaration": True},
        })
        if not result:
            return []
        return [self._location_to_reference(loc) for loc in result]

    async def go_to_definition(
        self, file: str, line: int, column: int = 0,
    ) -> list[Definition]:
        await self._ensure_ready()
        await self._open_file(file)
        uri = self._path_to_uri(file)
        result = await self._request("textDocument/definition", {
            "textDocument": {"uri": uri},
            "position": {"line": line - 1, "character": column},
        })
        if not result:
            return []
        if isinstance(result, dict):
            result = [result]
        return [self._location_to_definition(loc) for loc in result]

    async def get_symbol_info(
        self, file: str, line: int, column: int = 0,
    ) -> SymbolInfo | None:
        await self._ensure_ready()
        await self._open_file(file)
        uri = self._path_to_uri(file)
        result = await self._request("textDocument/hover", {
            "textDocument": {"uri": uri},
            "position": {"line": line - 1, "character": column},
        })
        if not result:
            return None
        return self._hover_to_symbol_info(result, file, line)

    # ----------------------------------------------------------------
    # resolve_symbol（纯文本，不需要 LSP）
    # ----------------------------------------------------------------

    def resolve_symbol(self, file: str, symbol: str) -> tuple[int, int] | None:
        try:
            text = Path(file).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        esc = re.escape(symbol)
        pattern = re.compile(
            rf'^\s*(?:async\s+)?(?:def|class)\s+{esc}\b'
            rf'|^{esc}\s*[=:]'
            rf'|^\s*{esc}\s*[=:]'
            rf'|^\s*from\s+\S+\s+import\s+.*\b{esc}\b'
            rf'|^\s*import\s+.*\b{esc}\b',
            re.MULTILINE,
        )
        for i, line_text in enumerate(text.splitlines(), 1):
            m = pattern.match(line_text)
            if m:
                col = line_text.find(symbol)
                return (i, max(col, 0))
        return None

    # ----------------------------------------------------------------
    # LSP 生命周期
    # ----------------------------------------------------------------

    async def _ensure_ready(self) -> None:
        if self._initialized and self._proc and self._proc.returncode is None:
            return
        await self._start()

    async def _start(self) -> None:
        async with self._lock:
            if self._initialized and self._proc and self._proc.returncode is None:
                return
            await self._cleanup()
            try:
                self._proc = await asyncio.create_subprocess_exec(
                    "pyright-langserver", "--stdio",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except FileNotFoundError:
                logger.error(
                    "pyright-langserver not found. Install: pip install pyright"
                )
                raise

            self._buf = b""
            self._reader_task = asyncio.create_task(self._reader_loop())

            root_uri = self._path_to_uri(
                self._project_path or os.getcwd()
            )
            resp = await self._request("initialize", {
                "processId": os.getpid(),
                "rootUri": root_uri,
                "capabilities": {},
            }, timeout=_INIT_TIMEOUT)
            if resp is None:
                raise RuntimeError("Pyright initialize failed")

            await self._notify("initialized", {})
            self._initialized = True
            self._opened_files.clear()
            self._restart_count = 0
            logger.info("Pyright LSP server started")

    async def _cleanup(self) -> None:
        self._initialized = False
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._proc and self._proc.returncode is None:
            try:
                self._proc.kill()
                await self._proc.wait()
            except Exception:
                pass
        self._proc = None
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(RuntimeError("LSP server stopped"))
        self._pending.clear()

    async def shutdown(self) -> None:
        if self._proc and self._proc.returncode is None:
            try:
                await self._request("shutdown", None, timeout=3.0)
                await self._notify("exit", None)
            except Exception:
                pass
        await self._cleanup()

    # ----------------------------------------------------------------
    # JSON-RPC 通信
    # ----------------------------------------------------------------

    async def _request(
        self, method: str, params: Any, timeout: float = _REQUEST_TIMEOUT,
    ) -> Any:
        self._request_id += 1
        req_id = self._request_id
        msg = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}

        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[req_id] = fut

        self._write(msg)

        try:
            result = await asyncio.wait_for(fut, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            logger.warning("LSP request %s timed out (id=%d)", method, req_id)
            return None
        except Exception as e:
            self._pending.pop(req_id, None)
            logger.warning("LSP request %s failed: %s", method, e)
            if self._restart_count < _MAX_RESTART:
                self._restart_count += 1
                self._initialized = False
                await self._ensure_ready()
            return None

    async def _notify(self, method: str, params: Any) -> None:
        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._write(msg)

    def _write(self, msg: dict) -> None:
        if not self._proc or not self._proc.stdin:
            return
        body = json.dumps(msg).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        self._proc.stdin.write(header + body)

    async def _reader_loop(self) -> None:
        assert self._proc and self._proc.stdout
        try:
            while True:
                header_line = b""
                while True:
                    byte = await self._proc.stdout.read(1)
                    if not byte:
                        return
                    header_line += byte
                    if header_line.endswith(b"\r\n\r\n"):
                        break

                length_match = re.search(
                    rb"Content-Length:\s*(\d+)", header_line
                )
                if not length_match:
                    continue
                length = int(length_match.group(1))
                body = await self._proc.stdout.readexactly(length)
                msg = json.loads(body.decode("utf-8"))

                if "id" in msg and "method" not in msg:
                    req_id = msg["id"]
                    fut = self._pending.pop(req_id, None)
                    if fut and not fut.done():
                        if "error" in msg:
                            fut.set_exception(
                                RuntimeError(msg["error"].get("message", "LSP error"))
                            )
                        else:
                            fut.set_result(msg.get("result"))
        except (asyncio.CancelledError, asyncio.IncompleteReadError):
            pass
        except Exception as e:
            logger.warning("LSP reader error: %s", e)

    # ----------------------------------------------------------------
    # 文件管理
    # ----------------------------------------------------------------

    async def _open_file(self, file: str) -> None:
        uri = self._path_to_uri(file)
        if uri in self._opened_files:
            return
        try:
            text = Path(file).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return
        await self._notify("textDocument/didOpen", {
            "textDocument": {
                "uri": uri,
                "languageId": "python",
                "version": 1,
                "text": text,
            },
        })
        self._opened_files.add(uri)

    async def _open_project_files(self, file: str) -> None:
        """打开文件所在项目的所有 Python 文件，确保跨文件引用可发现。"""
        root = self._find_project_root(file)
        for py_file in Path(root).rglob("*.py"):
            await self._open_file(str(py_file))

    # ----------------------------------------------------------------
    # 结果转换
    # ----------------------------------------------------------------

    def _location_to_reference(self, loc: dict) -> Reference:
        file = self._uri_to_path(loc["uri"])
        line = loc["range"]["start"]["line"] + 1
        column = loc["range"]["start"]["character"]
        context = self._read_line(file, line)
        return Reference(
            file=file, line=line, column=column,
            context=context,
            ref_type=self._infer_ref_type(context),
            source=self.name,
            confidence=Confidence.HIGH,
        )

    def _location_to_definition(self, loc: dict) -> Definition:
        file = self._uri_to_path(loc["uri"])
        line = loc["range"]["start"]["line"] + 1
        column = loc["range"]["start"]["character"]
        context = self._read_line(file, line)
        return Definition(
            file=file, line=line, column=column,
            context=context,
            source=self.name,
            confidence=Confidence.HIGH,
        )

    def _hover_to_symbol_info(
        self, hover: dict, file: str, line: int,
    ) -> SymbolInfo | None:
        contents = hover.get("contents", {})
        if isinstance(contents, dict):
            text = contents.get("value", "")
        elif isinstance(contents, str):
            text = contents
        elif isinstance(contents, list):
            text = "\n".join(
                c.get("value", "") if isinstance(c, dict) else str(c)
                for c in contents
            )
        else:
            return None

        if not text.strip():
            return None

        name = ""
        symbol_type = SymbolType.OTHER
        signature = ""
        docstring = ""
        return_type = ""

        lines = text.strip().split("\n")
        first_line = lines[0] if lines else ""

        if "(function)" in first_line or "def " in first_line:
            symbol_type = SymbolType.FUNCTION
            sig_lines = []
            for l in lines:
                sig_lines.append(l)
                if l.rstrip().endswith(")") or "-> " in l:
                    break
            signature = "\n".join(sig_lines)
            signature = re.sub(r"^\(function\)\s*", "", signature)
        elif "(class)" in first_line or "class " in first_line:
            symbol_type = SymbolType.CLASS
            signature = re.sub(r"^\(class\)\s*", "", first_line)
        elif "(variable)" in first_line:
            symbol_type = SymbolType.VARIABLE
            signature = re.sub(r"^\(variable\)\s*", "", first_line)
        elif "(module)" in first_line:
            symbol_type = SymbolType.MODULE
        else:
            signature = first_line

        name_match = re.search(r'(?:def|class)\s+(\w+)', signature)
        if name_match:
            name = name_match.group(1)
        elif ":" in first_line:
            name = first_line.split(":")[0].strip().split()[-1] if first_line.split() else ""
        if not name:
            name = re.sub(r'^\(\w+\)\s*', '', first_line).split("(")[0].split(":")[0].strip()

        ret_match = re.search(r'->\s*(.+?)$', signature, re.MULTILINE)
        if ret_match:
            return_type = ret_match.group(1).strip().rstrip(":")

        sig_end = text.find("\n\n")
        if sig_end >= 0:
            docstring = text[sig_end:].strip()

        return SymbolInfo(
            name=name,
            symbol_type=symbol_type,
            file=file,
            line=line,
            scope=self._infer_scope(name),
            signature=signature,
            return_type=return_type,
            docstring=docstring,
        )

    # ----------------------------------------------------------------
    # 工具方法
    # ----------------------------------------------------------------

    @staticmethod
    def _path_to_uri(path: str) -> str:
        abs_path = os.path.abspath(path)
        return f"file://{abs_path}"

    @staticmethod
    def _uri_to_path(uri: str) -> str:
        if uri.startswith("file://"):
            return uri[7:]
        return uri

    @staticmethod
    def _read_line(file: str, line: int) -> str:
        try:
            with open(file, encoding="utf-8") as f:
                for i, text in enumerate(f, 1):
                    if i == line:
                        return text.strip()
        except (OSError, UnicodeDecodeError):
            pass
        return ""

    @staticmethod
    def _find_project_root(file: str) -> str:
        markers = {"pyproject.toml", "setup.py", "setup.cfg", "package.json",
                    "go.mod", ".git", "requirements.txt", "Makefile"}
        path = Path(file).resolve().parent
        while path != path.parent:
            if any((path / m).exists() for m in markers):
                return str(path)
            path = path.parent
        return str(Path(file).resolve().parent)

    @staticmethod
    def _infer_ref_type(context: str) -> RefType:
        stripped = context.strip()
        if stripped.startswith(("import ", "from ")):
            return RefType.IMPORT
        if stripped.startswith(("def ", "async def ", "class ")):
            return RefType.OTHER
        if "(" in stripped:
            return RefType.CALL
        return RefType.OTHER

    @staticmethod
    def _infer_scope(name: str) -> SymbolScope:
        if name.startswith("__") and name.endswith("__"):
            return SymbolScope.MODULE_PUBLIC
        if name.startswith("_"):
            return SymbolScope.PRIVATE
        return SymbolScope.MODULE_PUBLIC

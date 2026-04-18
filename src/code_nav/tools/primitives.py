"""低级工具的业务逻辑实现。

编排引擎调用、合并结果、构造输出。
多引擎并行调用 → 结果合并去重 → 构造输出。
"""
from __future__ import annotations

import asyncio
import json
import logging

from code_nav.engines import EngineManager
from code_nav.models import (
    FindReferencesResult, GoToDefinitionResult, AstSearchResult, Location,
    Reference, Definition, SymbolInfo,
    Diagnostic, DiagnosticsResult,
)
from code_nav.utils.merge import merge_references, merge_definitions
from code_nav.utils.project import is_test_file

logger = logging.getLogger(__name__)


async def find_references(
    mgr: EngineManager,
    file: str,
    line: int = 0,
    column: int = 0,
    symbol: str = "",
    include_definition: bool = False,
    include_tests: bool = False,
) -> FindReferencesResult:
    """查找引用：多引擎并行调用 → 合并去重 → 返回。

    支持两种定位方式：
    - symbol: 按符号名查找（推荐，Agent 友好）
    - line + column: 按行列精确定位
    """
    # 如果提供了 symbol，先解析为 line/column
    if symbol and not line:
        pos = mgr.resolve_symbol(file, symbol)
        if pos is None:
            return FindReferencesResult(
                symbol=symbol, symbol_type="", definition=None,
                references=[], total_count=0,
            )
        line, column = pos

    language = mgr.detect_language(file)
    engines = mgr.get_engines_for(language)

    # 并行调用所有引擎
    ref_tasks = [engine.find_references(file, line, column) for engine in engines]
    info_tasks = [engine.get_symbol_info(file, line, column) for engine in engines]
    ref_results = await asyncio.gather(*ref_tasks, return_exceptions=True)
    info_results = await asyncio.gather(*info_tasks, return_exceptions=True)

    # 收集所有引用
    all_refs: list[Reference] = []
    for result in ref_results:
        if isinstance(result, list):
            all_refs.extend(result)

    # 取第一个成功返回的符号信息（引擎按优先级排序，pyright 优先）
    symbol_name = ""
    symbol_type = ""
    definition = None
    for result in info_results:
        if isinstance(result, SymbolInfo) and result is not None:
            symbol_name = result.name
            symbol_type = result.symbol_type.value
            definition = Location(file=result.file, line=result.line)
            break

    # 合并去重
    merged = merge_references(all_refs)

    if not include_definition and definition:
        merged = [
            r for r in merged
            if not (r.file == definition.file and r.line == definition.line)
        ]

    filtered_test_count = 0
    if not include_tests:
        before = len(merged)
        merged = [r for r in merged if not is_test_file(r.file)]
        filtered_test_count = before - len(merged)

    return FindReferencesResult(
        symbol=symbol_name,
        symbol_type=symbol_type,
        definition=definition,
        references=merged,
        total_count=len(merged),
        filtered_test_count=filtered_test_count,
    )


async def go_to_definition(
    mgr: EngineManager,
    file: str,
    line: int = 0,
    column: int = 0,
    symbol: str = "",
) -> GoToDefinitionResult:
    """跳转定义：多引擎并行调用 → 合并去重 → 返回。

    支持两种定位方式：
    - symbol: 按符号名查找（推荐）
    - line + column: 按行列精确定位
    """
    if symbol and not line:
        pos = mgr.resolve_symbol(file, symbol)
        if pos is None:
            return GoToDefinitionResult(
                symbol=symbol, symbol_type="", definitions=[],
            )
        line, column = pos

    language = mgr.detect_language(file)
    engines = mgr.get_engines_for(language)

    # 并行调用
    def_tasks = [engine.go_to_definition(file, line, column) for engine in engines]
    info_tasks = [engine.get_symbol_info(file, line, column) for engine in engines]
    def_results = await asyncio.gather(*def_tasks, return_exceptions=True)
    info_results = await asyncio.gather(*info_tasks, return_exceptions=True)

    all_defs: list[Definition] = []
    for result in def_results:
        if isinstance(result, list):
            all_defs.extend(result)

    symbol_name = ""
    symbol_type = ""
    docstring = ""
    signature = ""
    for result in info_results:
        if isinstance(result, SymbolInfo) and result is not None:
            symbol_name = result.name
            symbol_type = result.symbol_type.value
            docstring = result.docstring
            signature = result.signature
            break

    merged = merge_definitions(all_defs)

    return GoToDefinitionResult(
        symbol=symbol_name,
        symbol_type=symbol_type,
        definitions=merged,
        docstring=docstring,
        signature=signature,
    )


async def ast_search(
    mgr: EngineManager,
    pattern: str,
    language: str,
    path: str = ".",
    limit: int = 50,
) -> AstSearchResult:
    """AST 模式搜索：直接调用 ast-grep。"""
    engine = mgr.get_pattern_engine()
    matches = await engine.search(pattern, language, path, limit)
    return AstSearchResult(
        pattern=pattern,
        language=language,
        matches=matches,
        total_count=len(matches),
        truncated=len(matches) >= limit,
    )


async def check_diagnostics(
    path: str,
    level: str = "error",
) -> DiagnosticsResult:
    """运行 Pyright 类型检查，返回诊断结果。"""
    args = ["pyright", path, "--outputjson"]
    if level in ("warning", "information"):
        args.extend(["--level", level])

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
    except FileNotFoundError:
        logger.error("pyright not found. Install: pip install pyright")
        return DiagnosticsResult(
            diagnostics=[], error_count=0, warning_count=0, files_analyzed=0,
        )

    if not stdout.strip():
        return DiagnosticsResult(
            diagnostics=[], error_count=0, warning_count=0, files_analyzed=0,
        )

    try:
        data = json.loads(stdout.decode())
    except json.JSONDecodeError:
        logger.warning("pyright returned invalid JSON")
        return DiagnosticsResult(
            diagnostics=[], error_count=0, warning_count=0, files_analyzed=0,
        )

    diagnostics = []
    for d in data.get("generalDiagnostics", []):
        r = d.get("range", {})
        start = r.get("start", {})
        end = r.get("end", {})
        diagnostics.append(Diagnostic(
            file=d.get("file", ""),
            line=start.get("line", 0) + 1,
            column=start.get("character", 0),
            end_line=end.get("line", 0) + 1,
            end_column=end.get("character", 0),
            severity=d.get("severity", "error"),
            message=d.get("message", ""),
            rule=d.get("rule", ""),
        ))

    summary = data.get("summary", {})
    return DiagnosticsResult(
        diagnostics=diagnostics,
        error_count=summary.get("errorCount", 0),
        warning_count=summary.get("warningCount", 0),
        files_analyzed=summary.get("filesAnalyzed", 0),
    )

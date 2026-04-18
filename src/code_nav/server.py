"""code-nav MCP Server — 工具注册与启动。

使用 FastMCP 高级 API，工具参数从 Python 类型注解自动生成 JSON Schema。
"""
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from code_nav.engines import EngineManager
from code_nav.tools import primitives, workflows, wiki_tools
from code_nav.models import to_dict

mcp = FastMCP(
    name="code-nav",
    instructions=(
        "Semantic code navigation server. These tools are MORE ACCURATE than grep "
        "for understanding code structure — you MUST prefer them over Grep/text search.\n\n"
        "## When to use which tool\n\n"
        "Code navigation (use INSTEAD of grep/text search):\n"
        "- Need to find where a symbol is used → find_references (NOT grep for the name)\n"
        "- Need to jump to a symbol's definition → go_to_definition (NOT grep for 'def xxx')\n"
        "- Need to search for a code structure/pattern → ast_search (e.g. 'def $FN($$$):' "
        "for all function definitions, 'class $NAME' for all class definitions, "
        "'$OBJ.save($$$)' for all .save() calls)\n"
        "- Only use Grep for non-code content (config files, docs, string constants)\n\n"
        "Code understanding (use BEFORE reading source code):\n"
        "- Need to understand a module → get_wiki first; if is_stale=false, trust it and "
        "skip reading source; if is_stale=true or not exists, read source\n"
        "- Need a module's structure and dependencies → query_module\n"
        "- Need a symbol's callers/callees → query_symbol (millisecond response from index)\n\n"
        "Code modification (MUST follow this workflow):\n"
        "1. get_change_scope — quick risk assessment\n"
        "   - LOW → modify directly\n"
        "   - MEDIUM/HIGH → proceed to step 2\n"
        "2. pre_change_analysis — deep analysis of all callers and test coverage\n"
        "3. Make the code change\n"
        "4. post_change_validate — verify no downstream breakage\n"
        "5. check_diagnostics — run type checker to catch type errors (optional, "
        "like IDE red squiggles)\n\n"
        "After git commit:\n"
        "- Call build_index to check wiki_candidates\n"
        "- If stale or new lists are non-empty, inform the user which wikis need updating"
    ),
)

_engine_mgr: EngineManager | None = None


def get_engine_manager() -> EngineManager:
    global _engine_mgr
    if _engine_mgr is None:
        _engine_mgr = EngineManager()
    return _engine_mgr


# ============================================================
# 低级工具 (Primitive Tools)
# ============================================================

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def ast_search(
    pattern: str,
    language: str,
    path: str = ".",
    limit: int = 50,
) -> dict:
    """Search code using AST patterns. Unlike text grep, this understands code
    structure — won't match inside comments or strings, can match complex patterns.
    Supports all major languages. Examples: 'def $FN($$$):' finds all function
    definitions, 'class $NAME' finds all class definitions,
    '$OBJ.save($$$)' finds all .save() calls."""
    mgr = get_engine_manager()
    result = await primitives.ast_search(mgr, pattern, language, path, limit)
    return to_dict(result)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def find_references(
    file: str,
    symbol: str = "",
    line: int = 0,
    column: int = 0,
    include_definition: bool = False,
    include_tests: bool = False,
) -> dict:
    """Find all references to a symbol (function, class, variable).
    MORE ACCURATE than Grep — understands types, imports, and inheritance.

    Two ways to specify the target:
    - symbol: pass the symbol name directly (e.g. "calculate_total") — RECOMMENDED
    - line/column: pass the exact position in the file

    At least one of symbol or line must be provided."""
    mgr = get_engine_manager()
    result = await primitives.find_references(
        mgr, file, line, column, symbol=symbol,
        include_definition=include_definition,
        include_tests=include_tests,
    )
    return to_dict(result)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def go_to_definition(
    file: str,
    symbol: str = "",
    line: int = 0,
    column: int = 0,
) -> dict:
    """Jump to the definition of a symbol. More accurate than text search —
    follows imports, resolves aliases, handles inheritance.

    Two ways to specify the target:
    - symbol: pass the symbol name directly (e.g. "calculate_total") — RECOMMENDED
    - line/column: pass the exact position in the file

    At least one of symbol or line must be provided."""
    mgr = get_engine_manager()
    result = await primitives.go_to_definition(
        mgr, file, line, column, symbol=symbol,
    )
    return to_dict(result)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def check_diagnostics(
    path: str,
    level: str = "error",
) -> dict:
    """Run Pyright type checker on Python files. Returns type errors,
    warnings, and diagnostics — like IDE red/yellow squiggles.
    Use after modifying code to catch type issues early.

    Args:
        path: File or directory to check.
        level: Minimum severity to report: "error", "warning", or "information"."""
    result = await primitives.check_diagnostics(path, level)
    return to_dict(result)


# ============================================================
# 高级工具 (Workflow Tools)
# ============================================================

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def get_change_scope(
    file: str,
    symbol: str = "",
    line: int = 0,
) -> dict:
    """Quick risk assessment BEFORE modifying a symbol. Returns caller count,
    whether the symbol is exported, test coverage, risk level, and recommendation.

    Call this first when planning a code change to decide if you need deeper
    analysis via pre_change_analysis."""
    mgr = get_engine_manager()
    result = await workflows.get_change_scope(mgr, file, symbol=symbol, line=line)
    return to_dict(result)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def pre_change_analysis(
    file: str,
    symbol: str = "",
    line: int = 0,
    change_type: str = "other",
) -> dict:
    """Deep impact analysis BEFORE modifying a symbol. Returns current signature,
    all callers with context, test coverage, risk assessment, and specific suggestions.

    change_type options: modify_signature, rename, remove, change_return_type,
    change_behavior, other.

    Call this BEFORE making changes to understand the full blast radius."""
    mgr = get_engine_manager()
    result = await workflows.pre_change_analysis(
        mgr, file, symbol=symbol, line=line, change_type=change_type,
    )
    return to_dict(result)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def post_change_validate(
    file: str,
    symbol: str = "",
    line: int = 0,
    original_signature: str = "",
) -> dict:
    """Validate AFTER modifying a symbol. Compares old/new signatures, checks
    all callers for compatibility, reports breaking changes.

    Pass original_signature from pre_change_analysis to enable signature diff.
    Returns status (clean/breaking_changes_detected), issues list, and suggested fixes."""
    mgr = get_engine_manager()
    result = await workflows.post_change_validate(
        mgr, file, symbol=symbol, line=line,
        original_signature=original_signature,
    )
    return to_dict(result)


# ============================================================
# Wiki / 索引工具 (Wiki & Index Tools)
# ============================================================

@mcp.tool()
async def build_index(
    path: str = "",
    force: bool = False,
) -> dict:
    """Build or update the code knowledge graph for the project.

    Scans all Python files, extracts symbols (functions, classes, methods,
    variables), their parameters/return types, and the relationships between
    them (calls, imports, inheritance).

    The graph is stored in {project}/.code-nav/graph.db and is used by
    query_symbol and query_module for fast lookups.

    Args:
        path: Project root directory. Defaults to CODE_NAV_PROJECT or cwd.
        force: If true, rebuild from scratch. If false (default), only
               re-index files that changed since last build.

    Returns:
        Build summary: files scanned, symbols indexed, edges created, time taken.
    """
    mgr = get_engine_manager()
    project_path = path or mgr._project_path or "."
    result = await wiki_tools.build_index(
        project_path=project_path,
        analysis_engine=mgr.pyright,
        force=force,
    )
    return result


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def query_symbol(
    name: str,
    file: str = "",
    include_callers: bool = True,
    include_callees: bool = True,
    max_depth: int = 1,
) -> dict:
    """Query the knowledge graph for a symbol's complete information.

    Returns the symbol's signature, parameters with types, return type,
    docstring, and its upstream/downstream relationships (who calls it,
    what it calls).

    Much faster than find_references — uses pre-built index instead of
    real-time analysis. Use find_references when you need guaranteed
    up-to-the-second accuracy; use query_symbol when you need a quick
    overview of a symbol's role in the codebase.

    Args:
        name: Symbol name (e.g. "calculate_total", "OrderProcessor.process").
        file: Optional file path to disambiguate if multiple symbols share
              the same name.
        include_callers: Include functions that call this symbol (default: true).
        include_callees: Include functions this symbol calls (default: true).
        max_depth: How many hops to traverse (1 = direct callers/callees only,
                   2 = callers of callers, etc.). Default: 1, max: 3.

    Returns:
        Symbol info + relationship graph.
    """
    mgr = get_engine_manager()
    project_path = mgr._project_path or "."
    return await wiki_tools.query_symbol(
        project_path=project_path,
        name=name,
        file=file,
        include_callers=include_callers,
        include_callees=include_callees,
        max_depth=max_depth,
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def query_module(
    file: str = "",
    package: str = "",
) -> dict:
    """Query the knowledge graph for a module's overview.

    Returns all symbols defined in the module, its dependencies (what it
    imports), and its dependents (who imports from it). This gives a
    complete picture of a module's role in the project.

    Args:
        file: Module file path (e.g. "util/odps_util.py"). Relative to
              project root or absolute.
        package: Package name (e.g. "util.odps_util"). Alternative to file.

    At least one of file or package must be provided.

    Returns:
        Module overview with symbols and dependency information.
    """
    mgr = get_engine_manager()
    project_path = mgr._project_path or "."
    return await wiki_tools.query_module(
        project_path=project_path,
        file=file,
        package=package,
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def get_wiki(
    module: str = "",
    package: str = "",
    level: str = "",
) -> dict:
    """Read wiki content for a module, package, or the whole project.

    Wiki is a three-level documentation system maintained alongside the code
    knowledge graph. Each level serves a different purpose:
    - project: high-level overview of what the project does
    - package: what a package/subsystem is responsible for
    - module: detailed business logic documentation for a single file

    Args:
        module: Module file path (e.g. "util/odps_util.py") for module-level wiki.
        package: Package name (e.g. "util") for package-level wiki.
        level: Explicit level override ("project", "package", or "module").
               If omitted, inferred from module/package args.

    Returns:
        Wiki content, staleness status, and path information.
    """
    mgr = get_engine_manager()
    project_path = mgr._project_path or "."
    return await wiki_tools.get_wiki(
        project_path=project_path,
        module=module,
        package=package,
        level=level,
    )


@mcp.tool()
async def save_wiki(
    content: str,
    module: str = "",
    package: str = "",
    level: str = "",
) -> dict:
    """Save wiki content for a module, package, or the whole project.

    Use this after analyzing code with query_symbol/query_module to persist
    your understanding as human-readable documentation.

    Args:
        content: Markdown content to save.
        module: Module file path (e.g. "util/odps_util.py") for module-level wiki.
        package: Package name (e.g. "util") for package-level wiki.
        level: Explicit level override ("project", "package", or "module").

    Returns:
        Status and path of the saved wiki file.
    """
    mgr = get_engine_manager()
    project_path = mgr._project_path or "."
    return await wiki_tools.save_wiki(
        project_path=project_path,
        content=content,
        module=module,
        package=package,
        level=level,
    )

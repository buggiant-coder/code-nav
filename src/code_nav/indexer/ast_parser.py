"""AstParser — 用 Python ast 标准库提取符号、参数、装饰器、import 语句。"""
from __future__ import annotations

import ast
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class ParsedParameter:
    name: str
    position: int
    type_annotation: str = ""
    default_value: str = ""
    kind: str = "POSITIONAL_OR_KEYWORD"


@dataclass
class ParsedSymbol:
    name: str
    qualified_name: str
    symbol_type: str  # function | class | method | variable
    scope: str  # public | private | module_private
    line: int
    end_line: int | None = None
    column: int = 0
    signature: str = ""
    return_type: str = ""
    docstring: str = ""
    decorators: list[str] = field(default_factory=list)
    parent_name: str | None = None
    parameters: list[ParsedParameter] = field(default_factory=list)


@dataclass
class ParsedImport:
    module: str  # import source (e.g. "order", "os.path")
    names: list[str] = field(default_factory=list)  # imported names
    alias: str = ""
    line: int = 0


@dataclass
class ParseResult:
    symbols: list[ParsedSymbol] = field(default_factory=list)
    imports: list[ParsedImport] = field(default_factory=list)
    line_count: int = 0


def _annotation_to_str(node: ast.expr | None) -> str:
    """将 AST 类型注解节点转为字符串。"""
    if node is None:
        return ""
    return ast.unparse(node)


def _default_to_str(node: ast.expr | None) -> str:
    """将 AST 默认值节点转为字符串。"""
    if node is None:
        return ""
    return ast.unparse(node)


def _get_decorators(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> list[str]:
    """提取装饰器名称列表。"""
    result = []
    for dec in node.decorator_list:
        if isinstance(dec, ast.Name):
            result.append(dec.id)
        elif isinstance(dec, ast.Attribute):
            result.append(ast.unparse(dec))
        elif isinstance(dec, ast.Call):
            # @decorator(args) → 取函数部分
            if isinstance(dec.func, ast.Name):
                result.append(dec.func.id)
            elif isinstance(dec.func, ast.Attribute):
                result.append(ast.unparse(dec.func))
            else:
                result.append(ast.unparse(dec))
        else:
            result.append(ast.unparse(dec))
    return result


def _determine_scope(name: str) -> str:
    """根据命名规则判断 scope。"""
    if name.startswith("__") and name.endswith("__"):
        return "public"  # dunder methods
    if name.startswith("__"):
        return "module_private"
    if name.startswith("_"):
        return "private"
    return "public"


class AstParser:
    """用 Python ast 模块解析单个文件，提取符号和 import。"""

    def parse_file(self, file_path: str) -> ParseResult | None:
        """解析单个 Python 文件。解析失败返回 None。"""
        path = Path(file_path)
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            logger.warning("Skipping non-UTF-8 file: %s", file_path)
            return None
        except OSError as e:
            logger.warning("Cannot read file %s: %s", file_path, e)
            return None

        try:
            tree = ast.parse(source, filename=file_path)
        except SyntaxError as e:
            logger.warning("Syntax error in %s: %s", file_path, e)
            return None

        line_count = source.count("\n") + (1 if source and not source.endswith("\n") else 0)

        # 从文件路径推断模块名（用于 qualified_name 构建）
        module_name = path.stem  # 简单用文件名，builder 会传入完整的

        result = ParseResult(line_count=line_count)
        self._extract_imports(tree, result)
        self._extract_symbols(tree, result, module_name, parent_name=None)
        return result

    def _extract_imports(self, tree: ast.Module, result: ParseResult) -> None:
        """提取 import 和 from...import 语句。"""
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    result.imports.append(ParsedImport(
                        module=alias.name,
                        names=[],
                        alias=alias.asname or "",
                        line=node.lineno,
                    ))
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                names = [alias.name for alias in node.names]
                result.imports.append(ParsedImport(
                    module=module,
                    names=names,
                    alias="",
                    line=node.lineno,
                ))

    def _extract_symbols(
        self,
        node: ast.AST,
        result: ParseResult,
        module_name: str,
        parent_name: str | None,
    ) -> None:
        """递归提取符号定义。"""
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._process_function(child, result, module_name, parent_name)
            elif isinstance(child, ast.ClassDef):
                self._process_class(child, result, module_name, parent_name)
            elif isinstance(child, (ast.Assign, ast.AnnAssign)):
                # 仅处理模块级和类级变量
                if parent_name is None or parent_name is not None:
                    self._process_variable(child, result, module_name, parent_name)

    def _process_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        result: ParseResult,
        module_name: str,
        parent_name: str | None,
    ) -> None:
        """处理函数/方法定义。"""
        name = node.name
        if parent_name:
            symbol_type = "method"
            qualified_name = f"{module_name}.{parent_name}.{name}"
        else:
            symbol_type = "function"
            qualified_name = f"{module_name}.{name}"

        params = self._extract_parameters(node)
        signature = self._build_signature(name, params)
        return_type = _annotation_to_str(node.returns)

        sym = ParsedSymbol(
            name=name,
            qualified_name=qualified_name,
            symbol_type=symbol_type,
            scope=_determine_scope(name),
            line=node.lineno,
            end_line=node.end_lineno,
            column=node.col_offset,
            signature=signature,
            return_type=return_type,
            docstring=ast.get_docstring(node) or "",
            decorators=_get_decorators(node),
            parent_name=parent_name,
            parameters=params,
        )
        result.symbols.append(sym)

        # 处理嵌套函数
        self._extract_symbols(node, result, module_name, parent_name=name if not parent_name else parent_name)

    def _process_class(
        self,
        node: ast.ClassDef,
        result: ParseResult,
        module_name: str,
        parent_name: str | None,
    ) -> None:
        """处理类定义。"""
        name = node.name
        qualified_name = f"{module_name}.{name}"

        # 基类列表作为 signature 的一部分
        bases = [ast.unparse(b) for b in node.bases]
        signature = f"class {name}" + (f"({', '.join(bases)})" if bases else "")

        sym = ParsedSymbol(
            name=name,
            qualified_name=qualified_name,
            symbol_type="class",
            scope=_determine_scope(name),
            line=node.lineno,
            end_line=node.end_lineno,
            column=node.col_offset,
            signature=signature,
            docstring=ast.get_docstring(node) or "",
            decorators=_get_decorators(node),
            parent_name=parent_name,
        )
        result.symbols.append(sym)

        # 递归处理类体内的方法和嵌套类
        self._extract_symbols(node, result, module_name, parent_name=name)

    def _process_variable(
        self,
        node: ast.Assign | ast.AnnAssign,
        result: ParseResult,
        module_name: str,
        parent_name: str | None,
    ) -> None:
        """处理变量/常量赋值。仅处理简单的 Name 赋值。"""
        if isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                name = node.target.id
                type_ann = _annotation_to_str(node.annotation)
                value = _default_to_str(node.value) if node.value else ""
                self._add_variable_symbol(
                    name, module_name, parent_name, node.lineno, node.col_offset,
                    type_ann, value, result,
                )
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    name = target.id
                    value = _default_to_str(node.value)
                    self._add_variable_symbol(
                        name, module_name, parent_name, node.lineno, node.col_offset,
                        "", value, result,
                    )

    def _add_variable_symbol(
        self,
        name: str,
        module_name: str,
        parent_name: str | None,
        line: int,
        column: int,
        type_annotation: str,
        value: str,
        result: ParseResult,
    ) -> None:
        if parent_name:
            qualified_name = f"{module_name}.{parent_name}.{name}"
        else:
            qualified_name = f"{module_name}.{name}"

        signature = name
        if type_annotation:
            signature += f": {type_annotation}"
        if value:
            signature += f" = {value}"

        sym = ParsedSymbol(
            name=name,
            qualified_name=qualified_name,
            symbol_type="variable",
            scope=_determine_scope(name),
            line=line,
            column=column,
            signature=signature,
            return_type=type_annotation,
            parent_name=parent_name,
        )
        result.symbols.append(sym)

    def _extract_parameters(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> list[ParsedParameter]:
        """提取函数参数列表。"""
        params: list[ParsedParameter] = []
        args = node.args
        position = 0

        # posonlyargs (Python 3.8+)
        for arg in args.posonlyargs:
            params.append(ParsedParameter(
                name=arg.arg,
                position=position,
                type_annotation=_annotation_to_str(arg.annotation),
                kind="POSITIONAL_ONLY",
            ))
            position += 1

        # regular args
        # 默认值从右边对齐：defaults 对应 args 的后 N 个
        num_args = len(args.args)
        num_defaults = len(args.defaults)
        default_offset = num_args - num_defaults
        for i, arg in enumerate(args.args):
            # 跳过 self/cls
            if i == 0 and arg.arg in ("self", "cls"):
                continue
            default = ""
            if i >= default_offset:
                default = _default_to_str(args.defaults[i - default_offset])
            params.append(ParsedParameter(
                name=arg.arg,
                position=position,
                type_annotation=_annotation_to_str(arg.annotation),
                default_value=default,
                kind="POSITIONAL_OR_KEYWORD",
            ))
            position += 1

        # *args
        if args.vararg:
            params.append(ParsedParameter(
                name=args.vararg.arg,
                position=position,
                type_annotation=_annotation_to_str(args.vararg.annotation),
                kind="VAR_POSITIONAL",
            ))
            position += 1

        # keyword-only args
        for i, arg in enumerate(args.kwonlyargs):
            default = ""
            if i < len(args.kw_defaults) and args.kw_defaults[i] is not None:
                default = _default_to_str(args.kw_defaults[i])
            params.append(ParsedParameter(
                name=arg.arg,
                position=position,
                type_annotation=_annotation_to_str(arg.annotation),
                default_value=default,
                kind="KEYWORD_ONLY",
            ))
            position += 1

        # **kwargs
        if args.kwarg:
            params.append(ParsedParameter(
                name=args.kwarg.arg,
                position=position,
                type_annotation=_annotation_to_str(args.kwarg.annotation),
                kind="VAR_KEYWORD",
            ))

        return params

    def _build_signature(self, name: str, params: list[ParsedParameter]) -> str:
        """从参数列表重建函数签名字符串。"""
        parts = []
        for p in params:
            s = p.name
            if p.kind == "VAR_POSITIONAL":
                s = f"*{p.name}"
            elif p.kind == "VAR_KEYWORD":
                s = f"**{p.name}"
            if p.type_annotation:
                s += f": {p.type_annotation}"
            if p.default_value:
                s += f" = {p.default_value}"
            parts.append(s)
        return f"{name}({', '.join(parts)})"

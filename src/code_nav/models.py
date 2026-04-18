"""code-nav-mcp 的数据模型定义。

引擎层内部使用 dataclass 传递数据，MCP 工具函数将其转为 dict 返回给 Agent。
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field, asdict
from typing import Any


# ============================================================
# 枚举类型
# ============================================================

class RefType(str, enum.Enum):
    CALL = "call"
    IMPORT = "import"
    ASSIGNMENT = "assignment"
    TYPE_ANNOTATION = "type_annotation"
    OVERRIDE = "override"
    OTHER = "other"


class RiskLevel(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SymbolScope(str, enum.Enum):
    LOCAL = "local"
    PRIVATE = "private"
    MODULE_PRIVATE = "module-private"
    MODULE_PUBLIC = "module-public"
    PACKAGE_PUBLIC = "package-public"


class SymbolType(str, enum.Enum):
    FUNCTION = "function"
    CLASS = "class"
    METHOD = "method"
    VARIABLE = "variable"
    MODULE = "module"
    OTHER = "other"


class Confidence(str, enum.Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ChangeType(str, enum.Enum):
    MODIFY_SIGNATURE = "modify_signature"
    RENAME = "rename"
    REMOVE = "remove"
    CHANGE_RETURN_TYPE = "change_return_type"
    CHANGE_BEHAVIOR = "change_behavior"
    OTHER = "other"


class ValidationStatus(str, enum.Enum):
    CLEAN = "clean"
    BREAKING_CHANGES_DETECTED = "breaking_changes_detected"
    ANALYSIS_INCOMPLETE = "analysis_incomplete"


class IssueSeverity(str, enum.Enum):
    ERROR = "error"
    WARNING = "warning"


# ============================================================
# 基础位置类型
# ============================================================

@dataclass
class Location:
    file: str
    line: int
    column: int = 0


@dataclass
class Reference:
    file: str
    line: int
    column: int
    context: str
    ref_type: RefType = RefType.OTHER
    source: str = ""
    confidence: Confidence = Confidence.MEDIUM


@dataclass
class Definition:
    file: str
    line: int
    column: int
    context: str
    source: str = ""
    confidence: Confidence = Confidence.MEDIUM


@dataclass
class AstMatch:
    file: str
    line: int
    column: int
    matched_text: str
    captures: dict[str, str] = field(default_factory=dict)


@dataclass
class SymbolInfo:
    name: str
    symbol_type: SymbolType
    file: str
    line: int
    scope: SymbolScope = SymbolScope.MODULE_PUBLIC
    signature: str = ""
    return_type: str = ""
    docstring: str = ""


# ============================================================
# 低级工具的输出类型
# ============================================================

@dataclass
class FindReferencesResult:
    symbol: str
    symbol_type: str
    definition: Location | None
    references: list[Reference]
    total_count: int
    filtered_test_count: int = 0


@dataclass
class GoToDefinitionResult:
    symbol: str
    symbol_type: str
    definitions: list[Definition]
    docstring: str = ""
    signature: str = ""


@dataclass
class AstSearchResult:
    pattern: str
    language: str
    matches: list[AstMatch]
    total_count: int
    truncated: bool = False


# ============================================================
# 高级工具的输出类型
# ============================================================

@dataclass
class CallerInfo:
    file: str
    line: int
    function: str
    call_expression: str
    args_used: dict[str, str] = field(default_factory=dict)
    return_value_usage: str = ""


@dataclass
class TestCoverage:
    test_files: list[str] = field(default_factory=list)
    test_functions: list[str] = field(default_factory=list)
    has_direct_tests: bool = False


@dataclass
class RiskAssessment:
    level: RiskLevel
    caller_count: int
    reasons: list[str] = field(default_factory=list)


@dataclass
class PreChangeReport:
    symbol: str
    symbol_type: str
    file: str
    line: int
    current_signature: str
    return_type: str
    callers: list[CallerInfo]
    test_coverage: TestCoverage
    risk_assessment: RiskAssessment
    suggestions: list[str] = field(default_factory=list)


@dataclass
class ChangeIssue:
    severity: IssueSeverity
    file: str
    line: int
    caller_function: str = ""
    problem: str = ""
    current_call: str = ""
    suggested_fix: str = ""


@dataclass
class PostChangeReport:
    status: ValidationStatus
    new_signature: str = ""
    signature_changes: list[str] = field(default_factory=list)
    issues: list[ChangeIssue] = field(default_factory=list)
    warnings: list[ChangeIssue] = field(default_factory=list)
    type_errors: list[str] = field(default_factory=list)
    suggested_actions: list[str] = field(default_factory=list)


@dataclass
class ChangeScopeResult:
    symbol: str
    symbol_type: str
    scope: SymbolScope
    caller_count: int
    is_exported: bool
    has_tests: bool
    risk_level: RiskLevel
    recommendation: str


# ============================================================
# 诊断结果
# ============================================================

@dataclass
class Diagnostic:
    file: str
    line: int
    column: int
    end_line: int
    end_column: int
    severity: str
    message: str
    rule: str


@dataclass
class DiagnosticsResult:
    diagnostics: list[Diagnostic]
    error_count: int
    warning_count: int
    files_analyzed: int


# ============================================================
# 序列化辅助
# ============================================================

def to_dict(obj: Any) -> Any:
    """将 dataclass 转为可 JSON 序列化的 dict，Enum → .value。"""
    if hasattr(obj, "__dataclass_fields__"):
        return {k: to_dict(v) for k, v in asdict(obj).items()}
    if isinstance(obj, enum.Enum):
        return obj.value
    if isinstance(obj, dict):
        return {k: to_dict(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [to_dict(item) for item in obj]
    return obj

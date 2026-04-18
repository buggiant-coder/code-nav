"""高级工作流工具的业务逻辑实现。

提供改前分析、改后验证、影响范围评估三个工具，
编排低级工具和引擎调用，生成结构化报告。
"""
from __future__ import annotations

import re
from pathlib import Path

from code_nav.engines import EngineManager
from code_nav.models import (
    ChangeScopeResult, PreChangeReport, PostChangeReport,
    CallerInfo, TestCoverage, RiskAssessment, ChangeIssue,
    RiskLevel, SymbolScope, ChangeType, ValidationStatus, IssueSeverity,
)
from code_nav.tools import primitives
from code_nav.utils.project import find_test_files, find_test_functions


# ============================================================
# get_change_scope — 快速评估影响范围
# ============================================================

async def get_change_scope(
    mgr: EngineManager,
    file: str,
    symbol: str = "",
    line: int = 0,
) -> ChangeScopeResult:
    """快速评估修改一个符号的影响范围和风险等级。"""
    # 1. 获取符号信息和引用
    ref_result = await primitives.find_references(
        mgr, file, line=line, symbol=symbol, include_definition=True,
    )
    symbol_name = ref_result.symbol or symbol
    symbol_type = ref_result.symbol_type or ""

    # 排除定义本身，统计调用方数量
    caller_count = ref_result.total_count
    if ref_result.definition:
        caller_count = max(0, caller_count - 1)

    # 2. 获取符号作用域
    scope = await _get_symbol_scope(mgr, file, symbol_name, line)

    # 3. 判断是否被其他文件引用（是否导出）
    other_file_refs = [
        r for r in ref_result.references
        if r.file != file
    ]
    is_exported = len(other_file_refs) > 0

    # 4. 查找测试文件
    test_files = find_test_files(symbol_name, file)
    has_tests = len(test_files) > 0

    # 5. 风险评估
    risk_level = _assess_risk_level(caller_count, is_exported, scope)

    # 6. 生成建议
    recommendation = _build_recommendation(
        symbol_name, caller_count, is_exported, has_tests, risk_level,
    )

    return ChangeScopeResult(
        symbol=symbol_name,
        symbol_type=symbol_type,
        scope=scope,
        caller_count=caller_count,
        is_exported=is_exported,
        has_tests=has_tests,
        risk_level=risk_level,
        recommendation=recommendation,
    )


# ============================================================
# pre_change_analysis — 改前深度分析
# ============================================================

async def pre_change_analysis(
    mgr: EngineManager,
    file: str,
    symbol: str = "",
    line: int = 0,
    change_type: str = "other",
) -> PreChangeReport:
    """在修改代码前，分析符号的完整影响面。"""
    # 1. 获取符号信息（签名、返回类型、文档）
    pos = _resolve_position(mgr, file, symbol, line)
    if pos:
        line, col = pos
    else:
        col = 0

    info = await _get_best_symbol_info(mgr, file, line, col)
    symbol_name = info.name if info else (symbol or "")
    symbol_type = info.symbol_type.value if info else ""
    current_signature = info.signature if info else ""
    return_type = info.return_type if info else ""

    # 2. 查找所有引用
    ref_result = await primitives.find_references(
        mgr, file, line=line, column=col, include_definition=False,
    )

    # 3. 分析每个调用方
    callers = _analyze_callers(ref_result.references, file)

    # 4. 测试覆盖
    test_files = find_test_files(symbol_name, file)
    test_functions: list[str] = []
    for tf in test_files:
        test_functions.extend(find_test_functions(tf, symbol_name))
    test_coverage = TestCoverage(
        test_files=test_files,
        test_functions=test_functions,
        has_direct_tests=len(test_functions) > 0,
    )

    # 5. 风险评估
    scope = await _get_symbol_scope(mgr, file, symbol_name, line)
    other_file_refs = [r for r in ref_result.references if r.file != file]
    is_exported = len(other_file_refs) > 0
    risk = _build_risk_assessment(
        len(callers), is_exported, scope, change_type,
    )

    # 6. 生成建议
    suggestions = _build_suggestions(
        symbol_name, change_type, callers, test_coverage, risk,
    )

    return PreChangeReport(
        symbol=symbol_name,
        symbol_type=symbol_type,
        file=file,
        line=line,
        current_signature=current_signature,
        return_type=return_type,
        callers=callers,
        test_coverage=test_coverage,
        risk_assessment=risk,
        suggestions=suggestions,
    )


# ============================================================
# post_change_validate — 改后验证
# ============================================================

async def post_change_validate(
    mgr: EngineManager,
    file: str,
    symbol: str = "",
    line: int = 0,
    original_signature: str = "",
) -> PostChangeReport:
    """修改代码后，验证调用方是否兼容新签名。"""
    # 1. 获取修改后的新签名
    pos = _resolve_position(mgr, file, symbol, line)
    if pos:
        line, col = pos
    else:
        col = 0

    info = await _get_best_symbol_info(mgr, file, line, col)
    new_signature = info.signature if info else ""
    symbol_name = info.name if info else (symbol or "")

    # 2. 对比新旧签名
    signature_changes = _diff_signatures(original_signature, new_signature)

    # 3. 如果签名没变，直接返回 clean
    if not signature_changes and not original_signature:
        return PostChangeReport(
            status=ValidationStatus.CLEAN,
            new_signature=new_signature,
        )

    # 4. 重新查找所有调用方
    ref_result = await primitives.find_references(
        mgr, file, line=line, column=col, include_definition=False,
    )

    # 5. 检查每个调用方是否兼容
    issues: list[ChangeIssue] = []
    warnings: list[ChangeIssue] = []

    for ref in ref_result.references:
        # 只检查调用类型的引用
        if ref.ref_type.value not in ("call", "other"):
            continue
        issue = _check_caller_compatibility(
            ref, original_signature, new_signature, signature_changes,
        )
        if issue:
            if issue.severity == IssueSeverity.ERROR:
                issues.append(issue)
            else:
                warnings.append(issue)

    # 6. 确定状态
    if issues:
        status = ValidationStatus.BREAKING_CHANGES_DETECTED
    elif signature_changes:
        status = ValidationStatus.CLEAN
    else:
        status = ValidationStatus.CLEAN

    # 7. 生成建议
    suggested_actions = _build_post_change_actions(issues, warnings)

    return PostChangeReport(
        status=status,
        new_signature=new_signature,
        signature_changes=signature_changes,
        issues=issues,
        warnings=warnings,
        suggested_actions=suggested_actions,
    )


# ============================================================
# 内部辅助函数
# ============================================================

def _resolve_position(
    mgr: EngineManager, file: str, symbol: str, line: int,
) -> tuple[int, int] | None:
    """统一解析符号位置。"""
    if symbol and not line:
        return mgr.resolve_symbol(file, symbol)
    if line:
        return (line, 0)
    return None


async def _get_best_symbol_info(mgr, file, line, column):
    """从优先级最高的引擎获取符号信息。"""
    language = mgr.detect_language(file)
    engines = mgr.get_engines_for(language)
    for engine in engines:
        info = await engine.get_symbol_info(file, line, column)
        if info:
            return info
    return None


async def _get_symbol_scope(
    mgr: EngineManager, file: str, symbol: str, line: int,
) -> SymbolScope:
    """获取符号的作用域。"""
    if line:
        language = mgr.detect_language(file)
        engines = mgr.get_engines_for(language)
        for engine in engines:
            info = await engine.get_symbol_info(file, line, 0)
            if info:
                return info.scope
    # 回退：用命名规则推断
    if symbol.startswith("__") and symbol.endswith("__"):
        return SymbolScope.MODULE_PUBLIC
    if symbol.startswith("_"):
        return SymbolScope.PRIVATE
    return SymbolScope.MODULE_PUBLIC


def _assess_risk_level(
    caller_count: int, is_exported: bool, scope: SymbolScope,
) -> RiskLevel:
    """根据调用方数量、导出状态、作用域判断风险等级。"""
    if caller_count == 0:
        return RiskLevel.LOW
    if scope == SymbolScope.PRIVATE:
        return RiskLevel.LOW
    if is_exported and caller_count >= 10:
        return RiskLevel.HIGH
    if is_exported or caller_count >= 5:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def _build_recommendation(
    symbol: str, caller_count: int, is_exported: bool,
    has_tests: bool, risk_level: RiskLevel,
) -> str:
    """根据风险评估生成建议文本。"""
    parts = []
    if risk_level == RiskLevel.HIGH:
        parts.append(f"HIGH RISK: '{symbol}' has {caller_count} callers across multiple files.")
        parts.append("Run pre_change_analysis before modifying.")
    elif risk_level == RiskLevel.MEDIUM:
        parts.append(f"MEDIUM RISK: '{symbol}' has {caller_count} callers.")
        if is_exported:
            parts.append("Symbol is used by other modules — check callers before changing signature.")
    else:
        parts.append(f"LOW RISK: '{symbol}' has {caller_count} callers.")
        parts.append("Safe to modify with basic verification.")

    if not has_tests:
        parts.append("WARNING: No tests found — add tests before or after modification.")
    else:
        parts.append("Tests exist — run them after modification.")

    return " ".join(parts)


def _analyze_callers(references, source_file: str) -> list[CallerInfo]:
    """将引用列表转换为 CallerInfo，读取调用上下文。"""
    callers: list[CallerInfo] = []
    for ref in references:
        # 读取调用方的上下文（前后各 2 行）
        context_lines = _read_context(ref.file, ref.line, context=2)
        # 推断调用方所在的函数名
        enclosing_func = _find_enclosing_function(ref.file, ref.line)
        callers.append(CallerInfo(
            file=ref.file,
            line=ref.line,
            function=enclosing_func,
            call_expression=ref.context,
        ))
    return callers


def _find_enclosing_function(file: str, line: int) -> str:
    """查找指定行所在的函数/方法名。向上搜索最近的 def/class。"""
    try:
        with open(file, encoding="utf-8") as f:
            lines = f.readlines()
    except (OSError, UnicodeDecodeError):
        return "<unknown>"

    # 从目标行向上查找
    for i in range(min(line - 1, len(lines) - 1), -1, -1):
        text = lines[i]
        m = re.match(r'\s*(async\s+)?def\s+(\w+)', text)
        if m:
            return m.group(2)
        m = re.match(r'\s*class\s+(\w+)', text)
        if m:
            return m.group(1)
    return "<module>"


def _read_context(file: str, line: int, context: int = 2) -> list[str]:
    """读取文件中指定行的前后上下文。"""
    try:
        with open(file, encoding="utf-8") as f:
            lines = f.readlines()
    except (OSError, UnicodeDecodeError):
        return []
    start = max(0, line - 1 - context)
    end = min(len(lines), line + context)
    return [l.rstrip("\n") for l in lines[start:end]]


def _build_risk_assessment(
    caller_count: int, is_exported: bool,
    scope: SymbolScope, change_type: str,
) -> RiskAssessment:
    """构建详细的风险评估。"""
    reasons: list[str] = []
    level = _assess_risk_level(caller_count, is_exported, scope)

    if caller_count > 0:
        reasons.append(f"{caller_count} callers found")
    if is_exported:
        reasons.append("Symbol is used by other modules")
    if scope == SymbolScope.MODULE_PUBLIC:
        reasons.append("Public symbol — external consumers may depend on it")

    # 根据变更类型调整风险
    ct = change_type.lower()
    if ct in ("modify_signature", "rename", "remove"):
        if level == RiskLevel.LOW and caller_count > 0:
            level = RiskLevel.MEDIUM
        reasons.append(f"Change type '{ct}' may break callers")
    elif ct == "change_return_type":
        reasons.append("Return type change — callers using return value may break")

    return RiskAssessment(
        level=level,
        caller_count=caller_count,
        reasons=reasons,
    )


def _build_suggestions(
    symbol: str, change_type: str,
    callers: list[CallerInfo], test_coverage: TestCoverage,
    risk: RiskAssessment,
) -> list[str]:
    """根据分析结果生成修改建议。"""
    suggestions: list[str] = []

    if risk.level == RiskLevel.HIGH:
        suggestions.append(
            "Consider making the change backward-compatible "
            "(e.g., add new parameter with default value instead of removing old one)."
        )

    if callers:
        # 列出受影响的文件
        affected_files = sorted(set(c.file for c in callers))
        if len(affected_files) > 1:
            suggestions.append(
                f"Update callers in {len(affected_files)} files: "
                + ", ".join(Path(f).name for f in affected_files[:5])
                + ("..." if len(affected_files) > 5 else "")
            )

    if not test_coverage.has_direct_tests:
        suggestions.append(f"Add tests for '{symbol}' before modifying.")
    else:
        suggestions.append(
            f"Run existing tests after modification: "
            + ", ".join(test_coverage.test_functions[:3])
        )

    ct = change_type.lower()
    if ct == "modify_signature":
        suggestions.append("After modification, run post_change_validate to check caller compatibility.")
    elif ct == "rename":
        suggestions.append("Use find_references to locate all usages, then rename them all.")
    elif ct == "remove":
        suggestions.append("Verify no callers remain before removing. Check for dynamic/reflection usage.")

    return suggestions


# ---- post_change_validate 辅助 ----

def _diff_signatures(old_sig: str, new_sig: str) -> list[str]:
    """对比新旧签名，返回变更列表。"""
    if not old_sig or not new_sig:
        return []
    if old_sig == new_sig:
        return []

    changes: list[str] = []
    old_params = _parse_params(old_sig)
    new_params = _parse_params(new_sig)

    old_names = [p[0] for p in old_params]
    new_names = [p[0] for p in new_params]

    # 检查删除的参数
    for name in old_names:
        if name not in new_names:
            changes.append(f"Parameter '{name}' removed")

    # 检查新增的参数
    old_dict = {p[0]: p for p in old_params}
    new_dict = {p[0]: p for p in new_params}
    for name in new_names:
        if name not in old_names:
            param = new_dict[name]
            if param[2]:  # has default
                changes.append(f"Parameter '{name}' added (with default)")
            else:
                changes.append(f"Parameter '{name}' added (REQUIRED — may break callers)")

    # 检查参数顺序变化
    common = [n for n in new_names if n in old_names]
    old_order = [n for n in old_names if n in common]
    if common != old_order:
        changes.append("Parameter order changed")

    # 检查默认值变化
    for name in common:
        old_p = old_dict[name]
        new_p = new_dict[name]
        if old_p[2] and not new_p[2]:
            changes.append(f"Parameter '{name}' default removed (now REQUIRED)")
        elif not old_p[2] and new_p[2]:
            changes.append(f"Parameter '{name}' now has default value")

    return changes


def _parse_params(signature: str) -> list[tuple[str, str, bool]]:
    """解析签名字符串中的参数。返回 [(name, type_hint, has_default), ...]。"""
    # 提取括号内的参数部分（支持多行签名）
    m = re.search(r'\((.+)\)', signature, re.DOTALL)
    if not m:
        return []
    params_str = m.group(1)
    params: list[tuple[str, str, bool]] = []
    for part in params_str.split(","):
        part = part.strip()
        if not part or part == "self" or part == "cls":
            continue
        has_default = "=" in part
        # 去掉默认值部分
        name_part = part.split("=")[0].strip()
        # 去掉类型注解
        name = name_part.split(":")[0].strip()
        type_hint = ""
        if ":" in name_part:
            type_hint = name_part.split(":", 1)[1].strip()
        # 清理 * 和 ** 前缀
        name = name.lstrip("*")
        if name:
            params.append((name, type_hint, has_default))
    return params


def _check_caller_compatibility(
    ref, old_sig: str, new_sig: str, sig_changes: list[str],
) -> ChangeIssue | None:
    """检查一个调用方是否兼容新签名。"""
    if not sig_changes:
        return None

    # 检查是否有 REQUIRED 参数新增或默认值移除
    breaking_changes = [c for c in sig_changes if "REQUIRED" in c or "removed" in c.lower()]
    if not breaking_changes:
        return None

    return ChangeIssue(
        severity=IssueSeverity.WARNING,
        file=ref.file,
        line=ref.line,
        caller_function=_find_enclosing_function(ref.file, ref.line),
        problem="; ".join(breaking_changes),
        current_call=ref.context,
        suggested_fix="Update this call to match the new signature.",
    )


def _build_post_change_actions(
    issues: list[ChangeIssue], warnings: list[ChangeIssue],
) -> list[str]:
    """生成改后建议操作列表。"""
    actions: list[str] = []
    if issues:
        affected = sorted(set(i.file for i in issues))
        actions.append(
            f"BREAKING: {len(issues)} callers need updating in: "
            + ", ".join(Path(f).name for f in affected[:5])
        )
    if warnings:
        actions.append(f"{len(warnings)} callers may need review.")
    if not issues and not warnings:
        actions.append("All callers appear compatible. Run tests to confirm.")
    actions.append("Run tests to verify the change.")
    return actions

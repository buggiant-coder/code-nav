"""项目检测、语言识别、测试文件发现。"""
from __future__ import annotations

import os
from pathlib import Path

# 项目根目录标记文件
_PROJECT_MARKERS = {
    "pyproject.toml", "setup.py", "setup.cfg", "package.json",
    "go.mod", "Cargo.toml", ".git", "requirements.txt", "Makefile",
}

# 文件扩展名 → 语言
_EXT_TO_LANG: dict[str, str] = {
    ".py": "python", ".pyi": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "tsx", ".jsx": "jsx",
    ".go": "go", ".rs": "rust",
    ".java": "java", ".kt": "kotlin",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp", ".cc": "cpp",
}

# 常见测试目录/文件名模式
_TEST_DIR_NAMES = {"tests", "test", "testing", "__tests__", "spec"}
_TEST_FILE_PREFIXES = ("test_", "tests_")
_TEST_FILE_SUFFIXES = ("_test.py", "_tests.py", "_spec.py")


def is_test_file(path: str) -> bool:
    """判断文件是否为测试文件。

    基于文件名前缀/后缀和直接父目录名判断。
    不检查祖先目录，避免 tests/fixtures/sample_project/checkout.py 被误判。
    """
    name = os.path.basename(path)
    if any(name.startswith(p) for p in _TEST_FILE_PREFIXES):
        return True
    if any(name.endswith(s) for s in _TEST_FILE_SUFFIXES):
        return True
    parent = Path(path).parent.name
    return parent in _TEST_DIR_NAMES


def find_project_root(file: str) -> str:
    """从文件路径向上查找项目根目录。"""
    path = Path(file).resolve().parent
    while path != path.parent:
        if any((path / m).exists() for m in _PROJECT_MARKERS):
            return str(path)
        path = path.parent
    return str(Path(file).resolve().parent)


def detect_language(file: str) -> str:
    """从文件扩展名推断语言。"""
    _, ext = os.path.splitext(file)
    return _EXT_TO_LANG.get(ext, "python")


def find_test_files(symbol_name: str, source_file: str) -> list[str]:
    """查找与指定符号/文件关联的测试文件。

    策略：
    1. 查找同目录或 tests/ 子目录下的 test_*.py
    2. 匹配文件名中包含源文件模块名的测试文件
    3. 向上查找项目级 tests/ 目录
    """
    source_path = Path(source_file).resolve()
    module_name = source_path.stem  # e.g. "order" from "order.py"
    project_root = Path(find_project_root(source_file))

    found: list[str] = []
    seen: set[str] = set()

    def _add(p: Path) -> None:
        s = str(p)
        if s not in seen and p.exists():
            seen.add(s)
            found.append(s)

    # 1. 同目录下的 test_<module>.py
    same_dir = source_path.parent / f"test_{module_name}.py"
    _add(same_dir)

    # 2. 同目录的 tests/ 子目录
    for test_dir in _TEST_DIR_NAMES:
        d = source_path.parent / test_dir
        if d.is_dir():
            for f in d.glob(f"test_{module_name}*.py"):
                _add(f)
            for f in d.glob(f"*_{module_name}_test.py"):
                _add(f)

    # 3. 项目根目录的 tests/ 目录
    for test_dir in _TEST_DIR_NAMES:
        d = project_root / test_dir
        if d.is_dir():
            # 递归搜索匹配的测试文件
            for f in d.rglob(f"test_{module_name}*.py"):
                _add(f)
            for f in d.rglob(f"*_{module_name}_test.py"):
                _add(f)

    return found


def find_test_functions(test_file: str, symbol_name: str) -> list[str]:
    """在测试文件中查找与指定符号相关的测试函数名。

    简单的文本匹配：找 def test_*symbol_name* 的函数。
    """
    results: list[str] = []
    try:
        with open(test_file, encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped.startswith("def test_") and symbol_name.lower() in stripped.lower():
                    # 提取函数名
                    name = stripped.split("(")[0].replace("def ", "").strip()
                    results.append(name)
    except (OSError, UnicodeDecodeError):
        pass
    return results

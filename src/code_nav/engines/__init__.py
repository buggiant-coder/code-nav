"""引擎管理器 — 根据语言选择引擎、编排多引擎调用。"""
from __future__ import annotations

from code_nav.engines.base import AnalysisEngine, PatternSearchEngine
from code_nav.engines.sg_engine import SgEngine
from code_nav.engines.pyright_engine import PyrightEngine
from code_nav.utils.project import detect_language as _detect_language


class EngineManager:
    """管理所有分析引擎的生命周期和选择。"""

    def __init__(self, project_path: str | None = None):
        self._project_path = project_path
        self.sg = SgEngine()
        self.pyright = PyrightEngine(project_path=project_path)
        # pyright 优先（Python 语义分析更精确），ast-grep 作为补充
        self._engines: list[AnalysisEngine] = [self.pyright, self.sg]

    def get_engines_for(self, language: str) -> list[AnalysisEngine]:
        """返回支持指定语言的引擎列表，按优先级排序。

        Python → [pyright, ast-grep]
        其他   → [ast-grep]
        """
        return [e for e in self._engines if e.supports(language)]

    def get_pattern_engine(self) -> PatternSearchEngine:
        return self.sg

    def detect_language(self, file: str) -> str:
        return _detect_language(file)

    def resolve_symbol(self, file: str, symbol: str) -> tuple[int, int] | None:
        """在文件中按符号名查找定义位置。Python 用 pyright，其他语言回退到文本搜索。"""
        language = self.detect_language(file)
        if language == "python":
            return self.pyright.resolve_symbol(file, symbol)
        return self._text_resolve(file, symbol)

    @staticmethod
    def _text_resolve(file: str, symbol: str) -> tuple[int, int] | None:
        """文本回退：在文件中搜索 def/class/function 等关键字后的符号名。"""
        import re
        try:
            with open(file, encoding="utf-8") as f:
                for i, line_text in enumerate(f, 1):
                    if re.search(rf'\b(def|class|function|func|const|let|var)\s+{re.escape(symbol)}\b', line_text):
                        col = line_text.find(symbol)
                        return (i, max(col, 0))
        except (OSError, UnicodeDecodeError):
            pass
        return None

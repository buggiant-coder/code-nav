"""分析引擎的抽象基类。

每个具体引擎（pyright、ast-grep）实现此接口。
引擎只负责底层分析，不做结果合并或工具输出格式化。
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from code_nav.models import Reference, Definition, SymbolInfo, AstMatch


class AnalysisEngine(ABC):
    """代码分析引擎的抽象基类。"""

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @property
    @abstractmethod
    def supported_languages(self) -> list[str]:
        ...

    def supports(self, language: str) -> bool:
        return "*" in self.supported_languages or language in self.supported_languages

    @abstractmethod
    async def find_references(
        self, file: str, line: int, column: int = 0,
    ) -> list[Reference]:
        ...

    @abstractmethod
    async def go_to_definition(
        self, file: str, line: int, column: int = 0,
    ) -> list[Definition]:
        ...

    @abstractmethod
    async def get_symbol_info(
        self, file: str, line: int, column: int = 0,
    ) -> SymbolInfo | None:
        ...


class PatternSearchEngine(ABC):
    """支持 AST 模式搜索的引擎抽象。"""

    @abstractmethod
    async def search(
        self,
        pattern: str,
        language: str,
        path: str = ".",
        limit: int = 50,
    ) -> list[AstMatch]:
        ...

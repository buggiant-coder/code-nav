"""基于文件 mtime 的简单 TTL 缓存。

用于缓存分析引擎的结果，避免重复解析。
文件被修改后缓存自动失效。
"""
from __future__ import annotations

import os
import time
from typing import Any


class FileCache:
    """基于文件 mtime 的缓存。

    - key 为 (file_path, extra_key) 的元组
    - 缓存在文件 mtime 变化或 TTL 过期时失效
    """

    def __init__(self, ttl: float = 30.0):
        """
        Args:
            ttl: 缓存最大存活时间（秒），默认 30s
        """
        self._ttl = ttl
        self._store: dict[tuple, _CacheEntry] = {}

    def get(self, file: str, key: str = "") -> Any | None:
        """获取缓存，未命中返回 None。"""
        cache_key = (file, key)
        entry = self._store.get(cache_key)
        if entry is None:
            return None
        # 检查 TTL
        if time.monotonic() - entry.created_at > self._ttl:
            del self._store[cache_key]
            return None
        # 检查文件 mtime
        try:
            current_mtime = os.path.getmtime(file)
        except OSError:
            del self._store[cache_key]
            return None
        if current_mtime != entry.file_mtime:
            del self._store[cache_key]
            return None
        return entry.value

    def set(self, file: str, value: Any, key: str = "") -> None:
        """写入缓存。"""
        try:
            mtime = os.path.getmtime(file)
        except OSError:
            return  # 文件不存在则不缓存
        cache_key = (file, key)
        self._store[cache_key] = _CacheEntry(
            value=value,
            file_mtime=mtime,
            created_at=time.monotonic(),
        )

    def invalidate(self, file: str) -> None:
        """清除指定文件的所有缓存。"""
        to_remove = [k for k in self._store if k[0] == file]
        for k in to_remove:
            del self._store[k]

    def clear(self) -> None:
        """清除所有缓存。"""
        self._store.clear()

    @property
    def size(self) -> int:
        return len(self._store)


class _CacheEntry:
    __slots__ = ("value", "file_mtime", "created_at")

    def __init__(self, value: Any, file_mtime: float, created_at: float):
        self.value = value
        self.file_mtime = file_mtime
        self.created_at = created_at

"""进程内图像缓存。

质检 Agent 需要**像素级数据**做客观指标计算，但：
- 把 bytes 塞进图状态会破坏状态的可序列化性（未来接 checkpointer 会直接报错）；
- 把 base64 塞进 SSE 帧会让前端帧体积膨胀数十倍；
- 每次都重新下载会白白多花一次网络往返。

因此在进程内做一个带 TTL 与容量上限的小缓存，键为 task_id。
缓存未命中时由调用方按 URL 回源，语义上退化为「多一次下载」，不影响正确性。
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import NamedTuple


class Blob(NamedTuple):
    data: bytes
    content_type: str
    created_at: float


class BlobStore:
    def __init__(self, *, max_items: int = 64, ttl_seconds: float = 900.0) -> None:
        self._lock = threading.Lock()
        self._items: OrderedDict[str, Blob] = OrderedDict()
        self.max_items = max_items
        self.ttl = ttl_seconds

    def put(self, key: str, data: bytes, content_type: str = "image/png") -> None:
        with self._lock:
            self._items[key] = Blob(data, content_type, time.time())
            self._items.move_to_end(key)
            self._evict()

    def get(self, key: str) -> Blob | None:
        with self._lock:
            blob = self._items.get(key)
            if blob is None:
                return None
            if time.time() - blob.created_at > self.ttl:
                self._items.pop(key, None)
                return None
            self._items.move_to_end(key)
            return blob

    def pop(self, key: str) -> Blob | None:
        with self._lock:
            return self._items.pop(key, None)

    def _evict(self) -> None:
        now = time.time()
        for key in [k for k, v in self._items.items() if now - v.created_at > self.ttl]:
            self._items.pop(key, None)
        while len(self._items) > self.max_items:
            self._items.popitem(last=False)

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"items": len(self._items), "bytes": sum(len(v.data) for v in self._items.values())}


blobs = BlobStore()

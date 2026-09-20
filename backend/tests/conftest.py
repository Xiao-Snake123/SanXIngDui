"""pytest 公共夹具。

两条纪律：

1. **不引入 `pytest-asyncio`**：异步用例统一用 `asyncio.run()` 包裹，少一个依赖、
   少一层事件循环配置，也避免「服务代码用 uvloop、测试用默认循环」这类环境差异。

2. **测试必须离线可复现**：默认关闭所有需要网络的出图通道（DashScope、第三方免鉴权通道），
   让出图稳定落在本地占位 provider 上。
   否则一次 `pytest` 会去请求公网图片服务 —— 既慢（实测从 3s 涨到 234s），
   又会因为第三方限流而随机失败。需要验证真实出图链路的场景由
   `app/eval/harness.py` 承担，那里是显式选择，不是隐式副作用。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


@pytest.fixture(scope="session", autouse=True)
def hermetic_image_channels():
    """把出图链路固定到本地占位 provider，保证测试离线、快速、确定性。"""
    from app.core.config import settings

    original = {
        "freeimage_enabled": settings.freeimage_enabled,
        "dashscope_api_key": settings.dashscope_api_key,
    }
    settings.freeimage_enabled = False
    settings.dashscope_api_key = ""

    yield

    for key, value in original.items():
        setattr(settings, key, value)


@pytest.fixture(scope="session", autouse=True)
def hermetic_storage():
    """把存储层钉在进程内实现上，理由与出图通道完全相同。

    本机确实跑着 PostgreSQL 与 Redis，但**单测绝不能依赖它们**：
    - 测试会写脏真实数据库，跑几次之后 `/api/tasks` 的结果就没法看了；
    - CI / 别人的机器上没有这两个服务，测试会变成「只在我电脑上绿」。

    真实存储链路的验证由 `scripts/check_storage.py` 承担 —— 显式执行，
    与 app/eval/harness.py 对真实出图链路的处理方式一致。
    """
    from app.core.config import settings

    original = {
        "database_url": settings.database_url,
        "redis_url": settings.redis_url,
    }
    settings.database_url = ""
    settings.redis_url = ""

    yield

    for key, value in original.items():
        setattr(settings, key, value)


@pytest.fixture(scope="session")
def backend_root() -> Path:
    return BACKEND_ROOT


@pytest.fixture(scope="session")
def corpus_dir(backend_root: Path) -> Path:
    return backend_root / "data" / "corpus"


@pytest.fixture(scope="module")
def client():
    """FastAPI 测试客户端。

    放在 conftest 而不是各文件重复定义：原先 `test_api.py` 与
    `test_conversation.py` 各有一份完全相同的实现，第三个需要它的文件
    （`test_qa_answerer.py`）就该共用，而不是复制第三份。

    必须用 `with` 进入，否则不会触发 lifespan —— 那样存储/检索的初始化与
    清理都跑不到，测试会通过但测的不是真实启动路径。
    """
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as instance:
        yield instance

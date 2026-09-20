"""HTTP 客户端工厂与「本机地址」判定。

这里解决一个真实环境下的坑：**Windows 上 httpx 默认会读取系统 WinINET 代理**
（经由 `urllib.request.getproxies()` 读注册表 `Internet Settings`），
而该读取**不包含 ProxyOverride 例外名单**。

后果：用户开着 Clash 之类的代理时，连 `http://127.0.0.1:8123`（本项目后端）这样的
本机请求也会被丢给代理，得到 502 空响应，且错误信息毫无指向性
（表现为「服务明明起着，但请求就是 502」）。

因此本模块统一约定：
- 访问**本机 / 私有网段**的客户端一律 `trust_env=False`（绕过系统代理）；
- 访问**公网**（DashScope 等）的客户端保持 `trust_env=True`（该走代理还是得走）。
"""

from __future__ import annotations

import ipaddress
import os
from urllib.parse import urlparse

import httpx

LOCAL_HOSTNAMES = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal"}


def is_local_url(url: str) -> bool:
    """判断 URL 是否指向本机或私有网段。"""
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return False
    if not host:
        return False
    if host.lower() in LOCAL_HOSTNAMES:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_private or address.is_loopback


def make_async_client(
    *,
    timeout: float | httpx.Timeout = 60.0,
    headers: dict[str, str] | None = None,
    trust_env: bool = True,
    base_url: str | None = None,
) -> httpx.AsyncClient:
    resolved = timeout if isinstance(timeout, httpx.Timeout) else httpx.Timeout(timeout, connect=10.0)
    return httpx.AsyncClient(
        base_url=base_url or "",
        timeout=resolved,
        headers=headers or {},
        trust_env=trust_env,
        limits=httpx.Limits(max_connections=16, max_keepalive_connections=8),
    )


def _redact_proxy_url(value: str) -> str:
    """脱敏代理 URL 里的 userinfo。

    代理地址常写成 http://user:password@proxy:port —— 原样输出等于把内网账号密码
    暴露给任何能访问 /api/health 的人（该端点无鉴权）。只保留「配没配、指向哪」。
    """
    if "@" not in value:
        return value
    scheme, sep, rest = value.partition("://")
    if not sep:
        return value
    _, _, host = rest.rpartition("@")
    return f"{scheme}://***:***@{host}"


def proxy_diagnostics() -> dict[str, object]:
    """把「当前进程会走哪个代理」显式暴露出来，用于 /api/health 自检（凭据已脱敏）。"""
    env_keys = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")
    env = {
        key: _redact_proxy_url(str(os.environ.get(key)))
        for key in env_keys
        if os.environ.get(key)
    }

    system: dict[str, str] = {}
    try:
        import urllib.request

        system = {str(k): _redact_proxy_url(str(v)) for k, v in urllib.request.getproxies().items()}
    except Exception:  # noqa: BLE001
        pass

    return {"env": env, "system_wininet": system}

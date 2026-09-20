"""探测可用的免鉴权出图通道（用于无 API Key / 无 GPU 环境下的兜底）。

本脚本**不写入任何代码路径**，仅做可用性与延迟验证。
"""

from __future__ import annotations

import time
from pathlib import Path
from urllib.parse import quote

import httpx

# 以本文件位置推出 backend/var，保证从任意工作目录调用都能写盘
VAR_DIR = Path(__file__).resolve().parents[1] / "var"
VAR_DIR.mkdir(parents=True, exist_ok=True)

PROMPT = (
    "bronze standing figure of Sanxingdui, tall flared crown, three-layer robe, "
    "matte oxidized bronze patina, dark museum hall, documentary photograph"
)

CANDIDATES = [
    (
        "pollinations-flux",
        f"https://image.pollinations.ai/prompt/{quote(PROMPT)}"
        "?width=768&height=960&model=flux&nologo=true&seed=42",
    ),
    (
        "pollinations-turbo",
        f"https://image.pollinations.ai/prompt/{quote(PROMPT)}"
        "?width=768&height=960&model=turbo&nologo=true&seed=42",
    ),
]


def main() -> None:
    for name, url in CANDIDATES:
        started = time.perf_counter()
        try:
            response = httpx.get(url, timeout=90.0, follow_redirects=True, trust_env=True)
            elapsed = (time.perf_counter() - started) * 1000
            ctype = response.headers.get("content-type", "")
            print(
                f"{name:24} status={response.status_code} "
                f"len={len(response.content):>8} ctype={ctype:<16} {elapsed:>8.0f}ms"
            )
            if response.status_code == 200 and (
                response.content[:4] == b"\x89PNG" or response.content[:3] == b"\xff\xd8\xff"
            ):
                suffix = ".png" if response.content[:4] == b"\x89PNG" else ".jpg"
                # 用绝对路径：脚本可能从仓库根目录被调用，相对路径会 FileNotFoundError
                out = VAR_DIR / f"probe_{name}{suffix}"
                out.write_bytes(response.content)
                print(f"{'':24} 已保存 -> {out}")
        except Exception as exc:
            print(f"{name:24} FAILED: {type(exc).__name__}: {str(exc)[:160]}")


if __name__ == "__main__":
    main()

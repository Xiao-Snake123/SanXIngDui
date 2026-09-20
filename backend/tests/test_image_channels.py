"""出图降级链测试。

核心诉求：**真实通道的瞬时可恢复失败，不该直接让用户看到占位图。**
占位图是「程序画的示意图」，用户看到它的第一反应就是「生图逻辑坏了 / 跟三星堆无关」。
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import httpx
import pytest

from app.imagegen.base import ImageRequest, ImageResult
from app.imagegen.freeimage import FreeImageProvider

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _provider(**kwargs) -> FreeImageProvider:
    kwargs.pop("fit_limit", None)  # 兼容不同测试的写法，预算统一由 settings 决定
    return FreeImageProvider(
        url_template="https://example.invalid/prompt/{prompt}?seed={seed}",
        model="flux",
        attempts=kwargs.pop("attempts", 3),
        backoff_seconds=kwargs.pop("backoff_seconds", 0.0),
        **kwargs,
    )


class _ScriptedClient:
    """按脚本依次返回响应/抛异常，并记录调用次数。"""

    def __init__(self, steps):
        self.steps = list(steps)
        self.calls = 0
        self.last_headers = None

    async def get(self, url, follow_redirects=True, headers=None):  # noqa: ARG002
        self.calls += 1
        self.last_headers = headers
        step = self.steps.pop(0) if self.steps else httpx.Response(200, content=PNG)
        if isinstance(step, Exception):
            raise step
        return step


def _run(coro):
    return asyncio.run(coro)


class TestRetryBehaviour:
    def test_rate_limit_wrapped_in_500_is_retried(self):
        """真实事故：上游把限流包在 500 里返回。

            HTTP 500  {"message":"Gen Sana request failed with 429:
                       Per-user limit of 300 RPM exceeded ..."}

        只看状态码会当成永久失败并降级到占位图。这里必须重试并最终成功。
        """
        limited = httpx.Response(
            500,
            json={
                "error": "Internal Server Error",
                "message": "Gen Sana request failed with 429: Per-user limit of 300 RPM exceeded",
            },
        )
        client = _ScriptedClient([limited, httpx.Response(200, content=PNG, headers={"content-type": "image/png"})])
        provider = _provider()
        provider._client = client

        with patch("app.imagegen.freeimage.archive_image", return_value="/media/x.png"):
            result = _run(provider.generate(ImageRequest(prompt="gold mask", kind="scene")))

        assert client.calls == 2, "限流被当成永久失败，没有重试"
        assert result.provider == "free-third-party"

    def test_transport_error_is_retried(self):
        client = _ScriptedClient([httpx.ConnectError("boom"), httpx.Response(200, content=PNG)])
        provider = _provider()
        provider._client = client

        with patch("app.imagegen.freeimage.archive_image", return_value="/media/x.png"):
            result = _run(provider.generate(ImageRequest(prompt="p", kind="scene")))

        assert client.calls == 2
        assert result.image_url == "/media/x.png"

    def test_persistent_429_eventually_raises(self):
        attempts = 3
        client = _ScriptedClient([httpx.Response(429, text="slow down") for _ in range(attempts)])
        provider = _provider(attempts=attempts)
        provider._client = client

        with pytest.raises(Exception) as excinfo:
            _run(provider.generate(ImageRequest(prompt="p", kind="scene")))

        assert client.calls == attempts, "重试次数与配置不符"
        assert "429" in str(excinfo.value)

    def test_client_error_is_not_retried(self):
        """400/404 这类是请求本身的问题，重试只是浪费配额。"""
        client = _ScriptedClient([httpx.Response(400, text="bad prompt")])
        provider = _provider()
        provider._client = client

        with pytest.raises(Exception):
            _run(provider.generate(ImageRequest(prompt="p", kind="scene")))

        assert client.calls == 1

    def test_non_image_body_is_rejected(self):
        """上游有时会返回 200 + HTML 错误页，直接当图片存下来会污染输出目录。"""
        client = _ScriptedClient([httpx.Response(200, content=b"<html>oops</html>")])
        provider = _provider(attempts=1)
        provider._client = client

        with pytest.raises(Exception) as excinfo:
            _run(provider.generate(ImageRequest(prompt="p", kind="scene")))

        assert "不是图像" in str(excinfo.value)


class TestPromptFitting:
    """提示词超预算时，必须保住**头部与尾部**。

    事故复盘：提示词按「基准描述 + 史料证据 + 回炉修正指令」拼接，修正指令在末尾。
    原先按尾部截断，把唯一变化的部分切掉了 —— 表现为「回炉了但分数一点没变」
    （实测两轮都是 0.716），看起来像模型能力不行，实际是修正指令根本没送到。

    但后来实测发现约束比这更强（`scripts/probe_freeimage_tail.py`）：
    出图通道有**有效窗口**，732 字时连「保留下来的尾部」都会被静默丢弃。
    所以保尾只是兜底，真正的修复是把修正指令**前置**到提示词开头 ——
    见 `test_conversation.py::test_revision_directive_survives_the_truncation_window`。
    """

    def test_short_prompt_is_untouched(self):
        text = "a short prompt"
        fitted, truncated = FreeImageProvider.fit_prompt(text, 900)
        assert fitted == text
        assert truncated is False

    def test_overlong_prompt_keeps_the_tail(self):
        head = "Key artefact: the gold mask of Sanxingdui. " * 40
        tail = "Revision round 1: lower the saturation and add hammer marks. Do not repeat."
        fitted, truncated = FreeImageProvider.fit_prompt(head + tail, 400)

        assert truncated is True
        assert len(fitted) <= 400
        assert tail[-40:] in fitted, "回炉修正指令被截掉了 —— 这正是回炉失效的原因"
        assert fitted.startswith(head[:40]), "主体描述不应丢失"

    def test_revision_round_prompts_stay_distinct_after_fitting(self):
        """两轮的提示词在压缩之后仍必须不同。

        如果压缩把它们变成同一个字符串，上游会按 URL 缓存返回同一张图，
        「不达标自动回炉」就退化成「原地重画」。
        """
        base = "Key artefact: the gold mask of Sanxingdui, hammered gold foil. " * 30
        round0 = base
        round1 = base + " Revision round 1: reduce specular highlights, add granular detail."

        fitted0, _ = FreeImageProvider.fit_prompt(round0, 400)
        fitted1, _ = FreeImageProvider.fit_prompt(round1, 400)
        assert fitted0 != fitted1

    def test_truncation_is_recorded_in_raw(self):
        """截断状态要落在 raw 里：排查「回炉无效」时第一个要看的就是它。"""
        long_prompt = "Key artefact: " + "x" * 3000
        provider = _provider(fit_limit=None)
        client = _ScriptedClient([httpx.Response(200, content=PNG, headers={"content-type": "image/png"})])
        provider._client = client

        with patch("app.imagegen.freeimage.archive_image", return_value="/media/x.png"):
            result = _run(provider.generate(ImageRequest(prompt=long_prompt, kind="scene")))

        assert result.raw["prompt_truncated"] is True
        assert result.raw["prompt_chars_total"] == len(long_prompt)
        assert result.raw["prompt_chars_sent"] <= result.raw["prompt_chars_total"]


class TestRetryableClassification:
    @pytest.mark.parametrize(
        ("status", "body", "expected"),
        [
            (429, "rate limited", True),
            (500, "internal", True),
            (503, "unavailable", True),
            (500, "Gen Sana request failed with 429: exceeded", True),
            (400, "bad prompt", False),
            (404, "not found", False),
            (200, "ok", False),
        ],
    )
    def test_classification(self, status, body, expected):
        response = httpx.Response(status, text=body)
        assert FreeImageProvider._is_retryable(response) is expected


class TestPlaceholderIsAlwaysLabelled:
    def test_local_placeholder_reports_itself_as_degraded(self):
        """占位图必须自带 degraded 标记与原因，否则前端无从提示用户。"""
        from PIL import Image  # noqa: F401 - 确认 Pillow 可用

        from app.imagegen.local import LocalPlaceholderProvider

        provider = LocalPlaceholderProvider()
        result = _run(provider.generate(ImageRequest(prompt="gold mask", kind="scene")))

        assert result.degraded is True
        assert result.error, "占位图必须带上「为什么没有真实出图」的原因"
        assert result.provider == "local-placeholder"

    def test_placeholder_is_flagged_as_non_generative(self):
        """占位图必须显式标记为非生成图。

        下游（质检）据此短路。没有这个标记，系统就会对一张程序画的示意图
        做「三星堆风格一致性」质检，得到永远不达标、且回炉也不可能改善的假结论 ——
        用户看到的是「回炉失败」，实际是「没有可判定的对象」。
        """
        from app.imagegen.local import LocalPlaceholderProvider

        provider = LocalPlaceholderProvider()
        result = _run(provider.generate(ImageRequest(prompt="gold mask", kind="scene")))

        assert result.generative is False
        assert result.to_dict()["generative"] is False

    def test_real_channels_are_marked_generative_by_default(self):
        assert ImageResult(image_url="x", provider="dashscope", latency_ms=1.0).generative is True


class TestQualityIsSkippedForPlaceholder:
    """占位图不应触发质检与回炉 —— 这是「回炉失败」的真实成因。"""

    def test_placeholder_run_marks_quality_skipped(self):
        from app.graph.runner import run_once

        result = _run(
            run_once(
                {
                    "kind": "scene",
                    "item": "黄金面具",
                    "scene": "三星堆祭祀坑",
                    "style": "考古档案照片",
                }
            )
        )

        qa = result.get("qa") or {}
        assert qa.get("skipped") is True, "占位图必须跳过质检，而不是给一个假分数"
        assert qa.get("score") is None
        assert qa.get("decision") == "skipped"
        assert "示意图" in str(qa.get("skip_reason"))
        assert result.get("revisions") == 0, "不可判定的对象不该消耗回炉预算"

    def test_skipped_quality_does_not_loop_back_to_restoration(self):
        """supervisor 不得把「跳过质检」当成「未通过」而送回出图（会死循环）。"""
        from app.agents.supervisor import _clamp, _rule_route

        state = {
            "qa": {"skipped": True, "passed": False, "decision": "skipped"},
            "plan": {"retrieval_queries": ["黄金面具"]},
            "completed": ["retrieval", "restoration", "quality"],
            "revisions": 0,
            "max_revisions": 2,
            "steps": 6,
        }
        completed = list(state["completed"])

        # 规则路由不能选中 restoration
        route, _ = _rule_route(state, completed, 6)
        assert route != "restoration"
        # 护栏也要拦掉 LLM 建议的重出图
        assert _clamp(state, "restoration", completed, 6) is None
        # 文案环节必须放行，否则任务永远收不了尾
        assert _clamp(state, "copywriting", completed, 6) == "copywriting"

    def test_task_still_produces_image_and_copy_when_skipped(self):
        """跳过质检不等于任务失败：图和文案仍然要交付，只是如实标注未质检。"""
        from app.graph.runner import run_once

        result = _run(run_once({"kind": "scene", "item": "青铜大立人", "style": "博物馆纪实摄影"}))

        assert result.get("image"), "即使未质检也必须交付图像产物"
        assert result.get("copy"), "即使未质检也必须交付科普文案"
        assert result.get("ok") is True, "占位图属于设计内降级，不是任务失败"


class TestGiveUpStopsRework:
    """决策为 `give_up` 之后不得再回炉。

    这是「打爆限流」那次事故的直接根因。实测链路：

        质检判 give_up → LLM 仍建议 restoration → `_clamp` 放行 →
        连出 4 轮，2 分钟内打完 6 次 DashScope → 触发 RPM 限流 →
        免费通道也 429 → 最终交付一张程序画的占位图。

    真正停住它的不是业务判断，而是「已达步数上限 18，强制收敛」——
    而那时通道已经废了。
    """

    def test_give_up_blocks_restoration(self):
        from app.agents.supervisor import _clamp, _rule_route

        state = {
            "qa": {"passed": False, "skipped": False, "decision": "give_up", "score": 0.4},
            "plan": {"retrieval_queries": ["青铜纵目面具"]},
            "completed": ["retrieval", "restoration", "quality"],
            "revisions": 1,
            # 回炉预算**还没用完** —— 唯一该拦住它的理由就是 give_up 本身。
            # 不这么设的话，测试会因为 revisions 超限而通过，掩盖真正的缺陷。
            "max_revisions": 4,
            "steps": 6,
        }
        completed = list(state["completed"])

        assert _clamp(state, "restoration", completed, 6) is None
        # 规则路由也必须与之一致（`_clamp` 拒绝后会退回它）
        route, _ = _rule_route(state, completed, 6)
        assert route != "restoration"
        # 但文案环节要放行，否则任务永远收不了尾
        assert _clamp(state, "copywriting", completed, 6) == "copywriting"

    def test_revise_still_allows_restoration(self):
        """别修过头：`revise` 的语义就是「回炉重做」，必须仍然放行。"""
        from app.agents.supervisor import _clamp

        state = {
            "qa": {"passed": False, "skipped": False, "decision": "revise"},
            "revisions": 0,
            "max_revisions": 4,
            "steps": 6,
        }
        assert _clamp(state, "restoration", ["retrieval"], 6) == "restoration"


class TestFallbackReasonIsReachableByTheUser:
    """降级原因必须写进**结果**里，不能只留在 metrics 与 trace。

    事故复盘：结果对象只带着 provider 自己的 `error` 文案出去，而本地占位图的
    文案当时写死成「未配置 DASHSCOPE_API_KEY」—— 那次真实原因是 DashScope 限流，
    Key 一直是配好的。用户只看到那句假话，于是去翻 `.env` 找 Key，方向完全错了。

    判断依据：`metrics` 和 `trace` 只在服务端，救不了正在界面前困惑的人。
    """

    @staticmethod
    def _service():
        from app.core.errors import ProviderError
        from app.imagegen.service import ImageService

        class _RateLimited:
            name = "dashscope"

            async def available(self) -> bool:
                return True

            async def generate(self, request):  # noqa: ANN001, ARG002
                raise ProviderError("Requests rate limit exceeded, please try again later")

            async def aclose(self) -> None:
                pass

        class _Placeholder:
            name = "local-placeholder"

            async def available(self) -> bool:
                return True

            async def generate(self, request):  # noqa: ANN001, ARG002
                return ImageResult(
                    image_url="/media/x.png",
                    provider=self.name,
                    latency_ms=1.0,
                    degraded=True,
                    generative=False,
                    error="出图通道均不可用，本次输出为程序绘制的示意图",
                )

            async def aclose(self) -> None:
                pass

        service = ImageService()
        service._providers = [_RateLimited(), _Placeholder()]  # noqa: SLF001
        service._built = True  # noqa: SLF001
        return service

    def test_upstream_failure_reason_reaches_the_result(self):
        result = _run(self._service().generate(ImageRequest(prompt="p", kind="scene")))

        assert result.provider == "local-placeholder"
        # 用户能看到的那份说明里必须包含真实原因
        assert "rate limit" in (result.error or ""), "降级原因没有透传到结果里"
        # 并且不能再说「未配置 Key」这种它并不知道的事
        assert "未配置 DASHSCOPE_API_KEY" not in (result.error or "")
        # 原始失败记录保留，便于排查
        assert result.raw["upstream_errors"] == [
            "dashscope: Requests rate limit exceeded, please try again later"
        ]
        assert result.raw["fallback_chain"] == ["dashscope"]

    def test_primary_channel_result_is_left_untouched(self):
        """主通道成功时不得被降级逻辑改动 —— 别把正常结果也写上「降级原因」。"""

        class _Ok:
            name = "dashscope"

            async def available(self) -> bool:
                return True

            async def generate(self, request):  # noqa: ANN001, ARG002
                return ImageResult(image_url="/media/ok.png", provider=self.name, latency_ms=1.0)

            async def aclose(self) -> None:
                pass

        from app.imagegen.service import ImageService

        service = ImageService()
        service._providers = [_Ok()]  # noqa: SLF001
        service._built = True  # noqa: SLF001

        result = _run(service.generate(ImageRequest(prompt="p", kind="scene")))

        assert result.error is None
        assert result.degraded is False
        assert "upstream_errors" not in result.raw

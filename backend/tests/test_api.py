"""HTTP 接口端到端测试（含 SSE 帧协议）。

全部在「未配置任何 API Key」的环境下运行：出图走本地占位 provider，
LLM 层走规则/抽取式降级。这恰好验证了最重要的一条产品承诺 ——
**未配置 Key 时功能链路完整可用，只是质量下降且如实标注。**
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.main import app

SIMPLE_REQUEST = {
    "kind": "scene",
    "identity": "大祭司",
    "scene": "博物馆展厅",
    "item": "青铜纵目面具",
    "style": "博物馆纪实摄影",
    "seed": 20260916,
}


@pytest.fixture(scope="module")
def client():
    # 用 with 进入才能在退出时正确触发 lifespan 的清理逻辑
    with TestClient(app) as instance:
        yield instance


class TestMetaEndpoints:
    def test_root(self, client: TestClient):
        body = client.get("/").json()
        assert body["service"] == "sanxingdui-multiagent-restoration"

    def test_health_reports_engine_and_degradation(self, client: TestClient):
        response = client.get("/api/health")
        assert response.status_code == 200
        body = response.json()

        assert body["status"] == "ok"
        assert body["engine"]["engine"] in {"langgraph", "builtin"}
        assert body["retrieval"]["corpus_size"] >= 40
        assert body["style_profiles"] >= 10
        # 代理诊断必须暴露，否则「服务在跑但请求 502」会变成不可调试的问题
        assert "network" in body

    def test_graph_topology_exposes_self_correction_loop(self, client: TestClient):
        body = client.get("/api/graph").json()
        assert body["topology"]["self_correction_loop"]
        assert body["max_revisions"] >= 0
        assert 0 < body["style_threshold"] <= 1

    def test_models_matrix_explains_every_role(self, client: TestClient):
        body = client.get("/api/models").json()
        roles = {item["role"] for item in body["roles"]}
        assert {"supervisor", "planner", "retrieval", "copywriter", "judge", "vlm"} <= roles
        for item in body["roles"]:
            assert item["primary"], f"{item['role']} 必须声明主模型"
            assert len(item["rationale"]) > 30, "每个角色都要有可解释的选型理由"

    def test_styles_endpoint_lists_profiles(self, client: TestClient):
        body = client.get("/api/styles").json()
        keys = {profile["key"] for profile in body["profiles"]}
        assert {"museum_doc", "restoration", "cyberpunk", "default"} <= keys

    def test_metrics_endpoint_shape(self, client: TestClient):
        body = client.get("/api/metrics").json()
        assert "counters" in body and "histograms" in body and "derived" in body

    def test_corpus_reload(self, client: TestClient):
        body = client.post("/api/corpus/reload").json()
        assert body["ok"] is True
        assert body["corpus_size"] >= 40

    def test_trace_endpoint_rejects_path_traversal(self, client: TestClient):
        assert client.get("/api/traces/..%2F..%2Fetc%2Fpasswd").status_code in {400, 404}

    def test_trace_endpoint_404_for_unknown_task(self, client: TestClient):
        assert client.get("/api/traces/sxd-doesnotexist").status_code == 404


class TestSyncRestore:
    def test_full_pipeline_returns_artifact_and_verdict(self, client: TestClient):
        response = client.post("/api/restore", json=SIMPLE_REQUEST, timeout=120)
        assert response.status_code == 200
        body = response.json()

        assert body["ok"] is True
        assert body["task_id"].startswith("sxd-")
        assert body["image_url"], "即使全部降级也必须产出图像（本地占位图）"
        assert body["engine"]

        # 质检结果必须是结构化的，不能只是「通过/不通过」一个布尔
        qa = body["qa"]
        if qa.get("skipped"):
            # 测试环境没有真实出图通道，产出的是本地占位示意图。
            # 对示意图做风格质检没有可判定的对象，因此系统如实标注「未质检」，
            # 而不是伪造一个低分再假装回炉失败（这正是之前 give_up 的假象来源）。
            assert qa["decision"] == "skipped"
            assert qa["score"] is None
            assert "示意图" in qa["skip_reason"]
            assert body["revisions"] == 0, "不可判定的图不应消耗回炉预算"
        else:
            assert 0.0 <= qa["score"] <= 1.0
            assert qa["threshold"] > 0
            assert qa["decision"] in {"accept", "revise", "give_up"}
            assert qa["objective_score"] >= 0.0
        assert isinstance(qa["feedback"], list)

        # 检索证据必须可追溯
        assert body["evidence"], "检索层必须给出可追溯的史料卡片"
        for chunk in body["evidence"]:
            assert chunk["doc_id"] and chunk["title"] and chunk["source"]

        # 规划产物必须完整
        plan = body["plan"]
        assert plan["goal"] and plan["profile_label"] and plan["image_spec"]["prompt"]

        # 文案必须由 Worker 产出，并带上来源与免责声明
        copy_payload = body["copy"]
        assert copy_payload["text"]
        assert copy_payload["disclaimer"]

    def test_result_is_idempotent_for_same_seed(self, client: TestClient):
        first = client.post("/api/restore", json=SIMPLE_REQUEST, timeout=120).json()
        second = client.post("/api/restore", json=SIMPLE_REQUEST, timeout=120).json()
        # 同一 seed + 同一降级出图通道 → 图像指纹一致（证明 seed 真的透传下去了）
        assert first["image_url"].split("-")[-1] == second["image_url"].split("-")[-1]

    def test_each_kind_runs(self, client: TestClient):
        payloads = [
            {"kind": "figure", "gender": "男性", "rank": "大祭司", "detail": "全身像"},
            {"kind": "artifact", "artifact": "残缺玉璋", "method": "写实复原"},
            {"kind": "style", "style_preset": "玉石质感", "strength": 60},
        ]
        for payload in payloads:
            body = client.post("/api/restore", json=payload, timeout=120).json()
            assert body["ok"] is True, f"{payload['kind']} 执行失败: {body.get('errors')}"
            assert body["image_url"]
            assert body["goal"]

    def test_invalid_reference_image_rejected(self, client: TestClient):
        bad = {**SIMPLE_REQUEST, "reference_images": ["ftp://example.com/a.png"]}
        assert client.post("/api/restore", json=bad).status_code == 422

    def test_invalid_kind_rejected(self, client: TestClient):
        assert client.post("/api/restore", json={"kind": "nonsense"}).status_code == 422


class TestSseStream:
    def _collect(self, client: TestClient, payload: dict) -> tuple[dict[str, int], list[dict], dict | None]:
        counts: dict[str, int] = {}
        nodes: list[dict] = []
        result: dict | None = None

        with client.stream(
            "POST",
            "/api/restore/stream",
            json=payload,
            headers={"Accept": "text/event-stream"},
            timeout=120,
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")

            event_name = "message"
            data_lines: list[str] = []
            for line in response.iter_lines():
                if line == "":
                    if data_lines:
                        counts[event_name] = counts.get(event_name, 0) + 1
                        data = json.loads("\n".join(data_lines))
                        if event_name == "node":
                            nodes.append(data)
                        elif event_name == "done":
                            result = data["result"]
                    event_name = "message"
                    data_lines = []
                    continue
                if line.startswith(":"):
                    counts["ping"] = counts.get("ping", 0) + 1
                elif line.startswith("event:"):
                    event_name = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].strip())
        return counts, nodes, result

    def test_frame_protocol(self, client: TestClient):
        counts, nodes, result = self._collect(client, SIMPLE_REQUEST)

        for required in ("start", "node", "trace", "done"):
            assert counts.get(required, 0) >= 1, f"缺少 {required} 帧（实际: {counts}）"

        assert result is not None
        assert result["image_url"]

        # 节点事件必须能支撑前端画进度条与时间线
        assert nodes[0]["node"] == "planner"
        assert nodes[-1]["node"] == "finalize"
        assert nodes[-1]["progress"] == 100
        progress = [item["progress"] for item in nodes]
        assert progress == sorted(progress), "进度必须单调递增，否则前端进度条会回退"
        assert all(item.get("label") for item in nodes)

    def test_self_correction_loop_is_visible_in_node_sequence(self, client: TestClient):
        _, nodes, result = self._collect(client, SIMPLE_REQUEST)
        sequence = [item["node"] for item in nodes]

        assert sequence.count("quality") >= 1
        assert sequence.count("restoration") == sequence.count("quality"), (
            "每次出图都必须紧跟一次质检，否则回炉就是无效的"
        )

        # 若质检给出 revise，则必须能看到 quality → restoration 的回头路
        first_qa = next((item for item in nodes if item["node"] == "quality"), None)
        if first_qa and first_qa["patch"].get("decision") == "revise":
            for index, name in enumerate(sequence[:-1]):
                if name == "quality":
                    assert sequence[index + 1] == "restoration"
                    break

    def test_done_result_matches_sync_endpoint_shape(self, client: TestClient):
        _, _, result = self._collect(client, SIMPLE_REQUEST)
        assert result is not None
        for key in ("plan", "retrieval", "evidence", "image", "qa", "copy", "revision_history"):
            assert key in result, f"SSE 结果缺少字段 {key}"
        assert result["usage"]["llm_calls"] >= 0
        assert isinstance(result["degraded_components"], list)

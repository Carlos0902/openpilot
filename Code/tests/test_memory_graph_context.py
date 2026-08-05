from __future__ import annotations

import json
import math

from core.openpilot_log import OpenPilotLogger
from memory.agents.memory_vault_agent import MemoryVaultAgent
from memory.context_builder import MemoryContextBuilder
from memory.memory_models import MemoryRecord, MemoryType
from memory.memory_store import MemoryStore
from memory.memory_vault import MemoryVault
from memory.tool.memory_context_tool import memory_context_executor
from metadata import ToolInputMetadata


class FakeEmbeddingService:
    def embed_text(self, text: str) -> list[float]:
        lower = text.lower()
        return [
            1.0 if "pygame" in lower else 0.0,
            1.0 if "memory" in lower else 0.0,
            1.0 if "arcade" in lower else 0.0,
        ]

    def compute_similarity(self, left: list[float], right: list[float], method: str = "cosine") -> float:
        dot = sum(a * b for a, b in zip(left, right))
        left_norm = math.sqrt(sum(a * a for a in left))
        right_norm = math.sqrt(sum(b * b for b in right))
        if not left_norm or not right_norm:
            return 0.0
        return dot / (left_norm * right_norm)

    def find_similar(self, embedding, candidate_embeddings, top_k=5, threshold=0.7):
        scored = [
            (index, self.compute_similarity(embedding, candidate))
            for index, candidate in enumerate(candidate_embeddings)
        ]
        return [
            (index, score)
            for index, score in sorted(scored, key=lambda item: item[1], reverse=True)[:top_k]
            if score >= threshold
        ]


def test_memory_vault_agent_graph_operations_and_logs(tmp_path) -> None:
    logger = OpenPilotLogger(tmp_path / "memory_graph.jsonl")
    vault = MemoryVault(
        FakeEmbeddingService(),
        storage_dir=tmp_path / "vault",
        auto_relate=False,
    )
    agent = MemoryVaultAgent(
        memory_vault=vault,
        logger=logger,
        session_id_getter=lambda: "session",
    )

    first = agent.add_node(
        "User prefers pygame arcade polish.",
        MemoryType.PROJECT,
        tags=["pygame", "arcade"],
        confidence=0.9,
    )
    second = agent.add_node(
        "Memory graph should preserve user preferences.",
        MemoryType.USER,
        tags=["memory"],
        confidence=0.8,
    )
    assert agent.add_edge(first, second, relevance=0.73) is True

    edge = vault.graph.get_edge(first, second, edge_type="relevance")
    assert edge is not None
    assert edge.weight == 0.73
    assert edge.attributes["relevance"] == 0.73

    reminders = agent.remind("pygame arcade", limit=2)
    confidence, answer = agent.confidence_evaluate("pygame arcade")

    assert reminders[0]["id"] == first
    assert reminders[0]["tags"] == ["pygame", "arcade"]
    assert reminders[0]["confidence"] == 0.9
    assert confidence > 0.5
    assert "pygame arcade" in answer

    payloads = [
        json.loads(line)["payload"]
        for line in (tmp_path / "memory_graph.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert any(payload.get("source_name") == "memory.agents.memory_vault_agent" for payload in payloads)
    assert {payload.get("source_type") for payload in payloads} == {"agent"}


def test_memory_context_builder_prefers_graph_recall_over_store(tmp_path) -> None:
    vault = MemoryVault(FakeEmbeddingService(), storage_dir=tmp_path / "vault", auto_relate=False)
    agent = MemoryVaultAgent(memory_vault=vault)
    graph_id = agent.add_node(
        "Graph memory says pygame arcade polish matters.",
        MemoryType.PROJECT,
        tags=["graph"],
        confidence=0.95,
    )
    store = MemoryStore(tmp_path / "store")
    store.save(
        MemoryRecord(
            id="store-memory",
            memory_type=MemoryType.PROJECT,
            content="Store memory fallback.",
            tags=["store"],
            confidence=0.7,
        )
    )
    builder = MemoryContextBuilder(memory_store=store, memory_vault_agent=agent)

    context = builder.build("pygame arcade", limit=3)

    assert context["related_memories"][0]["id"] == graph_id
    assert context["related_memories"][0]["score"] > 0
    assert "Graph memory says pygame arcade polish matters." in context["prompt_text"]


def test_memory_context_builder_falls_back_to_store_when_vault_fails(tmp_path) -> None:
    class BrokenVaultAgent:
        def remind(self, query, limit=10):
            raise RuntimeError("vault unavailable")

    store = MemoryStore(tmp_path / "store")
    store.save(
        MemoryRecord(
            id="fallback-memory",
            memory_type=MemoryType.PROJECT,
            content="Fallback memory from store.",
            tags=["fallback"],
            confidence=0.8,
        )
    )
    builder = MemoryContextBuilder(memory_store=store, memory_vault_agent=BrokenVaultAgent())

    context = builder.build("fallback", limit=3)

    assert context["related_memories"][0]["id"] == "fallback-memory"
    assert "Fallback memory from store." in context["prompt_text"]


def test_memory_context_tool_accepts_injected_memory_vault_agent(tmp_path) -> None:
    vault = MemoryVault(FakeEmbeddingService(), storage_dir=tmp_path / "vault", auto_relate=False)
    agent = MemoryVaultAgent(memory_vault=vault)
    memory_id = agent.add_node(
        "Tool graph memory for pygame context.",
        MemoryType.PROJECT,
        tags=["tool", "pygame"],
        confidence=0.9,
        attributes={"project_path": str(tmp_path)},
    )
    result = memory_context_executor(
        ToolInputMetadata.from_mapping("memory_context", {
            "query": "pygame",
            "project_path": str(tmp_path),
            "_memory_vault_agent": agent,
        })
    )

    assert result["related_memories"][0]["id"] == memory_id
    assert "Tool graph memory" in result["prompt_text"]

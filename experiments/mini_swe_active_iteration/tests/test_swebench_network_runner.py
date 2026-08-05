from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from mini_swe_active_iteration.swebench_agent_environment import (
    SWEbenchDockerEnvironment,
)
from mini_swe_active_iteration.swebench_network_runner import (
    run_network_isolated_evaluation,
)


class _FakeContainer:
    def __init__(self) -> None:
        self.started = False
        self.removed = False
        self.exec_calls: list[tuple[object, dict[str, object]]] = []

    def start(self) -> None:
        self.started = True

    def exec_run(self, command: object, **kwargs: object) -> tuple[int, bytes]:
        self.exec_calls.append((command, kwargs))
        if "--no-ext-diff" in str(command):
            return 0, b"diff --git a/example.py b/example.py\n"
        return 0, b"command output"

    def remove(self, *, force: bool) -> None:
        assert force is True
        self.removed = True


class _FakeContainers:
    def __init__(self) -> None:
        self.created: list[dict[str, object]] = []
        self.container = _FakeContainer()

    def create(self, *args: object, **kwargs: object) -> _FakeContainer:
        self.created.append(dict(kwargs))
        return self.container

    def run(self, *args: object, **kwargs: object) -> _FakeContainer:
        self.created.append(dict(kwargs))
        return self.container


def _pinned_client(containers: _FakeContainers) -> SimpleNamespace:
    digest = "sha256:" + "a" * 64
    image = SimpleNamespace(
        id="sha256:" + "b" * 64,
        attrs={
            "RepoDigests": (
                "swebench/sweb.eval.x86_64.example_1776_issue-1@" + digest,
            )
        },
    )
    return SimpleNamespace(
        containers=containers,
        images=SimpleNamespace(get=lambda image_ref: image),
    )


def test_agent_environment_is_network_isolated_and_exports_only_a_patch() -> None:
    containers = _FakeContainers()
    environment = SWEbenchDockerEnvironment(
        instance_id="example__issue-1",
        instance_image_ref="swebench/sweb.eval.x86_64.example_1776_issue-1:acq-v4-aaaaaaaaaaaa",
        expected_image_manifest_digest="sha256:" + "a" * 64,
        docker_client=_pinned_client(containers),
    )

    assert containers.created == [
        {
            "image": "swebench/sweb.eval.x86_64.example_1776_issue-1:acq-v4-aaaaaaaaaaaa",
            "command": ["/bin/sh", "-c", "while true; do sleep 3600; done"],
            "working_dir": "/testbed",
            "network_mode": "none",
            "environment": {"HOME": "/testbed", "LANG": "C.UTF-8", "LC_ALL": "C"},
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
        }
    ]
    assert containers.container.started is True
    assert environment.execute({"command": "pwd"})["output"] == "command output"
    assert environment.collect_patch() == "diff --git a/example.py b/example.py\n"
    with pytest.raises(ValueError, match="inside /testbed"):
        environment.execute({"command": "pwd"}, cwd="/tmp")

    environment.close()
    assert containers.container.removed is True
    assert environment.serialize()["info"]["config"]["environment"]["network_allowed"] is False


def test_model_patch_evaluation_stays_private_and_is_network_isolated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    containers = _FakeContainers()
    real_client = SimpleNamespace(containers=containers)
    docker_module = ModuleType("docker")
    docker_module.from_env = lambda: real_client  # type: ignore[attr-defined]
    run_evaluation = SimpleNamespace(
        docker=docker_module,
        GIT_APPLY_CMDS=["git", "apply"],
    )
    captured: dict[str, object] = {}

    def fake_main(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        prediction = json.loads(Path(str(kwargs["predictions_path"])).read_text())
        assert prediction[0]["model_patch"] == "diff --git a/a b/a\n"
        run_evaluation.docker.from_env().containers.run(image="evaluator-image")
        return {"resolved": []}

    run_evaluation.main = fake_main
    swebench_module = ModuleType("swebench")
    harness_module = ModuleType("swebench.harness")
    harness_module.run_evaluation = run_evaluation  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "docker", docker_module)
    monkeypatch.setitem(sys.modules, "swebench", swebench_module)
    monkeypatch.setitem(sys.modules, "swebench.harness", harness_module)
    dataset = tmp_path / "dataset.json"
    dataset.write_text("[]")
    result = tmp_path / "result.json"

    run_network_isolated_evaluation(
        dataset_path=dataset,
        instance_id="example__issue-1",
        mode="model",
        model_patch="diff --git a/a b/a\n",
        repetition=1,
        run_id="model-arm",
        timeout_seconds=60,
        private_work_dir=tmp_path / "private",
        result_path=result,
    )

    payload = json.loads(result.read_text())
    assert payload["mode"] == "model"
    assert payload["model_patch_sha256"] == hashlib.sha256(
        b"diff --git a/a b/a\n"
    ).hexdigest()
    assert payload["network_disabled"] is True
    assert captured["predictions_path"] != "gold"
    assert "diff --git" not in result.read_text()

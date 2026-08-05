"""Run one official SWE-bench evaluation with evaluator networking disabled."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch


class NetworkIsolatedContainers:
    def __init__(self, containers: object) -> None:
        self._containers = containers
        self.created_network_modes: list[str] = []
        self.created_images: list[str] = []

    def create(self, *args: object, **kwargs: object) -> object:
        self._record_isolation(kwargs)
        return self._containers.create(*args, **kwargs)

    def run(self, *args: object, **kwargs: object) -> object:
        self._record_isolation(kwargs)
        return self._containers.run(*args, **kwargs)

    def _record_isolation(self, kwargs: dict[str, object]) -> None:
        kwargs["network_mode"] = "none"
        self.created_network_modes.append("none")
        self.created_images.append(str(kwargs.get("image", "")))

    def __getattr__(self, name: str) -> object:
        return getattr(self._containers, name)


class NetworkIsolatedDockerClient:
    def __init__(self, client: object) -> None:
        self._client = client
        self.containers = NetworkIsolatedContainers(client.containers)

    def __getattr__(self, name: str) -> object:
        return getattr(self._client, name)


def _serialize_result_payload(payload: dict[str, object]) -> str:
    return json.dumps(
        payload,
        indent=2,
        sort_keys=True,
        default=str,
    ) + "\n"


def _verify_local_pinned_image(
    *,
    images: object,
    image_ref: str,
    expected_manifest_digest: str,
) -> str:
    if (
        not expected_manifest_digest.startswith("sha256:")
        or len(expected_manifest_digest) != 71
        or any(
            character not in "0123456789abcdef"
            for character in expected_manifest_digest.removeprefix("sha256:")
        )
    ):
        raise ValueError("expected image manifest digest must be full SHA-256")
    if not image_ref.startswith("swebench/") or ":" not in image_ref:
        raise ValueError("pinned image must use the official swebench namespace")
    image = images.get(image_ref)
    repository = image_ref.rsplit(":", 1)[0]
    expected_repo_digest = f"{repository}@{expected_manifest_digest}"
    repo_digests = tuple(
        str(value)
        for value in getattr(image, "attrs", {}).get("RepoDigests", ())
    )
    if expected_repo_digest not in repo_digests:
        raise ValueError("local image does not match frozen manifest digest")
    image_id = str(getattr(image, "id", ""))
    if (
        not image_id.startswith("sha256:")
        or len(image_id) != 71
        or any(
            character not in "0123456789abcdef"
            for character in image_id.removeprefix("sha256:")
        )
    ):
        raise ValueError("local pinned image ID must be full SHA-256")
    return image_id


def _normalize_report(
    report: object,
    *,
    private_work_dir: Path,
) -> tuple[object, str | None]:
    if not isinstance(report, (str, Path)):
        return report, None
    report_path = Path(report)
    if not report_path.is_absolute():
        report_path = private_work_dir / report_path
    report_path = report_path.resolve()
    private_root = private_work_dir.resolve()
    if not report_path.is_relative_to(private_root):
        raise ValueError("harness report escaped the private work directory")
    if not report_path.is_file() or report_path.is_symlink():
        return str(report), None
    payload = report_path.read_bytes()
    return (
        json.loads(payload),
        hashlib.sha256(payload).hexdigest(),
    )


def run_network_isolated_evaluation(
    *,
    dataset_path: Path,
    instance_id: str,
    mode: str,
    model_patch: str | None = None,
    repetition: int,
    run_id: str,
    timeout_seconds: int,
    private_work_dir: Path,
    result_path: Path,
    instance_image_ref: str | None = None,
    expected_image_manifest_digest: str | None = None,
) -> Path:
    if mode not in {"base", "gold", "model"}:
        raise ValueError("mode must be base, gold or model")
    if (mode == "model") != (model_patch is not None):
        raise ValueError("model mode requires exactly one model_patch")
    if repetition not in {1, 2}:
        raise ValueError("repetition must be 1 or 2")
    if result_path.exists() or result_path.is_symlink():
        raise FileExistsError(
            f"refusing to overwrite SWE-bench result: {result_path}"
        )
    private_work_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(private_work_dir, 0o700)
    dataset_path = dataset_path.resolve()
    if dataset_path.is_symlink() or not dataset_path.is_file():
        raise ValueError("dataset must be a regular non-symlink file")

    os.chdir(private_work_dir)
    import docker
    from swebench.harness import run_evaluation

    pinned_image_id: str | None = None
    namespace: str | None = None
    instance_image_tag = "latest"
    if (instance_image_ref is None) != (
        expected_image_manifest_digest is None
    ):
        raise ValueError(
            "pinned image ref and manifest digest must appear together"
        )
    if instance_image_ref is not None:
        instance_image_tag = instance_image_ref.rsplit(":", 1)[-1]
        expected_image_ref = (
            "swebench/sweb.eval.x86_64."
            f"{instance_id.lower().replace('__', '_1776_')}:"
            f"{instance_image_tag}"
        )
        if instance_image_ref != expected_image_ref:
            raise ValueError(
                "pinned image ref does not match the candidate identity"
            )
        namespace = "swebench"

    predictions_path = private_work_dir / f"{run_id}.predictions.json"
    if mode in {"base", "model"}:
        prediction_patch = "\n" if mode == "base" else model_patch
        assert prediction_patch is not None
        predictions_path.write_text(
            json.dumps(
                [
                    {
                        "instance_id": instance_id,
                        "model_name_or_path": (
                            "acquisition-base"
                            if mode == "base"
                            else "mini-swe-core-benefit-screen"
                        ),
                        "model_patch": prediction_patch,
                    }
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        prediction_source = str(predictions_path)
    else:
        prediction_source = "gold"

    real_client = docker.from_env()
    if instance_image_ref is not None:
        pinned_image_id = _verify_local_pinned_image(
            images=real_client.images,
            image_ref=instance_image_ref,
            expected_manifest_digest=expected_image_manifest_digest,
        )
    isolated_client = NetworkIsolatedDockerClient(real_client)
    apply_commands = (
        ["true"] if mode == "base" else run_evaluation.GIT_APPLY_CMDS
    )
    with (
        patch.object(
            run_evaluation.docker,
            "from_env",
            return_value=isolated_client,
        ),
        patch.object(
            run_evaluation,
            "GIT_APPLY_CMDS",
            apply_commands,
        ),
    ):
        report = run_evaluation.main(
            dataset_name=str(dataset_path),
            split="test",
            instance_ids=[instance_id],
            predictions_path=prediction_source,
            max_workers=1,
            force_rebuild=False,
            cache_level="instance",
            clean=False,
            open_file_limit=4096,
            run_id=run_id,
            timeout=timeout_seconds,
            namespace=namespace,
            instance_image_tag=instance_image_tag,
            rewrite_reports=False,
            modal=False,
            report_dir=str(private_work_dir / "reports"),
        )

    network_modes = tuple(isolated_client.containers.created_network_modes)
    created_images = tuple(isolated_client.containers.created_images)
    if not network_modes or set(network_modes) != {"none"}:
        raise ValueError("evaluator did not create only network-isolated containers")
    if instance_image_ref is not None and any(
        image != instance_image_ref for image in created_images
    ):
        raise ValueError("evaluator container used an unpinned image")
    normalized_report, report_sha256 = _normalize_report(
        report,
        private_work_dir=private_work_dir,
    )
    payload = {
        "schema_version": "1.0",
        "runner_version": (
            "network-isolated-swebench-v4"
            if instance_image_ref is not None
            else "network-isolated-swebench-v3"
        ),
        "run_id": run_id,
        "instance_id": instance_id,
        "mode": mode,
        "repetition": repetition,
        "network_modes": network_modes,
        "network_disabled": bool(network_modes)
        and set(network_modes) == {"none"},
        "created_images": created_images,
        "report": normalized_report,
        "report_sha256": report_sha256,
    }
    if mode == "model":
        assert model_patch is not None
        payload["model_patch_sha256"] = hashlib.sha256(
            model_patch.encode()
        ).hexdigest()
    if instance_image_ref is not None:
        payload.update(
            {
                "instance_image_ref": instance_image_ref,
                "instance_image_manifest_digest": (
                    expected_image_manifest_digest
                ),
                "instance_image_id": pinned_image_id,
            }
        )
    serialized = _serialize_result_payload(payload)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    with result_path.open("x", encoding="utf-8") as stream:
        stream.write(serialized)
    os.chmod(result_path, 0o600)
    return result_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--mode", choices=("base", "gold", "model"), required=True)
    parser.add_argument("--model-patch", type=Path)
    parser.add_argument("--repetition", type=int, choices=(1, 2), required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--private-work-dir", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--instance-image-ref")
    parser.add_argument("--expected-image-manifest-digest")
    args = parser.parse_args()
    output = run_network_isolated_evaluation(
        dataset_path=args.dataset,
        instance_id=args.instance_id,
        mode=args.mode,
        model_patch=(
            args.model_patch.read_text(encoding="utf-8")
            if args.model_patch is not None
            else None
        ),
        repetition=args.repetition,
        run_id=args.run_id,
        timeout_seconds=args.timeout_seconds,
        private_work_dir=args.private_work_dir,
        result_path=args.result,
        instance_image_ref=args.instance_image_ref,
        expected_image_manifest_digest=(
            args.expected_image_manifest_digest
        ),
    )
    print(output.resolve())


if __name__ == "__main__":
    main()

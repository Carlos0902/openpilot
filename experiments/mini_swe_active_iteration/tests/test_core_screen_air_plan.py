from __future__ import annotations

import json
from pathlib import Path

from mini_swe_active_iteration.exploratory_plan import ExploratoryPairedRunPlan


def test_core_screen_lifecycle_accepts_exactly_twelve_pairs() -> None:
    package_root = Path(__file__).parents[1]
    payload = json.loads(
        (package_root / "EXPLORATORY_PAIRED_RUN_PLAN_V7.json").read_text()
    )
    payload.update(
        {
            "lifecycle": "core_benefit_screen",
            "protocol_id": "mini-swe-active-core-benefit-screen-paired-v1",
            "protocol_revision": 1,
            "predecessor_plan_sha256": None,
            "invalid_prior_result_sha256": None,
            "excluded_exposed_instance_ids": [],
            "runner_artifact_sha256": {"runner.py": "0" * 64},
            "pairs": payload["pairs"][:12],
        }
    )

    plan = ExploratoryPairedRunPlan.model_validate(payload)

    assert plan.lifecycle == "core_benefit_screen"
    assert len(plan.pairs) == 12

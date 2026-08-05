"""Isolated contracts for the mini-SWE active-iteration experiment.

Exports are loaded on demand so receipt-only utilities do not initialize the
mini-SWE runtime or its global configuration.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any


_EXPORTS: dict[str, tuple[str, str]] = {
    "ActionEffect": (".contracts", "ActionEffect"),
    "ActiveSignal": (".contracts", "ActiveSignal"),
    "ActiveState": (".contracts", "ActiveState"),
    "BudgetLimits": (".contracts", "BudgetLimits"),
    "Controllability": (".contracts", "Controllability"),
    "Decision": (".contracts", "Decision"),
    "DecisionKind": (".contracts", "DecisionKind"),
    "ExperimentArm": (".contracts", "ExperimentArm"),
    "ExperimentResult": (".contracts", "ExperimentResult"),
    "Freshness": (".contracts", "Freshness"),
    "MeasurementResult": (".contracts", "MeasurementResult"),
    "SignalStatus": (".contracts", "SignalStatus"),
    "Usage": (".contracts", "Usage"),
    "ActiveIterationPolicy": (".policy", "ActiveIterationPolicy"),
    "ExperimentRunner": (".runner", "ExperimentRunner"),
    "ActiveIterationAgent": (".mini_agent", "ActiveIterationAgent"),
    "BudgetedDefaultAgent": (".mini_agent", "BudgetedDefaultAgent"),
    "ActiveIterationController": (".controller", "ActiveIterationController"),
    "ControllerFormatError": (".controller", "ControllerFormatError"),
    "ControllerModelResult": (".controller", "ControllerModelResult"),
    "DevelopmentTaskSuite": (".fixture", "DevelopmentTaskSuite"),
    "load_development_suite": (".fixture", "load_development_suite"),
    "SeatbeltEnvironment": (".sandbox", "SeatbeltEnvironment"),
    "SWEbenchDockerEnvironment": (
        ".swebench_agent_environment",
        "SWEbenchDockerEnvironment",
    ),
    "MiniAgentAdapter": (".adapter", "MiniAgentAdapter"),
    "PairedDevelopmentResult": (".development_runner", "PairedDevelopmentResult"),
    "PairedDevelopmentRunner": (".development_runner", "PairedDevelopmentRunner"),
    "PairedSWEbenchRunner": (".swebench_task_runner", "PairedSWEbenchRunner"),
    "SWEbenchScreenTask": (".swebench_task_runner", "SWEbenchScreenTask"),
    "LiteLLMControllerModel": (".provider", "LiteLLMControllerModel"),
    "SharedProviderFactory": (".provider", "SharedProviderFactory"),
    "SharedProviderSpec": (".provider", "SharedProviderSpec"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    try:
        module_name, attribute_name = _EXPORTS[name]
    except KeyError as error:
        raise AttributeError(name) from error
    value = getattr(import_module(module_name, __name__), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted((*globals(), *_EXPORTS))

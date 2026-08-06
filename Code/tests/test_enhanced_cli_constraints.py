from __future__ import annotations

import pytest

from ui.enhanced_cli import _is_constraint_command


@pytest.mark.parametrize("command", ["/constraints", "/confirm proposal-1", "/reject proposal-1", "/revoke write_scope"])
def test_constraint_commands_share_interactive_ingress_dispatch(command: str) -> None:
    assert _is_constraint_command(command)


def test_non_constraint_slash_command_does_not_enter_constraint_ingress() -> None:
    assert not _is_constraint_command("/help")

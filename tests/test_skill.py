"""Keeps SKILL.md and the CLI from drifting: every `brain …` invocation in the skill
must parse against the real argparse parser."""

import re
import shlex
from pathlib import Path

import pytest

from brain.cli import build_parser

SKILL = Path(__file__).parent.parent / "skills" / "second-brain" / "SKILL.md"
SSH_PREFIX = "ssh $BRAIN_SSH_HOST "


def brain_invocations(text: str) -> list[list[str]]:
    commands = []
    candidates = []
    in_fence = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:  # fenced blocks only: prose mentions like `brain search` are not full commands
            candidates.append(line)
    for candidate in candidates:
        for part in re.split(r"&&|;", candidate):
            part = part.strip().removeprefix(SSH_PREFIX)
            if part.startswith("brain "):
                commands.append(shlex.split(part)[1:])
    return commands


def test_every_skill_command_is_a_valid_cli_invocation():
    invocations = brain_invocations(SKILL.read_text())
    assert len(invocations) >= 15, "skill should be full of concrete examples"
    parser = build_parser()
    for argv in invocations:
        try:
            parser.parse_args(argv)
        except SystemExit:
            pytest.fail(f"SKILL.md contains an invalid CLI invocation: brain {' '.join(argv)}")


def test_skill_covers_all_five_workflows():
    text = SKILL.read_text().lower()
    for workflow in ("capture", "q&a", "research", "content", "weekly review"):
        assert f"workflow: {workflow}" in text, f"missing workflow section: {workflow}"
    assert "brain_ssh_host" in text  # remote invocation documented

"""Task selection is usable without the optional AgentDojo dependency."""

import pytest

from agentdojo_mcp.bridge import BridgeError, pick_tasks


@pytest.mark.parametrize(
    ("patterns", "expected"),
    [
        (None, [1, 2, 3]),
        ([], [1, 2, 3]),
        (["user_task_1*"], [1, 2]),
        (["user_task_20", "user_task_10"], [3, 1]),
        (["user_task_1*", "user_task_10"], [1, 2]),
        (["user_task_?0"], [1, 3]),
    ],
)
def test_user_task_patterns(patterns, expected):
    available = {"user_task_10": 1, "user_task_11": 2, "user_task_20": 3}
    assert pick_tasks(patterns, available, "user tasks", "fixture") == expected


def test_injection_task_range():
    available = {f"injection_task_{i}": i for i in range(6)}
    assert pick_tasks(["injection_task_[0-3]"], available, "injection tasks", "fixture") == [
        0,
        1,
        2,
        3,
    ]


@pytest.mark.parametrize("pattern", ["missing*", "USER_TASK_*", ""])
def test_every_pattern_must_match(pattern):
    with pytest.raises(BridgeError, match="not in suite fixture") as exc:
        pick_tasks(["user_task_*", pattern], {"user_task_1": 1}, "user tasks", "fixture")
    assert repr(pattern) in str(exc.value)

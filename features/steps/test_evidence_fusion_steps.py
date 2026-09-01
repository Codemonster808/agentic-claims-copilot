import sys
from pathlib import Path

from pytest_bdd import given, scenarios, then, when

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from models.agent_loop import _reciprocal_rank_fusion  # noqa: E402

scenarios("../evidence-fusion.feature")


@given(
    "two retrieval lists where x is rank 1 in both but distances use different scales",
    target_fixture="lists",
)
def lists_consistent_rank():
    return [
        [{"id": "x", "distance": 0.1}, {"id": "y", "distance": 0.2}],
        [{"id": "x", "distance": 0.9}, {"id": "z", "distance": 0.95}],
    ]


@given(
    "one list with a far correct clause and another with close wrong clauses",
    target_fixture="lists",
)
def lists_scale_trap():
    return [
        [{"id": "correct", "distance": 0.85}],
        [{"id": "wrong1", "distance": 0.10}, {"id": "wrong2", "distance": 0.11}],
    ]


@when("reciprocal rank fusion is applied", target_fixture="fused")
def fuse(lists):
    return _reciprocal_rank_fusion(lists)


@then("the top fused item is x")
def top_is_x(fused):
    assert fused[0]["id"] == "x"


@then("the winner is not chosen by minimum raw distance")
def not_min_distance(fused):
    assert fused[0]["distance"] != min(0.10, 0.85)
    assert fused[0]["id"] in ("correct", "wrong1")

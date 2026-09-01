"""Pure-logic tests for reciprocal rank fusion (RRF) — no infra, no MiniStack.

Split out of the old flat tests/test_agent_loop.py: these two tests only
exercise src/models/agent_loop.py::_reciprocal_rank_fusion, a pure function
over in-memory lists, so they belong in unit/ rather than integration/.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from models.agent_loop import _reciprocal_rank_fusion  # noqa: E402


def test_reciprocal_rank_fusion_rewards_consistent_top_ranking():
    list_a = [{"id": "x", "distance": 0.1}, {"id": "y", "distance": 0.2}]
    list_b = [{"id": "x", "distance": 0.9}, {"id": "z", "distance": 0.95}]  # different scale, x still ranked #1
    fused = _reciprocal_rank_fusion([list_a, list_b])
    assert fused[0]["id"] == "x", "item ranked #1 in both queries should win the fusion regardless of raw distance scale"


def test_fusion_not_dominated_by_one_querys_distance_scale():
    """
    The exact regression this fix addresses: a query whose embeddings
    happen to be uniformly closer (e.g. list_b here) must not dominate
    the merge just because its raw distances are smaller.
    """
    list_a = [{"id": "correct", "distance": 0.85}]  # correct answer, but distance looks "bad"
    list_b = [{"id": "wrong1", "distance": 0.10}, {"id": "wrong2", "distance": 0.11}]  # wrong, but "good" distances
    fused = _reciprocal_rank_fusion([list_a, list_b])
    # rank-based: "correct" is rank 1 in its own list (score 1/61), "wrong1" is also rank 1 in its list (1/61) — tie by rank
    assert fused[0]["id"] in ("correct", "wrong1")
    assert fused[0]["distance"] != min(0.10, 0.85), "must not simply be sorted by raw distance"

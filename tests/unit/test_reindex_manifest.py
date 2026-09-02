"""SHA-256 manifest diff for incremental reindex.

ADR/spec: only policies whose canonical JSON hash changed are re-embedded.
This is the piece that had no unit test — the e2e covers the MiniStack
round-trip, not the hash comparison itself.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from transformation.reindex import (  # noqa: E402
    policies_needing_reembed,
    policy_content_digest,
)


def _policy(policy_id: str, text: str = "clause a") -> dict:
    return {"policy_id": policy_id, "clauses": [{"text": text}]}


def test_first_run_with_empty_manifest_marks_everything_changed():
    policies = [_policy("POL-001"), _policy("POL-002")]
    changed, new_manifest = policies_needing_reembed(policies, {})
    assert [p["policy_id"] for p in changed] == ["POL-001", "POL-002"]
    assert set(new_manifest) == {"POL-001", "POL-002"}


def test_unchanged_content_is_skipped():
    p = _policy("POL-001")
    digest = policy_content_digest(p)
    changed, _ = policies_needing_reembed([p], {"POL-001": digest})
    assert changed == []


def test_content_change_is_detected():
    old = _policy("POL-001", "old clause")
    new = _policy("POL-001", "new clause")
    changed, new_manifest = policies_needing_reembed([new], {"POL-001": policy_content_digest(old)})
    assert len(changed) == 1
    assert new_manifest["POL-001"] == policy_content_digest(new)
    assert new_manifest["POL-001"] != policy_content_digest(old)


def test_key_reordering_does_not_look_like_a_change():
    canonical = {"policy_id": "POL-001", "clauses": [{"text": "x"}]}
    shuffled = {"clauses": [{"text": "x"}], "policy_id": "POL-001"}
    assert json.dumps(canonical) != json.dumps(shuffled)
    assert policy_content_digest(canonical) == policy_content_digest(shuffled)
    changed, _ = policies_needing_reembed([shuffled], {"POL-001": policy_content_digest(canonical)})
    assert changed == []

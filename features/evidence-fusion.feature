Feature: Evidence fusion uses rank, not raw distance
  Spec: docs/specs/spec-agent-retrieval-loop.md

  Scenario: an item ranked first in both lists wins even if distances disagree
    Given two retrieval lists where x is rank 1 in both but distances use different scales
    When reciprocal rank fusion is applied
    Then the top fused item is x

  Scenario: a list of uniformly small distances does not dominate by magnitude
    Given one list with a far correct clause and another with close wrong clauses
    When reciprocal rank fusion is applied
    Then the winner is not chosen by minimum raw distance

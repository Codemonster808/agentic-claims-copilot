Feature: Agent loop token budget is enforced and counted atomically
  Spec: docs/specs/spec-agent-retrieval-loop.md

  Scenario: a budget of 1 token exhausts immediately, reaches the DLQ, and fabricates no answer
    Given a claim with a token budget of 1
    When the agentic loop runs for that claim
    Then the status is budget_exhausted
    And the response has no citations key
    And the claim is recorded in the real DLQ

  Scenario: 20 concurrent gate calls sum to exactly 6000 tokens spent
    Given a fresh claim id
    When the budget gate is called 20 times concurrently for that claim
    Then tokens_spent reaches exactly 6000

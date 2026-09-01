# Security Policy

This is a portfolio/demo project: an agentic claims-retrieval loop built
against **MiniStack** (a local AWS emulator), a fake LLM provider by
default, and synthetic data only. It is not deployed against real AWS
accounts, real insurance data, or a production LLM key by default, and it
carries **no SLA and no guaranteed response time**.

## Supported versions

Only the `main` branch is maintained. There are no released versions or
long-term support branches.

## Reporting a vulnerability

If you find a security issue in this repo, please open a
[GitHub Issue](../../issues) describing it. Since this is a demo project
maintained on a best-effort basis, there is no guaranteed response time
and no bounty program — but reports are welcome and will be looked at.

Please do not use this project's code or infrastructure patterns against
real production systems or real customer/claims data without an
independent security review — it was built to demonstrate an
architecture, not hardened for production use.

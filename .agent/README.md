# Agent workspace: services/ai-gateway
> **Project**: services/ai-gateway

This folder contains agent-facing context, tasks, workflows, and planning artifacts for this submodule.

## Current State
AI Gateway that routes agent and tool traffic with strict read and write separation, budgeting, and contract-first enforcement. Multi-region behavior and read replica fallback patterns are part of the active roadmap.

## Expected State
Operationally safe by default, with strong policy enforcement and observability. All tool dispatch and agent flows validated and audited.

## Behavior
Provides agent-facing APIs and tool routing. Applies budgets, allowlists, and security invariants before invoking downstream tools.

## How to work here
- Run/tests:
- Local dev:
- CI notes:

## Interfaces and dependencies
- Owned APIs/contracts:
- Depends on:
- Data stores/events (if any):

## Global context
See `.agent/context.md` for monorepo-wide invariants and architecture.

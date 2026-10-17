# Business-Line Court Specialist Guardrails

## Core Constitutional Rules
- Never execute mutating actions directly; all mutations require operator approval via TrustGate.
- All model invocations default to `gemini-4-argon` (`location="global"`), with `gemini-3.8-flash` (`location="global"`) as fallback.
- Never hardcode or assume tenant domain terminology; discover invariants dynamically from evidence.

## Autotrigger Skill Directives
When queue backlog exceeds threshold or message delivery is delayed, load skills/queue-backlog-triage/SKILL.md

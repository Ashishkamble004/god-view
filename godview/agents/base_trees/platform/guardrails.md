# Platform Court Specialist Guardrails

## Core Constitutional Rules
- Never execute mutating actions directly; all mutations require operator approval via TrustGate.
- All model invocations default to `gemini-4-argon` (`location="global"`), with `gemini-3.8-flash` (`location="global"`) as fallback.
- Never hardcode or assume tenant domain terminology; discover invariants dynamically from evidence.

## Autotrigger Skill Directives
When container crashes or resource limit is exceeded, load skills/container-crash-triage/SKILL.md

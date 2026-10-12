# Prompt catalog

Task prompts for the recurring operations here. Each is a **procedure with a
decision at the end**, not a description of the codebase — that lives in
[`AGENTS.md`](../../AGENTS.md) and [`docs/`](../../docs/), and these link to it
rather than copying it. One copy, one place to keep current.

| Prompt | Use when |
| --- | --- |
| [`diagnose-build-failure.prompt.md`](diagnose-build-failure.prompt.md) | A `Build And Promote Main Image` run is red |
| [`replay-a-build.prompt.md`](replay-a-build.prompt.md) | You need to reproduce a specific past build to diagnose it |
| [`review-safety-critical-change.prompt.md`](review-safety-critical-change.prompt.md) | A diff touches one of the seven files in AGENTS.md section 0 rule 2 |

The `.prompt.md` suffix is required, not decorative: GitHub Copilot discovers
prompt files in `.github/prompts/` by that extension, so a plain `.md` here
would be invisible to it. Each carries `description` and `mode` frontmatter for
the same reason.

## The rule that outranks every prompt here

None of these ends with "make it green". Two of them can end with *stop and
report*, which is the correct outcome when a fail-closed check is doing its job.
A guard that fires is reporting a real condition; the fix is the underlying
cause, never relaxing the guard (AGENTS.md section 0 rule 1).

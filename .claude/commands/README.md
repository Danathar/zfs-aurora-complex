# Claude Code commands

Slash commands for this repository. Each is a thin pointer at the matching file
in [`.github/prompts/`](../../.github/prompts/), which is where the actual
procedure lives — so there is one copy to keep current, and Copilot, Cursor and
Claude all read the same source.

| Command | Does |
| --- | --- |
| `/diagnose-build` | Name why a production run is red, with evidence, and say which fix applies |
| `/replay-build` | Rebuild with the exact inputs a past run used, without promoting |
| `/review-safety-critical` | Review a diff touching the signing or promotion path |

If a command here and its prompt disagree, the prompt is right and the command
is the bug.

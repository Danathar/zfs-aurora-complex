---
description: Rebuild with the exact inputs a past run used, for diagnosis
---

Follow [`.github/prompts/replay-a-build.prompt.md`](../../.github/prompts/replay-a-build.prompt.md).

That file is the procedure; do not restate it here. The two things that go
wrong:

- **`zfs_version` left empty in the lock file.** The resolver then re-resolves
  the newest release on `zfs_minor_version`, so the replay stops reproducing
  the original ZFS version as soon as a newer patch exists.
- **Dispatching with defaults.** `promote_to_stable` defaults to `true`. A
  diagnostic replay left at the default moves `:latest` to an image built for
  diagnosis, which `bootc upgrade` then pulls.

Do not propose the dispatch command for the maintainer to run without
`promote_to_stable=false`, and do not run it yourself: AGENTS.md section 0 rule
6 says registry tags are not an agent's to move.

Run to replay, if given: $ARGUMENTS

# Session summary

Carried state between agent sessions. Read this before starting; update it
before finishing.

Not a changelog — `git log` is the changelog, and it is better than this file
will ever be. This holds only what a fresh session cannot recover from the tree:
work in flight, decisions taken but not yet visible in a diff, and things known
to be true that no file states.

Keep it short. If a section grows past a screen, the durable part of it belongs
somewhere permanent:

- A wrong belief that cost time → [`.claude/memory/corrections.md`](memory/corrections.md)
- How something works → `docs/`, per [`docs/documentation-guide.md`](../docs/documentation-guide.md)
- A pin or decision on a clock nobody watches → [`docs/maintenance-watchlist.md`](../docs/maintenance-watchlist.md)
- A procedure → [`.github/prompts/`](../.github/prompts/)

Stale content here is worse than an empty file: a session that trusts it will
act on it. When in doubt, delete the section rather than leave it uncertain.

---

## Current state

Last updated: 2026-10-11.

`:latest` is published and carries ZFS 2.4.4 (its
`org.zfs-aurora-complex.zfs-version` label, read from the registry on
2026-10-11), the 2.4 line that `ci/defaults.json` configures. The 25 most recent
`build.yml` runs, 2026-09-18 to 2026-10-11, all concluded `success`; the nightly
schedule is `00 06` UTC.

That is the pipeline completing, not the image being good. Say which run you
read when you report on one (AGENTS.md section 0 rule 4).

## In flight

The Hive ACMM gap issues (#79 to #121) are being landed as slice pull requests
that target a staging branch, `acmm/integration`, rather than `main`, so that
only one production `build.yml` run results from the whole series. Check the
open pull requests before starting overlapping work. Update this section when
leaving work unfinished, with enough to resume: branch, what is done, what is
not, and what you were about to check.

## Standing constraints a session should not rediscover

- **Merging to `main` publishes.** `build.yml` triggers on push to `main`
  (excluding `**/*.md` and `docs/**`), then builds, signs, and promotes to
  `:latest`. There is no staging tier. AGENTS.md section 0 rule 6: propose, do
  not push, promote, tag, or delete.
- **`main` only changes through a pull request.** A ruleset refuses direct and
  force pushes, and a pull request cannot merge until a green `Python Unit Tests`
  check reports. See [`docs/branch-protection.md`](../docs/branch-protection.md).
- **Image builds are slow.** Pushing a branch triggers `Compute Branch Tag`,
  `Build Or Reuse Shared ZFS Akmods Cache` and `Build Branch Image`; opening the
  pull request triggers `Build PR Image (No Push)`. They take tens of minutes,
  so do not read a pending image build as a hang.
- **A bot-authored branch is built but not pushed.** `build-branch.yml` skips
  its push step when the actor is a bot account, so a Renovate branch validates
  the image locally and publishes no `br-*` tag. Branch images are unsigned
  test images and are never what `bootc upgrade` pulls.
- **`unexpected EOF` during a blob copy is a registry or CDN failure**, common
  enough here to rule out first, and never a reason to change code.

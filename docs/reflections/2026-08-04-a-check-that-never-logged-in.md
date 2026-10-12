# A check that never logged in

*2026-08-04*

## What happened

`ci_tools/check_akmods_cache.py` decides whether the shared akmods cache can be
reused. It called `skopeo inspect`, `skopeo copy` and `cosign verify` with no
credentials, and `main()` never read `REGISTRY_ACTOR` or `REGISTRY_TOKEN` at all.
It was the only skopeo-calling helper here that did not authenticate;
`check_stable_signal.py`, `promote_stable.py` and `write_last_good_build_badge.py`
all did.

Nothing noticed, because this repository's own akmods package is public and an
anonymous read works. The omission was invisible for as long as the package
stayed public.

It would have surfaced the first time someone forked the repository. GHCR creates
a new package as **private**, and answers an anonymous read of a private or
nonexistent package with a `403`, not `manifest unknown`, deliberately, so it
cannot confirm whether a private package exists. `is_missing_image_error()`
correctly declines to read a `403` as "missing", so the job would die before the
rebuild step that seeds the cache: the first probe on a fresh fork deadlocked its
own bootstrap.

## What changed

[#64](https://github.com/Danathar/zfs-aurora-complex/pull/64) threads credentials
through all three registry calls and makes them **required**. Required, not
optional, on purpose: an anonymous fallback is precisely what let this hide.
`cosign verify` needed the credentials for a reason of its own. Fetching a
signature is an ordinary registry read, and any failure there is caught and read
as "not signed", so on a private package a correctly signed cache would have been
rejected on every run and the kernel modules rebuilt forever. That is fail-closed,
and silently wasteful.

A second change made the failure explain itself. `prepare_validation_build` reads
the cache anonymously (`build-pr.yml` has no registry login), and a permission
error there escaped as a raw skopeo failure, ahead of a branch whose advice, "rebuild",
could not fix a permission problem. Permission-shaped failures now re-raise with
the likely cause, and everything else propagates untouched, so a transport error
does not send anyone off to change a package setting.

## What to carry forward

**A check that works only because the thing it checks is public has not been
tested against the case that matters.** Passing here proved that anonymous access
to a public package works, not that the check handles credentials.

**When one helper in a family differs from its siblings, find out why.** One helper
out of four that did not authenticate was visible in a read of the code, with no
failure needed.

**A catch-all that turns every error into "no" hides real causes.** The
`cosign verify` path read an auth failure as an unsigned cache. A check that fails
closed on the wrong reason is correct and still wrong: it is the same refusal with
a different meaning, and a log reader will believe the first one.

**Make the safe path the only path.** The fix did not add authentication where it
was missing and keep anonymous as a fallback. It removed the anonymous option.

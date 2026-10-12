# The nightly build that outgrew its argument list

*2026-08-25*

## What happened

The 2026-08-25 scheduled `build.yml` run
([`32816124214`](https://github.com/Danathar/zfs-aurora-complex/actions/runs/32816124214))
failed in `Build Candidate Image`, at the Chunkah rechunk step, with

```text
/usr/local/bin/podman: Argument list too long
##[error]Process completed with exit code 126.
```

This was not ZFS, the kernel, akmods or signing. The akmods cache had built and
signed, and the candidate image itself had built. The run died while re-layering an
image that already existed.

The cause was that the rechunk action put the whole `podman inspect` document into
an environment variable, and passed it through `podman run -e`. Linux caps a single
argument or environment string at `MAX_ARG_STRLEN` (131072 bytes), and `execve`
returns `E2BIG` past it. A full `podman inspect` records the layers three times
(`RootFS.Layers`, `History` and `GraphDriver.Data.LowerDir`), so it grows with the
base image's layer count, and the upstream Aurora base doubled that count between
two builds: from 128 layers in `stable-20260819.1` to 256 in `stable-20260825.1`.
The payload went from roughly 73 KB to over 120 KB, under the cap one day and over
it the next. Nothing in this repository had changed.

## What changed

[#72](https://github.com/Danathar/zfs-aurora-complex/pull/72) passes Chunkah only
the `.Config` element of the inspect output, which is what Chunkah documents
`--config-str` as taking, and which stays around 2 KiB regardless of layer count.
The pull request showed the resulting image config was identical to the old
output's: same `Env`, same `Cmd`, all 11 labels preserved. It also found the
boundary empirically, rather than quoting it: an env string of 131065 bytes
executes, 131070 fails.

## What to carry forward

**Some failures are deterministic and arrive on a schedule you do not control.**
Every run would have kept failing here until fixed, and no commit caused it. A
nightly build is exercised by upstream as much as by this repository, and a limit
nobody tested only shows up when upstream crosses it. That is why the daily build
exists, and why a stale `:latest` after a red night is a cost and not a hazard:
the image on the machine did not change.

**Size is an input.** Anything piped through an environment variable or an argument
list scales with something upstream controls. The pull request named the same risk
for anything else in the pipeline that passes large JSON through the environment,
and left the second question, whether `--max-layers 128` is still the intended
setting for a 256-layer base, open; the action still passes it.

**Read what the failing step actually received.** The first reading of "Argument
list too long" suggests a shell problem. The useful move was to find the payload's
size and compare it against the last good run's `build-inputs` artifact, which
showed the doubling directly.

**A failure that has not been classified is not yet a finding.** The classification
loop in [`docs/metrics.md`](../metrics.md) now has this message in it, so a
recurrence is named on sight.

# The build worked and the install did not

*2026-07-28*

## What happened

The maintainer's pools run the OpenZFS 2.3 line, and this image was publishing
2.4.3. Switching a machine onto it would cross a ZFS line, and once a 2.4 pool
feature activates, the previous image can no longer import that pool, which is
what rollback, this repository's whole recovery model, depends on. So
[#54](https://github.com/Danathar/zfs-aurora-complex/pull/54) set
`DEFAULT_ZFS_MINOR_VERSION` to `2.3`.

The check done beforehand was the right-looking one. Both lines declare the same
kernel ceiling in OpenZFS's `META`, and the current kernel (`7.0.12-201.fc44`) was
inside it. The akmods build then proved it: ZFS 2.3.8 compiled cleanly in 16
minutes 51 seconds, and the signing job passed.

The **image** build failed:

```text
installed package libzfs7-2.4.3-1.fc44.x86_64 obsoletes libzfs6 <= 2.4.3
           provided by libzfs6-2.3.8-1.fc44.x86_64 from @commandline
```

The Aurora DX base image already ships ZFS 2.4.3 userspace. `libzfs7` and
`libzpool7` obsolete the 2.3 packages, so 2.3.8 userspace cannot be installed on
top. This repository does not choose the ZFS line the image ships. The base image
does, and this repository chooses only which line the kernel module is built
from.

## What changed

#54 was closed unmerged with its reasoning in the closing comment, and the
constraint was written into
[`safety-model.md`](../safety-model.md#the-zfs-line-is-set-by-the-base-image-not-by-this-repo)
so it would not be re-derived. `:latest` was never touched. The branch run had rebuilt and signed
the shared akmods cache at 2.3.8 before failing, and the closing comment expected
the next `main` run to reject that cache and rebuild at 2.4.3, at the cost of one
cache rebuild.

Two workarounds were considered and rejected. Removing the base's ZFS packages
diverges from Aurora's own stack and needs re-checking on every base update.
Pinning an older base image surrenders the kernel currency this repository exists
to track. The mitigation for crossing the line is the rollback discipline that was
already documented: keep the previous image as a rollback target, and do not run
`zpool upgrade` until the new line is trusted.

## What to carry forward

**"Can it build" and "can it install" are different questions, and the cheap one
is not evidence for the expensive one.** The author of #54 checked that 2.3.8
would compile against the kernel and never checked that it would install beside
what the base already carries. The second question decided the outcome, and it
cost a 17-minute build to find out. A green akmods job says nothing about the
image step that follows it.

**A change to a pin the base image also constrains has to be tested against the
base.** Before changing `DEFAULT_ZFS_MINOR_VERSION`, find out which ZFS userspace
packages the current base image already carries (the failure above names
`libzfs7` and `libzpool7`) and compare them with the line you want, or expect the
image step to be the real test.

**The closing comment is the record.** #54 produced no code and a good deal of
knowledge. It is in the pull request thread because that is where the next person
will search, and in `safety-model.md` because that is where they will read.

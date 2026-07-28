# Proposal: Runtime ZFS Validation Before Unattended Promotion

**Status: proposal, not adopted.** Nothing here has been implemented. This is the last item
from the 2026-07-28 production-readiness review, and the one that actually gates the claim
"safe for unattended promotion to a machine with pools attached."

## The gap, stated precisely

Promotion to `:latest` today requires: input resolution, a verified akmods cache (exact kernel,
exact OpenZFS patch, valid signature), a successful image composition, `bootc container lint`,
a signed candidate, and signature re-verification before the digest is copied.

None of that is **runtime** evidence. Specifically, nothing has ever proven, before promotion,
that:

- the module loads into a running kernel (`modprobe zfs` succeeds)
- userspace and kmod versions agree at runtime
- a pool can be imported, written to, scrubbed, and exported
- the image boots at all

What *is* already checked, and should not be re-litigated:

- `containerfiles/zfs-akmods/install_zfs_from_akmods_cache.py::verify_zfs_module_present`
  fails the build if no `zfs.ko*` exists under `/lib/modules/<primary-kernel>/extra/zfs`,
  then runs `depmod -a <kernel>` for that release
- the installer refuses a cache containing two `kmod-zfs` RPMs for one kernel
- `bootc container lint` runs in the `Containerfile`

So "the module file exists and is for the right kernel" is covered. "The module works" is not.

The single boot test performed to date (2026-07-28, maintainer, in a VM) was a genuine
end-to-end confirmation — label `zfs-version=2.4.3` matched a booted `zfs-2.4.3-1` on kernel
`7.0.12-201.fc44` — but it was manual, one-off, on a digest that has since been superseded
twice, and had no pool attached.

## Three tiers, cheapest first

These are independent; adopting tier 1 does not require tier 3.

### Tier 1 — make promotion attended (recommended first, cheapest by far)

The problem is framed as "unattended promotion." The cheapest correct fix is not to automate
a boot test, it is to **stop promotion being unattended**.

A GitHub Environment supports **required reviewers**. Putting `promote-stable` behind such an
environment means `:latest` cannot move until a human approves the run — at which point the
human can boot the candidate in a VM if they judge it warranted.

- **Cost:** near zero. It composes with the environment already proposed in
  `production-boundary-proposal.md`; this is one more environment (or one more setting).
- **Effect:** removes the entire "unattended" property that makes the current gap serious.
  The candidate is still built, signed, and available to test by digest.
- **Trade-off:** the daily scheduled build stops auto-publishing. For a repo whose whole point
  is tracking a moving kernel, that is a real behavioural change — `:latest` would lag until
  approved. Worth being deliberate about; it may be the correct posture anyway given the
  maintainer daily-drives the result.
- **Note:** this is a repository setting, not a code change. An agent cannot apply it.
- **Do not enable "Prevent self-review" on that environment.** It is optional and off by
  default. In a solo-maintainer repository, enabling it means the only reviewer is forbidden
  from approving, which blocks promotion entirely with no way through except changing the
  setting back. See "Lockout safety" in
  [`production-boundary-proposal.md`](./production-boundary-proposal.md).

### Tier 2 — runtime smoke test in CI, no pools

Boot the candidate image and prove the module loads and versions agree.

Sketch: build a disk image from the candidate with `bootc-image-builder`, boot it under QEMU on
the runner, and on the booted system assert `modprobe zfs` succeeds, `zfs --version` matches
the `org.zfs-aurora-complex.zfs-version` label, and `zpool create` on a loopback file works.

**Unverified and must be checked before committing to this:** whether GitHub-hosted runners
expose `/dev/kvm`. Without hardware acceleration, QEMU falls back to TCG software emulation and
a full Fedora boot may take long enough to be impractical on a standard runner. This is
cheaply testable with a throwaway workflow that checks for `/dev/kvm` and times a boot — **do
that before designing around it.** I have not verified it, and the tier's viability rests on
the answer.

- **Cost:** a new workflow, a disk-image build step, and a boot harness. Meaningful, and it
  becomes a maintained thing that can itself break and block promotion.
- **Effect:** catches "module does not load" and "versions disagree at runtime" automatically,
  which is most of the practical risk.

### Tier 3 — pool-attached validation

Import a real pool, write, scrub, export, and confirm rollback compatibility.

This is where the actual data-loss risk lives (`CLAUDE.md` rule 5: a newer ZFS line can activate
pool features the previous image cannot import), and it is also the tier CI is worst suited to.
A scratch pool on a loopback file proves the code path but not compatibility with the
maintainer's real pools and their activated feature flags.

Realistic form: a **self-hosted runner** on the maintainer's hardware, or a documented manual
checklist run before accepting a ZFS line bump. Note this is only needed when the ZFS line
moves — patch bumps inside a line do not activate new pool features — so it maps naturally to a
manual gate rather than every-build automation.

## Recommendation

1. **Tier 1 now.** It directly answers the finding, costs almost nothing, and composes with
   work already proposed. Decide deliberately about `:latest` lagging.
2. **Verify the `/dev/kvm` question** before doing any Tier 2 design work.
3. **Tier 3 as a documented manual gate tied to ZFS line changes**, not as CI automation.
   Automating it badly would produce false confidence, which is worse than an honest checklist.

## What this does not claim

Adopting all three tiers would not make the pipeline "safe" in an absolute sense. It would move
the guarantee from "the image composed and is correctly signed" to "the image composed, is
correctly signed, and its ZFS module loaded and handled a pool on at least one machine." That is
a real improvement and still not proof for every pool and every kernel. The honest framing
stays: CI evidence bounds risk, it does not eliminate it.

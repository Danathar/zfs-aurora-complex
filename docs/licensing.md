# Licensing Note

If a term is unfamiliar, check the shared glossary first:
[`docs/glossary.md`](./glossary.md)

## Purpose

This page records the licensing position of the artifact this repository
publishes. It is short on purpose, and it is not legal advice.

ZFS is distributed under the Common Development and Distribution License (CDDL). The Linux kernel is distributed under version 2 of the GNU General Public License (GPLv2). The Software Freedom Law Center, the Free Software Foundation, and the OpenZFS project itself have long-standing disagreements about whether redistributing a binary kernel module built against a Linux kernel satisfies both licenses. This repository produces exactly such a binary: a `kmod-zfs` package compiled against a Fedora kernel, baked into a published container image.

This is not a legal opinion and nothing in this repository is legal advice. Operators running this image, redistributing it, or using it as a basis for a downstream image should read the [OpenZFS FAQ on licensing](https://openzfs.github.io/openzfs-docs/Project%20and%20Community/FAQ.html#licensing) and decide for themselves whether their use falls inside what they are comfortable shipping.

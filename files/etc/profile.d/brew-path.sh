# Put the Homebrew prefix from the `ublue-os/brew` payload on PATH.
#
# This file deliberately runs nothing out of that prefix. `brew-setup.service`
# extracts the payload on first boot and ends with
# `chown -R 1000:1000 /home/linuxbrew`, so every file under the prefix -- the
# `brew` executable included -- is writable by the desktop user. Files in
# /etc/profile.d are read by every login shell, root's among them (`su -`,
# `sudo -i`, a console or SSH root login), so a fragment that executes or
# `eval`s anything from the prefix hands its owner code execution as root.
#
# Setting PATH is all the payload is here for: `brew` works when the account
# that owns the prefix types it, and no login shell executes prefix content on
# the way in.
#
# `-O` is "owned by the effective user", so this is inert for root and for any
# other account: only the owner of the prefix gets it on PATH. The prefix is
# appended, not prepended, so system binaries keep priority -- the same
# ordering the payload's own fragment aimed for.
if [ -O /home/linuxbrew/.linuxbrew ] && [ -d /home/linuxbrew/.linuxbrew/bin ]; then
  case ":${PATH}:" in
    *":/home/linuxbrew/.linuxbrew/bin:"*) ;;
    *)
      PATH="${PATH}:/home/linuxbrew/.linuxbrew/bin:/home/linuxbrew/.linuxbrew/sbin"
      export PATH
      ;;
  esac
fi

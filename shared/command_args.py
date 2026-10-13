"""
Module: shared/command_args.py
What: Shared redaction of credential values in command argv.
Doing: Replaces the value of each known credential flag with a placeholder so
an external command can be named in an error message.
Why: CI helpers and the image-build ZFS helper both report failed commands.
One flag list keeps a credential flag added for one of them from reaching the
other's error text unredacted.
Goal: Keep failure messages readable without printing registry credentials.
"""

from __future__ import annotations

from collections.abc import Sequence

SECRET_ARG_FLAGS = {
    "--creds",
    "--src-creds",
    "--dest-creds",
    "--registry-username",
    "--registry-password",
}


def redact_command_args(args: Sequence[str]) -> list[str]:
    """Return command args with known secret values replaced for error messages."""
    redacted: list[str] = []
    redact_next = False
    for arg in args:
        if redact_next:
            redacted.append("***REDACTED***")
            redact_next = False
            continue
        flag, separator, _value = arg.partition("=")
        if flag in SECRET_ARG_FLAGS:
            if separator:
                redacted.append(f"{flag}=***REDACTED***")
            else:
                redacted.append(arg)
                redact_next = True
            continue
        redacted.append(arg)
    return redacted

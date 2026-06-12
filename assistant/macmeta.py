"""Native macOS extended-attribute tagging: write a summary onto a file so
Spotlight can surface it by concept later.

Two attributes are written. `com.apple.metadata:kMDItemFinderComment` is the
Finder comment Spotlight indexes for free-text search — tag a dense PDF with
"propofol induction dosing" and months later `mdfind` (or Cmd-Space) finds the
file even though that phrase never appears in its text. `com.aide.summary` is a
plain-UTF-8 copy in Aide's own namespace, immune to the Finder-comment quirks
(the canonical comment is half-owned by Finder/.DS_Store), so reading a tag back
is reliable.

macOS has no os.setxattr (that syscall is Linux-only), and the Finder-comment
value must be a binary-plist-encoded string, so the low-level access goes through
libc setxattr/getxattr via ctypes. Everything degrades to a clear error string on
a non-Mac or a permission failure; nothing here raises into a turn.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import plistlib
import subprocess
import sys

_FINDER = b"com.apple.metadata:kMDItemFinderComment"
_AIDE = b"com.aide.summary"
_MARK = "[Aide]"            # marks Aide's slice of a shared Finder comment
_XATTR_NOFOLLOW = 0x0001    # resolve symlinks like Finder does: follow them (0)

_libc = None


def _lib():
    """Lazily bind libc setxattr/getxattr with the macOS (6-arg) signatures."""
    global _libc
    if _libc is None:
        lib = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        # int setxattr(path, name, value, size, u_int32_t position, int options)
        lib.setxattr.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p,
                                 ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int]
        # ssize_t getxattr(path, name, value, size, u_int32_t position, int options)
        lib.getxattr.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p,
                                 ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int]
        lib.getxattr.restype = ctypes.c_ssize_t
        _libc = lib
    return _libc


def _set(path: str, name: bytes, value: bytes) -> None:
    rc = _lib().setxattr(path.encode(), name, value, len(value), 0, 0)
    if rc != 0:
        err = ctypes.get_errno()
        raise OSError(err, f"setxattr failed (errno {err})")


def _get(path: str, name: bytes) -> bytes | None:
    lib = _lib()
    size = lib.getxattr(path.encode(), name, None, 0, 0, 0)
    if size < 0:
        return None
    buf = ctypes.create_string_buffer(size)
    got = lib.getxattr(path.encode(), name, buf, size, 0, 0)
    return buf.raw[:got] if got >= 0 else None


def get_finder_comment(path: str) -> str:
    """The file's current Finder comment, or "" if none/unreadable."""
    raw = _get(path, _FINDER)
    if not raw:
        return ""
    try:
        val = plistlib.loads(raw)
        return val if isinstance(val, str) else ""
    except Exception:  # noqa: BLE001 - a malformed comment reads as empty
        return ""


def get_summary(path: str) -> str:
    """Aide's own stored summary for the file (the com.aide.summary xattr)."""
    raw = _get(path, _AIDE)
    return raw.decode("utf-8", "replace") if raw else ""


def _without_aide_block(comment: str) -> str:
    """Drop a prior `[Aide] ...` block so re-tagging replaces it rather than
    stacking duplicates, while preserving any comment the user wrote by hand."""
    i = comment.find(_MARK)
    return comment[:i].rstrip() if i != -1 else comment.rstrip()


def tag_file(path: str, summary: str) -> str:
    """Write `summary` onto the file's Spotlight metadata. Returns a status line.
    Preserves a user-authored Finder comment, replaces any previous Aide block,
    and forces a reindex so the tag is searchable immediately."""
    if sys.platform != "darwin":
        return "tag_file only works on macOS."
    summary = " ".join(summary.split())          # one tidy line for the comment
    if not summary:
        return "Nothing to tag: empty summary."

    try:
        existing = get_finder_comment(path)
    except OSError as exc:
        return f"Could not read existing tag: {exc.strerror or exc}"

    user_part = _without_aide_block(existing)
    comment = (f"{user_part}\n\n" if user_part else "") + f"{_MARK} {summary}"

    try:
        _set(path, _FINDER, plistlib.dumps(comment, fmt=plistlib.FMT_BINARY))
        _set(path, _AIDE, summary.encode("utf-8"))
    except OSError as exc:
        # errno 1 (EPERM)/13 (EACCES): a read-only file or a sandbox boundary.
        return f"Could not tag {path}: {exc.strerror or exc} (errno {exc.errno})."

    # Nudge Spotlight to index the new comment now instead of on its own schedule.
    try:
        subprocess.run(["mdimport", path], capture_output=True, timeout=10)
    except Exception:  # noqa: BLE001 - indexing catches up on its own if this fails
        pass

    kept = " (kept your existing comment)" if user_part else ""
    return f"Tagged {path} for Spotlight search{kept}: {summary}"

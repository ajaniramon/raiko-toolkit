"""Session storage: one JSON file per saved conversation under sessions/
(gitignored). A session stores its provider + model so it can be restored
exactly, and the working directory (`cwd`) it ran in so each folder has its
own history (`raiko --continue` / `--resume`, the panel's per-project view).

Sessions written before `cwd` existed simply have no such key; they normalize
to "" and behave as "global" — they never match a folder filter, but they are
still listed and resumable."""

import json
import os
import re
import tempfile

from engine.config import _app_home

SESSIONS_DIR = os.path.join(_app_home(), "sessions")
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,96}$")


def valid_session_id(sid):
    return isinstance(sid, str) and bool(SESSION_ID_RE.fullmatch(sid))


def _session_path(sid):
    if not valid_session_id(sid):
        raise ValueError("invalid session id")
    return os.path.join(sessions_dir(), sid + ".json")


def sessions_dir():
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    return SESSIONS_DIR


def normalize_cwd(path):
    """Canonical key used to compare working directories: absolute, symlink- and
    case-resolved (Windows paths differ only in case all the time). Falsy input
    -> "" (a session with no folder of its own)."""
    if not path:
        return ""
    try:
        return os.path.normcase(os.path.realpath(os.path.expanduser(str(path))))
    except (OSError, ValueError):
        return ""


def session_cwd(sess):
    """The normalized cwd of a saved session dict ("" for pre-cwd sessions)."""
    return normalize_cwd((sess or {}).get("cwd"))


def list_sessions(cwd=None):
    """Saved sessions (full dicts), newest-updated first. With `cwd`, only the
    ones saved in that folder; without it, every session."""
    out = []
    try:
        for fn in os.listdir(sessions_dir()):
            if not fn.endswith(".json"):
                continue
            try:
                out.append(json.load(open(os.path.join(SESSIONS_DIR, fn), encoding="utf-8")))
            except Exception:
                continue
    except Exception:
        pass
    if cwd is not None:
        want = normalize_cwd(cwd)
        out = [s for s in out if session_cwd(s) == want]
    out.sort(key=lambda s: s.get("updated", ""), reverse=True)
    return out


def last_session(cwd=None):
    """The most recently updated session (of `cwd`, if given) — `--continue`."""
    sessions = list_sessions(cwd)
    return sessions[0] if sessions else None


def load_session(sid):
    if not valid_session_id(sid):
        return None
    try:
        with open(_session_path(sid), encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return None


def write_session(sess):
    sid = sess.get("id") if isinstance(sess, dict) else None
    if not valid_session_id(sid):
        return False
    tmp_path = None
    try:
        path = _session_path(sid)
        fd, tmp_path = tempfile.mkstemp(prefix=f".{sid}.", suffix=".tmp", dir=sessions_dir())
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(sess, handle, indent=2, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        return True
    except Exception:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        return False


def delete_session(sid):
    if not valid_session_id(sid):
        return False
    try:
        os.remove(_session_path(sid))
        return True
    except Exception:
        return False

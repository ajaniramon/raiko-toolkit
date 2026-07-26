"""Session storage: one JSON file per saved conversation under sessions/
(gitignored). A session stores its provider + model so it can be restored
exactly. Moved verbatim from tui.py (Fase 2)."""

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


def list_sessions():
    """All saved sessions (full dicts), newest-updated first."""
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
    out.sort(key=lambda s: s.get("updated", ""), reverse=True)
    return out


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

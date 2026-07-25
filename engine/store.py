"""Session storage: one JSON file per saved conversation under sessions/
(gitignored). A session stores its provider + model so it can be restored
exactly. Moved verbatim from tui.py (Fase 2)."""

import json
import os

from engine.config import _app_home

SESSIONS_DIR = os.path.join(_app_home(), "sessions")


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
    try:
        return json.load(open(os.path.join(sessions_dir(), sid + ".json"), encoding="utf-8"))
    except Exception:
        return None


def write_session(sess):
    try:
        path = os.path.join(sessions_dir(), sess["id"] + ".json")
        json.dump(sess, open(path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    except Exception:
        pass


def delete_session(sid):
    try:
        os.remove(os.path.join(sessions_dir(), sid + ".json"))
    except Exception:
        pass

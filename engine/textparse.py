"""Recovery of tool-calls that models emit as plain text, and the <think> stream
splitter. Moved verbatim from tui.py (Fase 2); pure string logic, no UI."""

import json
import re

# ---- parsing of tool-calls emitted as TEXT (fallback) ----
_TC_BLOCK = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL)  # captures the WHOLE block
_TC_OPEN = re.compile(r"<tool_call>\s*(\{.*)", re.DOTALL)                 # block without closing tag
_TC_FUNC = re.compile(r"<function=([\w\-/.]+)>(.*?)</function>", re.DOTALL)
_TC_PARAM = re.compile(r"<parameter=([\w\-]+)>(.*?)</parameter>", re.DOTALL)


def _balanced_objects(s):
    """Returns all top-level JSON objects {...} (with well-balanced nested
    braces) found in s."""
    objs, i, n = [], 0, len(s)
    while i < n:
        if s[i] == "{":
            depth, start = 0, i
            j = i
            in_str = esc = False
            while j < n:
                ch = s[j]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == '"':
                        in_str = False
                elif ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        objs.append(s[start:j + 1])
                        i = j
                        break
                j += 1
        i += 1
    return objs


def _as_call(d):
    if not isinstance(d, dict):
        return None
    name = d.get("name") or d.get("tool") or d.get("function")
    if not name:
        return None
    a = d.get("arguments", d.get("parameters", d.get("args", {})))
    return {"name": name, "arguments": a if isinstance(a, str) else json.dumps(a)}


def parse_text_tool_calls(content):
    """Recovers tool-calls that the model emitted as plain text. Supports:
    - <tool_call>{...}</tool_call> with NESTED arguments (parsing by balanced braces),
      several blocks, and blocks without a closing </tool_call>.
    - <function=NAME><parameter=p>val</parameter></function>.
    - Loose JSON {"name":...,"arguments":...} without tags."""
    content = content or ""
    out = []
    blocks = _TC_BLOCK.findall(content)
    if not blocks:
        m = _TC_OPEN.search(content)
        if m:
            blocks = [m.group(1)]
    for b in blocks:
        for frag in (_balanced_objects(b) or [b]):
            try:
                call = _as_call(json.loads(frag))
            except Exception:
                call = None
            if call:
                out.append(call)
    if out:
        return out
    # XML format <function=...>
    for m in _TC_FUNC.finditer(content):
        args = {p: v.strip() for p, v in _TC_PARAM.findall(m.group(2))}
        out.append({"name": m.group(1), "arguments": json.dumps(args)})
    if out:
        return out
    # loose JSON with "name" (without any tag)
    if '"name"' in content:
        for frag in _balanced_objects(content):
            if '"name"' in frag:
                try:
                    call = _as_call(json.loads(frag))
                except Exception:
                    call = None
                if call:
                    out.append(call)
    return out


def strip_tool_call_text(content):
    if not content:
        return content
    c = re.sub(r"<tool_call>.*?</tool_call>", "", content, flags=re.DOTALL)
    c = re.sub(r"<tool_call>\s*\{.*", "", c, flags=re.DOTALL)   # block without closing tag
    c = _TC_FUNC.sub("", c)
    return c.strip()


class ThinkSplitter:
    """Slices a text stream and separates what goes inside <think>...</think>.
    Handles tags split across chunks. feed() returns [(mode, text), ...] where
    mode is 'thinking' or 'content'."""

    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self):
        self.mode = "content"
        self.buffer = ""

    def feed(self, chunk):
        self.buffer += chunk
        out = []
        while True:
            tag = self.OPEN if self.mode == "content" else self.CLOSE
            idx = self.buffer.find(tag)
            if idx != -1:
                if idx > 0:
                    out.append((self.mode, self.buffer[:idx]))
                self.buffer = self.buffer[idx + len(tag):]
                self.mode = "thinking" if self.mode == "content" else "content"
                continue
            safe = len(self.buffer)
            for i in range(min(len(tag) - 1, len(self.buffer)), 0, -1):
                if self.buffer.endswith(tag[:i]):
                    safe = len(self.buffer) - i
                    break
            if safe > 0:
                out.append((self.mode, self.buffer[:safe]))
                self.buffer = self.buffer[safe:]
            break
        return out

    def flush(self):
        if self.buffer:
            out = [(self.mode, self.buffer)]
            self.buffer = ""
            return out
        return []

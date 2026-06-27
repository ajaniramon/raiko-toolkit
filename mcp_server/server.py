"""JJ tools — MCP server + web backoffice.

Exposes the tools over MCP (endpoint /mcp) and, in --http mode, also serves a
BACKOFFICE at / to view/enable/disable tools and create custom tools
(name + description + shell command with {args}) — saves to tools_config.json
and restarts the server to apply.

Usage:
  python server.py                      # stdio (Claude Desktop / local Code)
  python server.py --http --port 8765   # MCP at /mcp + backoffice at /
"""

import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import tools  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402
from mcp.server.transport_security import TransportSecuritySettings  # noqa: E402

CONFIG_PATH = os.path.join(HERE, "tools_config.json")

SIMPLE_TOOLS = [
    "read_file", "write_file", "list_dir", "get_current_directory", "grep",
    "find_files", "read_lines", "head", "tail", "count_lines", "stat_path",
    "tree", "find_in_files", "edit_file",
]

# descriptions for the tools without a docstring (improves the MCP schema and the backoffice)
DESC = {
    "read_file": "Read a UTF-8 text file and return its contents.",
    "write_file": "Write text to a file (creates parent dirs, overwrites existing).",
    "list_dir": "List entries of a directory (one level) with file/folder sizes.",
    "get_current_directory": "Return the current working directory.",
    "grep": "Regex search across files; returns 'path:line: text' matches.",
}

DEFAULT_CONFIG = {
    "disabled": [],
    "custom": [
        {"name": "disk_free", "description": "Show disk usage", "shell": "df -h"},
        {"name": "uptime", "description": "System uptime", "shell": "uptime"},
    ],
}


def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            return json.load(open(CONFIG_PATH, encoding="utf-8"))
        except Exception:
            pass
    json.dump(DEFAULT_CONFIG, open(CONFIG_PATH, "w", encoding="utf-8"), indent=2)
    return DEFAULT_CONFIG


def save_config(cfg):
    json.dump(cfg, open(CONFIG_PATH, "w", encoding="utf-8"), indent=2)


def run_shell(command: str) -> str:
    """Run a shell command (zsh/bash) in the current directory. 25s timeout."""
    blocked = tools.danger_match(command)
    if blocked:
        return f"ERROR: blocked potentially destructive operation: '{blocked}'"
    sh = "/bin/zsh" if os.path.exists("/bin/zsh") else ("/bin/bash" if os.path.exists("/bin/bash") else None)
    try:
        if sh:
            proc = subprocess.run([sh, "-c", command], capture_output=True, text=True, timeout=25)
        else:
            proc = subprocess.run(["powershell", "-NoProfile", "-Command", command],
                                  capture_output=True, text=True, timeout=25)
    except subprocess.TimeoutExpired:
        return "ERROR: command timed out (25s)"
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return (out[:6000] + "\n… (truncated)") if len(out) > 6000 else (out or "(no output)")


def run_python(code: str) -> str:
    """Run a Python 3 snippet and get its printed output. Go-to tool for ANY computation or
    data processing: math, counting, summing, parsing/aggregating CSV/JSON, regex, dates, and
    reading/transforming/writing files. Use it instead of guessing. If the user explicitly says
    to 'use Python', ALWAYS call this tool. Remember to print() the result. cwd = current dir,
    20s timeout. Keep snippets short."""
    return tools.run_python(code)


def run_powershell(command: str) -> str:
    """Run a PowerShell command (pwsh on macOS/Linux, powershell on Windows). 25s timeout.
    Destructive operations are blocked."""
    blocked = tools.danger_match(command)
    if blocked:
        return f"ERROR: blocked potentially destructive operation: '{blocked}'"
    exe = shutil.which("pwsh") or shutil.which("powershell")
    if not exe:
        return "ERROR: PowerShell not found. Install it with: brew install --cask powershell"
    try:
        proc = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-Command", command],
                              capture_output=True, text=True, timeout=25)
    except subprocess.TimeoutExpired:
        return "ERROR: PowerShell timed out (25s)"
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return (out[:6000] + "\n… (truncated)") if len(out) > 6000 else (out or "(no output)")


def os_info() -> str:
    """Detect the operating system and basic machine info (OS, version, arch, host)."""
    d = {
        "system": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
        "processor": platform.processor() or platform.machine(),
        "python": platform.python_version(),
        "hostname": socket.gethostname(),
    }
    if platform.system() == "Darwin":
        try:
            d["macos_version"] = platform.mac_ver()[0]
        except Exception:
            pass
    return json.dumps(d, indent=2)


EXEC_TOOLS = [run_python, run_shell, run_powershell, os_info]


def build_server():
    cfg = load_config()
    disabled = set(cfg.get("disabled", []))
    # trusted local network: we disable the anti DNS-rebinding protection so we
    # can connect by IP on the LAN without a 421 Misdirected Request.
    mcp = FastMCP("jj-tools",
                  transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
    for name in SIMPLE_TOOLS:
        if name not in disabled:
            mcp.add_tool(getattr(tools, name), description=DESC.get(name))
    for fn in EXEC_TOOLS:
        if fn.__name__ not in disabled:
            mcp.add_tool(fn)
    for c in cfg.get("custom", []):
        nm, desc, tmpl = c.get("name"), c.get("description", ""), c.get("shell", "")
        if not nm or nm in disabled or not tmpl:
            continue

        def make(t):
            def custom_tool(args: str = "") -> str:
                """Custom user tool."""
                return run_shell(t.replace("{args}", args))
            return custom_tool
        fn = make(tmpl)
        fn.__name__ = nm
        mcp.add_tool(fn, name=nm, description=desc or f"runs: {tmpl}")
    return mcp


def _full_desc(doc):
    """The FULL description the model sees, normalized to a clean paragraph
    (multiline docstrings with indentation are collapsed to single spaces)."""
    return " ".join((doc or "").split())


def inventory():
    cfg = load_config()
    disabled = set(cfg.get("disabled", []))
    inv = []
    for nm in SIMPLE_TOOLS:
        d = DESC.get(nm) or _full_desc(getattr(tools, nm).__doc__)
        inv.append({"name": nm, "desc": d, "kind": "file", "enabled": nm not in disabled})
    for fn in EXEC_TOOLS:
        inv.append({"name": fn.__name__, "desc": _full_desc(fn.__doc__),
                    "kind": "exec", "enabled": fn.__name__ not in disabled})
    for c in cfg.get("custom", []):
        inv.append({"name": c.get("name"), "desc": c.get("description") or f"runs: {c.get('shell','')}",
                    "kind": "custom", "enabled": c.get("name") not in disabled, "shell": c.get("shell", "")})
    return inv


BACKOFFICE_HTML = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>JJ tools · backoffice</title>
<style>
:root{--ink:#13111c;--panel:#1b1830;--line:#352d52;--text:#ece8f7;--muted:#9b93b8;--gold:#e7b94e;--good:#5fe0a0;--exec:#f472b6;--custom:#46c2f5}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(900px 500px at 80% -10%,#2a1f48,transparent 55%),var(--ink);
color:var(--text);font-family:system-ui,"Segoe UI",sans-serif;line-height:1.5}
.wrap{max-width:880px;margin:0 auto;padding:28px 22px 70px}
h1{font-size:24px;margin:0 0 2px}.mono{font-family:ui-monospace,Menlo,Consolas,monospace}
.sub{color:var(--muted);font-size:14px}.url{margin-top:10px;font-family:ui-monospace,monospace;font-size:12.5px;color:var(--muted)}
.url b{color:var(--gold)}
.bar{display:flex;gap:10px;margin:18px 0;flex-wrap:wrap}
button{font:inherit;border:1px solid var(--line);background:var(--panel);color:var(--text);border-radius:9px;padding:9px 16px;cursor:pointer}
button.primary{background:var(--gold);color:#1a1505;border-color:var(--gold);font-weight:700}
button:focus-visible{outline:2px solid var(--gold);outline-offset:2px}
.grp{font-family:ui-monospace,monospace;font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);margin:22px 0 8px}
.tool{display:flex;align-items:center;gap:12px;background:var(--panel);border:1px solid var(--line);border-radius:11px;padding:12px 14px;margin-bottom:8px}
.tool.off{opacity:.45}
.nm{font-family:ui-monospace,monospace;font-weight:600;font-size:14px;min-width:170px}
.kind{font-family:ui-monospace,monospace;font-size:10px;text-transform:uppercase;letter-spacing:.1em;padding:2px 7px;border-radius:6px;border:1px solid var(--line);color:var(--muted)}
.dsc{color:var(--muted);font-size:13px}
.body{flex:1;min-width:0;display:flex;flex-direction:column;gap:5px}
.shellin{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;background:#13111c;border:1px solid var(--line);color:#caa6ff;border-radius:7px;padding:6px 9px;width:100%}
.shellin:focus-visible{outline:2px solid var(--gold)}
.runs{font-family:ui-monospace,Menlo,monospace;font-size:11px;color:var(--muted)}
.sw{margin-left:auto;width:42px;height:24px;border-radius:999px;background:#3a3357;position:relative;cursor:pointer;border:none;flex:none}
.sw.on{background:var(--good)}.sw::after{content:"";position:absolute;top:3px;left:3px;width:18px;height:18px;border-radius:50%;background:#fff;transition:.15s}
.sw.on::after{left:21px}
form{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px;margin-top:10px;display:grid;gap:10px}
input{font:inherit;background:#13111c;border:1px solid var(--line);color:var(--text);border-radius:8px;padding:9px 11px}
input:focus-visible{outline:2px solid var(--gold)}
label{font-size:12px;color:var(--muted);font-family:ui-monospace,monospace}
.toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);background:var(--good);color:#06150d;padding:10px 18px;border-radius:10px;font-weight:600;opacity:0;transition:.2s}
.toast.show{opacity:1}
</style></head><body><div class=wrap>
<h1>🔧 JJ tools <span class=sub>· backoffice</span></h1>
<div class=sub>Enable/disable tools and create your own. Saving restarts the MCP server.</div>
<div class=url id=url></div>
<div class=bar><button class=primary onclick=save()>💾 Save &amp; restart</button>
<button onclick=location.reload()>↻ Reload</button></div>
<div id=list></div>
<div class=grp>+ new custom tool (shell command)</div>
<form onsubmit="addCustom(event)">
  <div><label>name</label><input id=cname placeholder="e.g. ports" required></div>
  <div><label>description</label><input id=cdesc placeholder="what it does"></div>
  <div><label>shell command — use {args} for input</label><input id=cshell placeholder="lsof -i -P | grep LISTEN" required></div>
  <button type=submit>+ Add custom tool</button>
</form>
<div class=toast id=toast></div>
</div><script>
let TOOLS=[],CUSTOM=[];
async function load(){
  const r=await fetch('/api/tools');const d=await r.json();TOOLS=d.tools;
  document.getElementById('url').innerHTML='MCP endpoint for clients: <b>'+location.origin+'/mcp</b>';
  render();
}
function render(){
  CUSTOM=TOOLS.filter(t=>t.kind==='custom');
  const groups={file:'file tools',exec:'execution',custom:'custom tools'};
  let html='';
  for(const k of ['file','exec','custom']){
    const items=TOOLS.filter(t=>t.kind===k);if(!items.length)continue;
    html+='<div class=grp>'+groups[k]+'</div>';
    for(const t of items){
      const shell=(k==='custom')?`<input class=shellin data-name="${t.name}" value="${(t.shell||'').replace(/"/g,'&quot;')}" spellcheck=false placeholder="shell command (use {args})">`:'';
      html+=`<div class="tool ${t.enabled?'':'off'}"><span class=nm>${t.name}</span>
      <span class=kind style="color:var(--${k})">${k}</span>
      <div class=body><div class=dsc>${t.desc||''}</div>${shell}</div>
      <button class="sw ${t.enabled?'on':''}" aria-label="toggle ${t.name}" onclick="toggle('${t.name}')"></button></div>`;
    }
  }
  document.getElementById('list').innerHTML=html;
}
function toggle(n){const t=TOOLS.find(x=>x.name===n);t.enabled=!t.enabled;render();}
function addCustom(e){e.preventDefault();
  const name=cname.value.trim(),description=cdesc.value.trim(),shell=cshell.value.trim();
  if(!name||!shell)return;
  TOOLS.push({name,desc:description||('runs: '+shell),kind:'custom',enabled:true,shell});
  cname.value=cdesc.value=cshell.value='';render();toast('added — remember to Save');
}
async function save(){
  const disabled=TOOLS.filter(t=>!t.enabled).map(t=>t.name);
  const custom=TOOLS.filter(t=>t.kind==='custom').map(t=>{
    const inp=document.querySelector(`input.shellin[data-name="${t.name}"]`);
    return {name:t.name,description:t.desc,shell:inp?inp.value.trim():(t.shell||'')};
  });
  await fetch('/api/save',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({disabled,custom})});
  toast('saved · restarting server…');setTimeout(()=>location.reload(),2500);
}
function toast(m){const t=document.getElementById('toast');t.textContent=m;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),2200);}
load();
</script></body></html>"""


def http_app(mcp):
    app = mcp.streamable_http_app()
    from starlette.responses import HTMLResponse, JSONResponse

    async def home(req):
        return HTMLResponse(BACKOFFICE_HTML)

    async def api_tools(req):
        return JSONResponse({"tools": inventory()})

    async def api_save(req):
        body = await req.json()
        cfg = load_config()
        cfg["disabled"] = body.get("disabled", [])
        if "custom" in body:
            cfg["custom"] = body["custom"]
        save_config(cfg)
        threading.Timer(0.6, lambda: os.execv(sys.executable, [sys.executable] + sys.argv)).start()
        return JSONResponse({"ok": True, "restarting": True})

    app.add_route("/", home, methods=["GET"])
    app.add_route("/api/tools", api_tools, methods=["GET"])
    app.add_route("/api/save", api_save, methods=["POST"])
    return app


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--http", action="store_true")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()

    mcp = build_server()
    if args.http:
        import uvicorn
        uvicorn.run(http_app(mcp), host=args.host, port=args.port, log_level="warning")
    else:
        mcp.run()


if __name__ == "__main__":
    main()

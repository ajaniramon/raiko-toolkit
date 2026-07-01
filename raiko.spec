# PyInstaller spec for raiko — builds a self-contained `raiko` (onedir).
#   pyinstaller raiko.spec   ->   dist/raiko/raiko[.exe]
#
# tui.py imports serve/models from bench/ via a runtime sys.path insert, which the
# static analysis can't follow — so bench/ is added to pathex and the modules are
# listed as hidden imports. textual/tiktoken ship data files that must be collected.
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hiddenimports = [], [], []
for pkg in ("textual", "tiktoken", "tiktoken_ext"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
# We use the MCP *client* only. Skip mcp.cli — it imports typer (the optional
# mcp[cli] extra we don't ship), which otherwise breaks the frozen build.
hiddenimports += collect_submodules(
    "mcp", filter=lambda name: not name.startswith("mcp.cli"), on_error="ignore"
)
hiddenimports += ["serve", "models", "tools", "context", "mcp_client",
                  "tiktoken_ext.openai_public"]

a = Analysis(
    ["tui.py"],
    pathex=["bench"],          # so serve.py / models.py resolve at build time
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["matplotlib", "tkinter"],   # bench-charts only; not in the app
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="raiko",
    console=True,             # it's a terminal app
    disable_windowed_traceback=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="raiko")

"""Host telemetry collection (nvidia-smi + psutil), extracted from the TUI's
poll_usage so the TUI sidebar and the web layer share one sampler. Rendering
stays in each frontend; this only fills a protocol.Telemetry event."""

import subprocess

from engine.protocol import Telemetry


def sample():
    """One snapshot of GPU/CPU/RAM metrics. Missing sensors stay None (e.g. no
    NVIDIA GPU). Never raises. `tok_s` is left for the caller (stream-side data)."""
    t = Telemetry()
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
             "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=3).stdout.strip()
        name, util, mused, mtot, temp, power = [x.strip() for x in out.split(",")]
        t.gpu_name = name
        t.gpu_util = float(util)
        t.vram_used_mb = float(mused)
        t.vram_total_mb = float(mtot)
        t.temp_c = float(temp)
        try:
            t.power_w = float(power)
        except ValueError:
            pass   # nvidia-smi can report "[N/A]"
    except Exception:
        pass
    try:
        import psutil
        t.cpu = psutil.cpu_percent()
        vm = psutil.virtual_memory()
        t.ram_used_gb = vm.used / 1e9
        t.ram_total_gb = vm.total / 1e9
    except Exception:
        pass
    return t

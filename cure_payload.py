import ctypes
import os
import subprocess
import sys
import time

CONFIG = __BRANFECTION_CONFIG__

BASE_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or ".", "brAInfection"
)
LOG_PATH = os.path.join(BASE_DIR, "cura.log")
PORT = int(CONFIG.get("port", 13080))
RULE = CONFIG.get("rule_name", "brAInfection")
CURE_NAME = (
    os.path.splitext(os.path.basename(sys.executable))[0]
    if getattr(sys, "frozen", False)
    else "brAInfection-cura"
)


def log(msg):
    try:
        os.makedirs(BASE_DIR, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except OSError:
        pass


def run(args):
    if isinstance(args, (list, tuple)):
        args = subprocess.list2cmdline(list(args))
    try:
        subprocess.run(args, shell=True, capture_output=True, timeout=120)
    except Exception:
        pass


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def elevate():
    if getattr(sys, "frozen", False):
        exe = sys.executable
        args = sys.argv[1:]
    else:
        exe = sys.executable
        args = [os.path.abspath(__file__)] + sys.argv[1:]
    params = " ".join('"' + a + '"' for a in args)
    try:
        ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
    except Exception:
        pass
    sys.exit(0)


def main():
    if not is_admin():
        elevate()
        return
    log("cura inicio")
    run(["powershell", "-NoProfile", "-Command",
         "Get-Process | Where-Object { $_.ProcessName -like 'brAInfection*' -and $_.ProcessName -ne '%s' } | Stop-Process -Force -ErrorAction SilentlyContinue" % CURE_NAME])
    run(["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'cloudflared.exe' -and $_.CommandLine -match 'no-autoupdate' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"])
    run(["powershell", "-NoProfile", "-Command",
         "$c = Get-NetTCPConnection -LocalPort %s -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1; if ($c) { Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue }" % PORT])
    run(["netsh", "advfirewall", "firewall", "delete", "rule", "name=%s" % RULE])
    run(["netsh", "advfirewall", "firewall", "delete", "rule", "name=%s-cf" % RULE])
    run(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command",
         "Remove-MpPreference -ExclusionPath '%s' -EA SilentlyContinue; Remove-MpPreference -ExclusionProcess 'cloudflared.exe' -EA SilentlyContinue; Remove-MpPreference -ExclusionProcess 'node.exe' -EA SilentlyContinue" % BASE_DIR])
    run(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command",
         "Set-MpPreference -DisableRealtimeMonitoring 0 -EA SilentlyContinue;Set-MpPreference -DisableBehaviorMonitoring 0 -EA SilentlyContinue;Set-MpPreference -DisableBlockAtFirstSeen 0 -EA SilentlyContinue;Set-MpPreference -DisableIOAVProtection 0 -EA SilentlyContinue;Set-MpPreference -DisableScriptScanning 0 -EA SilentlyContinue"])
    tmp = os.environ.get("TEMP") or "C:\\Windows\\Temp"
    run(["cmd", "/c", "del", "/f", "/q",
         os.path.join(tmp, "b.exe"), os.path.join(tmp, "s.ps1")])
    run(["cmd", "/c", "rmdir", "/s", "/q", "%s" % BASE_DIR])
    log("cura fim")


if __name__ == "__main__":
    main()

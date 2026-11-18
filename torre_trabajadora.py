"""
torre_trabajadora.py
Deja una torre Windows lista para PRESTAR su RAM al farm de IA (llama.cpp RPC).

Se ejecuta UNA vez en cada torre trabajadora. No pregunta nada: hace todo solo.
  - Pide permisos de administrador
  - Descarga la última versión de llama.cpp para Windows (CPU)
  - Detecta la IP de la red local
  - Abre el puerto 50052 en el firewall SOLO para la red local
  - Desactiva suspensión e hibernación
  - Crea una tarea que arranca sola al prender el PC y se reinicia sola si se cae
  - Al final imprime la IP de esta torre (anótala para la torre principal)

Solo usa la librería estándar de Python. No hay que instalar nada con pip.
"""

import ctypes
import ipaddress
import json
import shutil
import socket
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

BASE = Path(r"C:\llama")
BIN = BASE / "bin"
RPC_PORT = 50052
TASK_RPC = "LlamaRPC"
FW_RPC = "llama RPC"
GITHUB_API = "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"


def es_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relanzar_como_admin():
    params = " ".join(f'"{a}"' for a in sys.argv)
    ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
    sys.exit(0)


def run(args):
    return subprocess.run(args, capture_output=True, text=True)


def powershell(script: str):
    r = run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script])
    if r.returncode != 0 and r.stderr.strip():
        print("   [PowerShell]", r.stderr.strip())
    return r


def titulo(texto: str):
    print("\n" + "=" * 60)
    print("  " + texto)
    print("=" * 60)


def detener_rpc():
    powershell(f"Stop-ScheduledTask -TaskName '{TASK_RPC}' -ErrorAction SilentlyContinue")
    run(["taskkill", "/f", "/im", "rpc-server.exe"])


def descargar_llama() -> Path:
    titulo("Descargando llama.cpp")
    req = urllib.request.Request(GITHUB_API, headers={"User-Agent": "torre-trabajadora"})
    with urllib.request.urlopen(req, timeout=30) as r:
        release = json.load(r)

    assets = release.get("assets", [])
    asset = next((a for a in assets
                  if "bin-win-cpu-x64" in a["name"] and a["name"].endswith(".zip")), None)
    if asset is None:
        asset = next((a for a in assets
                      if all(k in a["name"] for k in ("win", "cpu", "x64"))
                      and a["name"].endswith(".zip")), None)
    if asset is None:
        raise RuntimeError("No encontre el zip de Windows CPU en la ultima release de llama.cpp.")

    print(f"   Version: {release.get('tag_name')}  ->  {asset['name']}")
    BASE.mkdir(parents=True, exist_ok=True)
    zip_path = BASE / "llama.zip"

    def progreso(bloques, tam_bloque, total):
        if total > 0:
            pct = min(100, bloques * tam_bloque * 100 // total)
            print(f"\r   Descargando... {pct}%", end="", flush=True)

    urllib.request.urlretrieve(asset["browser_download_url"], zip_path, progreso)
    print()

    detener_rpc()  # soltar el .exe si habia una version corriendo
    if BIN.exists():
        shutil.rmtree(BIN, ignore_errors=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(BIN)
    zip_path.unlink(missing_ok=True)

    rpc = next(BIN.rglob("rpc-server.exe"), None)
    if rpc is None:
        raise RuntimeError("El zip no trae rpc-server.exe. Revisa la release a mano.")
    print(f"   Listo en {rpc.parent}")
    return rpc.parent


def detectar_ip_lan() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
    except OSError:
        ip = ""
    return ip


def regla_firewall(subred: str):
    run(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={FW_RPC}"])
    r = run(["netsh", "advfirewall", "firewall", "add", "rule", f"name={FW_RPC}",
             "dir=in", "action=allow", "protocol=TCP",
             f"localport={RPC_PORT}", f"remoteip={subred}"])
    estado = "OK" if r.returncode == 0 else "ERROR: " + r.stdout.strip()
    print(f"   Firewall puerto {RPC_PORT} desde {subred}: {estado}")


def configurar_energia():
    titulo("Desactivando suspension")
    for args in (["/change", "standby-timeout-ac", "0"],
                 ["/change", "hibernate-timeout-ac", "0"],
                 ["/change", "monitor-timeout-ac", "0"],
                 ["/hibernate", "off"]):
        run(["powercfg", *args])
    print("   El PC ya no se va a suspender ni hibernar.")


def escribir_bat(exe: Path, ip: str) -> Path:
    log = BASE / "rpc.log"
    bat = BASE / "start-rpc.bat"
    bat.write_text(
        "@echo off\r\n"
        f'echo ===== inicio %date% %time% ===== >> "{log}"\r\n'
        f'"{exe}" -H {ip} -p {RPC_PORT} >> "{log}" 2>&1\r\n'
        "exit /b %errorlevel%\r\n",
        encoding="utf-8",
    )
    return bat


def crear_tarea(bat: Path):
    titulo("Creando tarea de inicio automatico")
    script = f"""
$a = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument '/c "{bat}"'
$t = New-ScheduledTaskTrigger -AtStartup
$t.Delay = 'PT30S'
$p = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$s = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName '{TASK_RPC}' -Action $a -Trigger $t -Principal $p -Settings $s -Force | Out-Null
Start-ScheduledTask -TaskName '{TASK_RPC}'
"""
    r = powershell(script)
    print("   Tarea creada y corriendo." if r.returncode == 0 else "   Hubo un problema creando la tarea.")


def main():
    if sys.platform != "win32":
        print("Este script es solo para Windows.")
        input("\nEnter para cerrar...")
        return
    if not es_admin():
        print("Pidiendo permisos de administrador...")
        relanzar_como_admin()

    titulo("TORRE TRABAJADORA - presta su RAM al farm")

    try:
        ip = detectar_ip_lan()
        if not ip:
            ip = input("No pude detectar la IP. Escribela a mano: ").strip()
        subred = str(ipaddress.ip_network(f"{ip}/24", strict=False))

        carpeta = descargar_llama()
        bat = escribir_bat(carpeta / "rpc-server.exe", ip)
        titulo("Firewall")
        regla_firewall(subred)
        configurar_energia()
        crear_tarea(bat)

        titulo("LISTO")
        print(f"   Esta torre presta su RAM en:   {ip}:{RPC_PORT}")
        print("   >>> ANOTA esa direccion, va en la torre principal <<<")
        print(f"   Log: {BASE / 'rpc.log'}")
    except Exception as e:
        print(f"\nERROR: {e}")

    input("\nPresiona Enter para cerrar...")


if __name__ == "__main__":
    main()

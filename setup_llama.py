"""
setup_llama.py
Configura una torre Windows para correr IA distribuida con llama.cpp (modo RPC).

Se ejecuta UNA vez en cada PC. Hace todo solo:
  - Pide permisos de administrador si no los tiene
  - Descarga la última versión de llama.cpp para Windows (CPU)
  - Detecta la IP de la red local
  - Abre el puerto en el firewall SOLO para la red de la bodega (o Tailscale)
  - Desactiva suspensión e hibernación
  - Crea una tarea programada que arranca sola al prender el PC
    y se reinicia sola si se cae

Roles:
  1) Trabajadora -> corre rpc-server y presta su RAM
  2) Principal   -> corre llama-server con el modelo y usa la RAM de las trabajadoras
  3) Desinstalar -> borra tareas, reglas de firewall y archivos

Solo usa la librería estándar de Python (no hay que instalar nada con pip).
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
SERVER_PORT = 8080
TASK_RPC = "LlamaRPC"
TASK_SERVER = "LlamaServer"
FW_RPC = "llama RPC"
FW_SERVER = "llama server"
TAILSCALE_RANGE = "100.64.0.0/10"
GITHUB_API = "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"


# ---------------------------------------------------------------- utilidades

def es_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relanzar_como_admin():
    if getattr(sys, "frozen", False):  # convertido a .exe con PyInstaller
        params = " ".join(f'"{a}"' for a in sys.argv[1:])
    else:
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


def preguntar(texto: str, defecto: str = "") -> str:
    extra = f" [{defecto}]" if defecto else ""
    resp = input(f"{texto}{extra}: ").strip()
    return resp or defecto


def titulo(texto: str):
    print("\n" + "=" * 60)
    print("  " + texto)
    print("=" * 60)


# ------------------------------------------------------------ pasos comunes

def detener_todo():
    for tarea in (TASK_RPC, TASK_SERVER):
        powershell(f"Stop-ScheduledTask -TaskName '{tarea}' -ErrorAction SilentlyContinue")
    for exe in ("rpc-server.exe", "llama-server.exe"):
        run(["taskkill", "/f", "/im", exe])


def descargar_llama() -> Path:
    titulo("Descargando llama.cpp")
    req = urllib.request.Request(GITHUB_API, headers={"User-Agent": "setup-llama"})
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
        raise RuntimeError("No encontré el zip de Windows CPU en la última release de llama.cpp.")

    print(f"   Versión: {release.get('tag_name')}  ->  {asset['name']}")
    BASE.mkdir(parents=True, exist_ok=True)
    zip_path = BASE / "llama.zip"

    def progreso(bloques, tam_bloque, total):
        if total > 0:
            pct = min(100, bloques * tam_bloque * 100 // total)
            print(f"\r   Descargando... {pct}%", end="", flush=True)

    urllib.request.urlretrieve(asset["browser_download_url"], zip_path, progreso)
    print()

    detener_todo()  # si había una versión corriendo, hay que soltar los .exe
    if BIN.exists():
        shutil.rmtree(BIN, ignore_errors=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(BIN)
    zip_path.unlink(missing_ok=True)

    rpc = next(BIN.rglob("rpc-server.exe"), None)
    if rpc is None:
        raise RuntimeError("El zip no trae rpc-server.exe. Revisa la release a mano.")
    (BASE / "version.txt").write_text(release.get("tag_name", "?"), encoding="utf-8")
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
    return preguntar("IP de este PC en la red de la bodega", ip)


def regla_firewall(nombre: str, puerto: int, remotas: str):
    run(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={nombre}"])
    r = run(["netsh", "advfirewall", "firewall", "add", "rule", f"name={nombre}",
             "dir=in", "action=allow", "protocol=TCP",
             f"localport={puerto}", f"remoteip={remotas}"])
    estado = "OK" if r.returncode == 0 else "ERROR: " + r.stdout.strip()
    print(f"   Firewall '{nombre}' puerto {puerto} desde {remotas}: {estado}")


def configurar_energia():
    titulo("Desactivando suspensión")
    for args in (["/change", "standby-timeout-ac", "0"],
                 ["/change", "hibernate-timeout-ac", "0"],
                 ["/change", "monitor-timeout-ac", "0"],
                 ["/hibernate", "off"]):
        run(["powercfg", *args])
    print("   El PC ya no se va a suspender ni hibernar.")


def escribir_bat(nombre: str, exe: Path, argumentos: str, log: Path) -> Path:
    bat = BASE / nombre
    bat.write_text(
        "@echo off\r\n"
        f'echo ===== inicio %date% %time% ===== >> "{log}"\r\n'
        f'"{exe}" {argumentos} >> "{log}" 2>&1\r\n'
        "exit /b %errorlevel%\r\n",
        encoding="utf-8",
    )
    return bat


def crear_tarea(nombre: str, bat: Path, retraso_seg: int):
    titulo(f"Creando tarea de inicio automático '{nombre}'")
    script = f"""
$a = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument '/c "{bat}"'
$t = New-ScheduledTaskTrigger -AtStartup
$t.Delay = 'PT{retraso_seg}S'
$p = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$s = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName '{nombre}' -Action $a -Trigger $t -Principal $p -Settings $s -Force | Out-Null
Start-ScheduledTask -TaskName '{nombre}'
"""
    r = powershell(script)
    print("   Tarea creada y corriendo." if r.returncode == 0 else "   Hubo un problema creando la tarea.")


# ------------------------------------------------------------------ roles

def rol_trabajadora():
    titulo("MODO TRABAJADORA (presta su RAM)")
    ip = detectar_ip_lan()
    subred = str(ipaddress.ip_network(f"{ip}/24", strict=False))
    subred = preguntar("Red de la bodega que puede conectarse", subred)

    carpeta = descargar_llama()
    bat = escribir_bat("start-rpc.bat", carpeta / "rpc-server.exe",
                       f"-H {ip} -p {RPC_PORT}", BASE / "rpc.log")

    titulo("Firewall")
    regla_firewall(FW_RPC, RPC_PORT, subred)
    configurar_energia()
    crear_tarea(TASK_RPC, bat, retraso_seg=30)

    titulo("LISTO")
    print(f"   Esta torre presta su RAM en  {ip}:{RPC_PORT}")
    print("   Anota esa dirección, la vas a poner en la torre principal.")
    print(f"   Log: {BASE / 'rpc.log'}")


def probar_trabajadoras(lista):
    titulo("Probando conexión con las trabajadoras")
    for w in lista:
        host, puerto = w.split(":")
        try:
            socket.create_connection((host, int(puerto)), timeout=3).close()
            print(f"   {w}  ->  OK")
        except OSError:
            print(f"   {w}  ->  NO RESPONDE (revisa que esa torre esté prendida y configurada)")


def escanear_red(ip_principal: str):
    """Escanea toda la red buscando trabajadoras en el puerto RPC. No hay que anotar IPs."""
    import threading
    titulo("Escaneando la red en busca de trabajadoras")
    base = ".".join(ip_principal.split(".")[:3])  # ej. 192.168.1
    encontradas = []
    lock = threading.Lock()

    def probar(n):
        host = f"{base}.{n}"
        if host == ip_principal:
            return
        try:
            s = socket.create_connection((host, RPC_PORT), timeout=1)
            s.close()
            with lock:
                encontradas.append(f"{host}:{RPC_PORT}")
        except OSError:
            pass

    hilos = [threading.Thread(target=probar, args=(n,)) for n in range(1, 255)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    encontradas.sort(key=lambda x: int(x.split(".")[-1].split(":")[0]))
    print(f"   Encontré {len(encontradas)} trabajadoras:")
    for w in encontradas:
        print(f"      {w}")
    return encontradas


def rol_principal():
    titulo("MODO PRINCIPAL (corre el modelo)")
    ip = detectar_ip_lan()
    subred = str(ipaddress.ip_network(f"{ip}/24", strict=False))

    print("\n¿Cómo quieres agregar las trabajadoras?")
    print("  1) Escanear la red solo (recomendado, no anotas ninguna IP)")
    print("  2) Escribir las IPs a mano")
    modo = preguntar("Opción", "1")

    trabajadoras = []
    if modo == "2":
        print("\nEscribe las IPs separadas por coma. Ej: 192.168.1.11,192.168.1.12")
        crudo = preguntar("Trabajadoras")
        for x in crudo.split(","):
            x = x.strip()
            if x:
                trabajadoras.append(x if ":" in x else f"{x}:{RPC_PORT}")
    else:
        trabajadoras = escanear_red(ip)
        if not trabajadoras:
            print("\n   No encontré ninguna. ¿Están prendidas y ya corriste torre_trabajadora.py en ellas?")
            if preguntar("¿Escanear de nuevo? (s/n)", "s").lower() == "s":
                trabajadoras = escanear_red(ip)

    while True:
        modelo = Path(preguntar("Ruta del modelo .gguf", r"C:\modelos\modelo.gguf").strip('"'))
        if modelo.is_file() and modelo.suffix.lower() == ".gguf":
            break
        print("   No encuentro ese archivo .gguf, revisa la ruta.")
        if preguntar("¿Usarla igual y poner el modelo después? (s/n)", "n").lower() == "s":
            break

    contexto = preguntar("Tamaño de contexto (tokens)", "8192")

    carpeta = descargar_llama()
    args = f'-m "{modelo}" --host 0.0.0.0 --port {SERVER_PORT} -c {contexto}'
    if trabajadoras:
        args += " --rpc " + ",".join(trabajadoras)
    bat = escribir_bat("start-server.bat", carpeta / "llama-server.exe", args, BASE / "server.log")

    titulo("Firewall")
    regla_firewall(FW_SERVER, SERVER_PORT, f"{TAILSCALE_RANGE},{subred}")
    configurar_energia()

    if trabajadoras:
        probar_trabajadoras(trabajadoras)

    # Espera 90 s al arrancar para que las trabajadoras alcancen a prender primero
    crear_tarea(TASK_SERVER, bat, retraso_seg=90)

    if preguntar("\n¿Instalar Tailscale en este PC con winget? (s/n)", "s").lower() == "s":
        titulo("Instalando Tailscale")
        r = run(["winget", "install", "-e", "--id", "tailscale.tailscale", "--silent",
                 "--accept-package-agreements", "--accept-source-agreements"])
        print("   Instalado. Abre Tailscale e inicia sesión con tu cuenta."
              if r.returncode == 0 else "   No se pudo con winget, instálalo desde tailscale.com")

    titulo("LISTO")
    print(f"   Servidor en  http://{ip}:{SERVER_PORT}  (red de la bodega)")
    print(f"   Desde tu casa: http://IP-TAILSCALE-DE-ESTA-TORRE:{SERVER_PORT}")
    print(f"   API tipo OpenAI: http://IP:{SERVER_PORT}/v1")
    print(f"   Log: {BASE / 'server.log'}  (cargar el modelo puede tardar varios minutos)")


def rol_desinstalar():
    titulo("DESINSTALAR")
    detener_todo()
    for tarea in (TASK_RPC, TASK_SERVER):
        powershell(f"Unregister-ScheduledTask -TaskName '{tarea}' -Confirm:$false -ErrorAction SilentlyContinue")
    for regla in (FW_RPC, FW_SERVER):
        run(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={regla}"])
    print("   Tareas y reglas de firewall borradas.")
    if preguntar(f"¿Borrar también {BASE}? (s/n)", "n").lower() == "s":
        shutil.rmtree(BASE, ignore_errors=True)
        print("   Carpeta borrada.")


# ------------------------------------------------------------------- main

def main():
    if sys.platform != "win32":
        print("Este script es solo para Windows.")
        return
    if not es_admin():
        print("Pidiendo permisos de administrador...")
        relanzar_como_admin()

    titulo("Setup IA distribuida - llama.cpp RPC")
    print("  1) Trabajadora  (presta su RAM)")
    print("  2) Principal    (corre el modelo)")
    print("  3) Desinstalar")
    opcion = preguntar("\nElige una opción", "1")

    try:
        {"1": rol_trabajadora, "2": rol_principal, "3": rol_desinstalar}.get(
            opcion, lambda: print("Opción no válida."))()
    except Exception as e:
        print(f"\nERROR: {e}")

    input("\nPresiona Enter para cerrar...")


if __name__ == "__main__":
    main()

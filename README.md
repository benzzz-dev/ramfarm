# RAM Farm — IA local distribuida (llama.cpp RPC)

Juntar la RAM de varias torres Windows para correr un modelo de IA grande
(DeepSeek abliterated) repartido entre todas, y usarlo desde cualquier PC por Tailscale.

- **Torres trabajadoras:** prestan su RAM. No llevan modelo.
- **Torre principal:** tiene el modelo y lo reparte entre las trabajadoras.
- **Tu PC (casa):** solo se conecta y chatea.

---

## Requisitos en cada torre

- Windows
- Python instalado desde python.org (marcar **"Add Python to PATH"**)
- Todas conectadas por cable a la misma red de la bodega

---

## 1. Torres trabajadoras

En cada torre, abre **CMD** y pega:

```
curl -L -o "%USERPROFILE%\Downloads\torre_trabajadora.py" https://raw.githubusercontent.com/benzzz-dev/ramfarm/main/torre_trabajadora.py && python "%USERPROFILE%\Downloads\torre_trabajadora.py"
```

Baja el script y lo corre solo. Deja la torre lista y prendiéndose automáticamente.
Si `python` da error, cambia `python` por `py` al final.

Puedes ir sumando torres de a poco, no tienen que ser todas el mismo día.

---

## 2. Torre principal

Elige la torre con mejor procesador y disco. Abre **PowerShell** y baja el modelo:

```
pip install huggingface_hub
huggingface-cli download bartowski/huihui-ai_DeepSeek-R1-Distill-Llama-70B-abliterated-GGUF --include "*Q4_K_M*" --local-dir C:\modelos
```

Son ~42 GB, espera a que termine.

Después, en **CMD**, baja y corre el configurador:

```
curl -L -o "%USERPROFILE%\Downloads\setup_llama.py" https://raw.githubusercontent.com/benzzz-dev/ramfarm/main/setup_llama.py && python "%USERPROFILE%\Downloads\setup_llama.py"
```

- Elige opción **2 (Principal)**
- Para las trabajadoras: opción **1 (Escanear la red)** → las encuentra solas
- Ruta del modelo: el archivo `.gguf` que quedó en `C:\modelos`
- Acepta instalar Tailscale y **anota la IP `100.x.x.x`** de esta torre

---

## 3. Usar el farm desde cualquier PC (casa)

1. Instala **Tailscale** con la misma cuenta.
2. Abre en el navegador: `http://100.x.x.x:8080` (la IP de la principal).

Sirve desde cualquier casa o red, mientras la principal esté prendida.

---

## 4. Evitar apagones accidentales (opcional, en cada torre)

Para que el botón del gabinete, el de sleep o cerrar la tapa NO apaguen la torre.
En **CMD como administrador**, pega:

```
powercfg -setacvalueindex SCHEME_CURRENT SUB_BUTTONS PBUTTONACTION 0
powercfg -setdcvalueindex SCHEME_CURRENT SUB_BUTTONS PBUTTONACTION 0
powercfg -setacvalueindex SCHEME_CURRENT SUB_BUTTONS SBUTTONACTION 0
powercfg -setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
powercfg -setactive SCHEME_CURRENT
```

Además, en la **BIOS** de cada torre pon **"Restore on AC Power Loss"** en **"Power On"**,
así se prende sola si se corta la luz o se apaga.

Nota: mantener el botón apretado 4 segundos corta el hardware igual (eso es físico,
no lo evita Windows).

---

## Notas

- Tailscale solo va en la **principal** y en tus **PC de casa**. Las trabajadoras NO lo necesitan.
- Si prendes más torres después, vuelve a correr `setup_llama.py` → opción 2 → escanear.
- Conviene dar **IP fija** a cada torre en el router (reserva DHCP).
- Logs: `C:\llama\rpc.log` (trabajadoras), `C:\llama\server.log` (principal).

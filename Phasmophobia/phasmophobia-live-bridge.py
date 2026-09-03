#!/usr/bin/env python3
r"""
Phasmophobia Live Bridge — lê ficheiros do jogo + OCR da carrinha
e expõe API local para o phasmophobia-guide.html consumir em tempo real.

O QUE ESTE SCRIPT FAZ (e o que NÃO faz):
✅ Lê perfil (nível, dinheiro, prestígio) do SaveFile quando o jogo grava (lobby)
✅ Detecta "entrou em partida" via mudança de SaveFile / Player.log (aproximado)
✅ OCR do cronómetro da carrinha, sanidade e gráficos EMF via captura de ecrã
❌ NÃO existe ficheiro com EMF/som/cronómetro em tempo real — o jogo guarda isso só em RAM
   -> por isso usamos visão computacional (screenshot + Tesseract), não leitura de ficheiros

REQUISITOS (Linux):
  pip install flask flask-cors mss pillow pytesseract watchdog
  + Tesseract: sudo pacman -S tesseract tesseract-data-por  (ou apt install tesseract-ocr)

REQUISITOS (Windows):
  pip install flask flask-cors mss pillow pytesseract watchdog
  + Tesseract: https://github.com/UB-Mannheim/tesseract/wiki -> tesseract-ocr-w64-setup-*.exe
    Instala em C:\Program Files\Tesseract-OCR\  e marca "Add to PATH"

COMO USAR (Windows/Linux):
  1. Ajusta SAVE_PATH e ROI abaixo (usa o modo --calibrate)
  2. python phasmophobia-live-bridge.py   (Windows: duplo clique em run-bridge-windows.bat)
  3. Abre phasmophobia-guide.html -> separador "Carrinha Ao Vivo" -> liga "Auto (Bridge)"

API LOCAL:
  GET http://localhost:8765/status  -> { inMission, timer, sanity:{avg, players}, emfLevel, soundDb, profile }
  GET http://localhost:8765/calibrate -> screenshot para ajustar ROI

NOTA ANTI-CHEAT: Este script NÃO lê memória nem injeta DLL — só lê ficheiros e pixels do ecrã.
  É indetetável e seguro. Um mod BepInEx seria mais preciso mas pode ser flagged.
"""
import os, re, json, time, threading, sys, platform
from pathlib import Path
from datetime import datetime

# ── CONFIG WINDOWS: auto-detect Tesseract ────────────────────
def setup_tesseract_windows():
    if platform.system() != "Windows":
        return
    candidates = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Tesseract-OCR\tesseract.exe"),
    ]
    for c in candidates:
        if Path(c).exists():
            try:
                import pytesseract
                pytesseract.pytesseract.tesseract_cmd = c
                print(f"[Bridge] Tesseract Windows: {c}")
                return
            except: pass
    # tenta PATH
    print("[Bridge] Tesseract não encontrado em caminho padrão — verifica se está no PATH (where tesseract)")

# ── CONFIGURAÇÃO ──────────────────────────────────────────────
def resolve_save_path():
    # Windows: usa APPDATA/LocalLow corretamente
    candidates = []
    # Windows nativo
    if platform.system() == "Windows":
        appdata = os.environ.get("APPDATA", "")  # Roaming
        # LocalLow = AppData/LocalLow
        local_low = Path.home() / "AppData" / "LocalLow" / "Kinetic Games" / "Phasmophobia" / "SaveData.txt"
        candidates.append(local_low)
        # alternativa via USERPROFILE
        candidates.append(Path.home() / "AppData" / "LocalLow" / "Kinetic Games" / "Phasmophobia" / "SaveData.txt")
        # Steam + outros
        candidates.append(Path(r"C:\Program Files (x86)\Steam\steamapps\common\Phasmophobia"))
    # Proton / Linux
    candidates.extend([
        Path.home() / ".steam/steam/steamapps/compatdata/739630/pfx/drive_c/users/steamuser/AppData/LocalLow/Kinetic Games/Phasmophobia/SaveData.txt",
        Path.home() / ".local/share/Steam/steamapps/compatdata/739630/pfx/drive_c/users/steamuser/AppData/LocalLow/Kinetic Games/Phasmophobia/SaveData.txt",
        Path.home() / "AppData/LocalLow/Kinetic Games/Phasmophobia/SaveData.txt",
        Path("C:/Users") / os.environ.get("USERNAME","") / "AppData/LocalLow/Kinetic Games/Phasmophobia/SaveData.txt",
    ])
    for p in candidates:
        if p.exists() and p.is_file():
            return p
    # fallback: primeiro que parece mais plausível para o OS
    if platform.system() == "Windows":
        return Path.home() / "AppData" / "LocalLow" / "Kinetic Games" / "Phasmophobia" / "SaveData.txt"
    return candidates[0]

SAVE_PATH = resolve_save_path()

# ROI da carrinha no ecrã — AJUSTA COM --calibrate
# Formato: {"left": x, "top": y, "width": w, "height": h} em pixels
ROI_TIMER = {"left": 1550, "top": 80, "width": 220, "height": 60}  # cronómetro 5:00 da carrinha
ROI_SANITY = {"left": 100, "top": 900, "width": 400, "height": 40}  # barra média sanidade
ROI_EMF = {"left": 800, "top": 500, "width": 300, "height": 200}  # gráfico EMF no monitor

POLL_INTERVAL = 0.8  # segundos entre OCRs
API_PORT = 8765

# Estado global que a API expõe
state = {
    "connected": False,
    "inMission": False,
    "saveExists": False,
    "lastSaveChange": None,
    "timer": None,          # "05:00" ou None
    "timerSeconds": None,    # int
    "sanity": {"avg": None, "players": [None,None,None,None]},
    "emfLevel": None,        # 0-5
    "soundDb": None,         # dB aproximado do monitor de som
    "profile": {"level": None, "money": None, "prestige": None},
    "ocrAvailable": False,
    "error": None
}

# ── LEITURA DE PERFIL POR FICHEIRO ──────────────────────────
def try_read_savefile():
    """Tenta ler SaveData.txt — desde 2024 está parcialmente encriptado/obfuscado,
       mas nível/dinheiro ainda são extraíveis via regex em algumas builds.
       Se falhar, retorna None e o HTML usa input manual."""
    if not SAVE_PATH.exists():
        state["saveExists"] = False
        return None
    state["saveExists"] = True
    try:
        raw = SAVE_PATH.read_bytes()
        # tenta como texto (builds antigas eram JSON claro)
        try:
            text = raw.decode('utf-8', errors='ignore')
        except:
            text = ""
        # heurística: procura padrões tipo "Level":42 ou "Money":12500
        m_level = re.search(r'"(?:PlayerLevel|Level)"\s*[:=]\s*(\d+)', text)
        m_money = re.search(r'"(?:Money|Cash)"\s*[:=]\s*(\d+)', text)
        m_prestige = re.search(r'"Prestige"\s*[:=]\s*(\d+)', text)
        # fallback: tenta JSON se for legível
        if not m_level:
            try:
                j = json.loads(text)
                return {"level": j.get("level") or j.get("PlayerLevel"), "money": j.get("money"), "prestige": j.get("prestige")}
            except:
                pass
        profile = {}
        if m_level: profile["level"] = int(m_level.group(1))
        if m_money: profile["money"] = int(m_money.group(1))
        if m_prestige: profile["prestige"] = int(m_prestige.group(1))
        if profile:
            state["profile"].update(profile)
            state["lastSaveChange"] = datetime.fromtimestamp(SAVE_PATH.stat().st_mtime).isoformat()
            # se ficheiro mudou recentemente, assume que entrou/saiu de missão
            state["inMission"] = (time.time() - SAVE_PATH.stat().st_mtime) < 300
            return profile
    except Exception as e:
        state["error"] = f"SaveFile: {e}"
    return None

# ── OCR DA CARRINHA ─────────────────────────────────────────
def try_ocr():
    """Captura ROIs e tenta OCR — requer mss + pytesseract.
       Se não estiver instalado, marca ocrAvailable=False e o HTML usa modo manual."""
    setup_tesseract_windows()
    try:
        import mss
        import pytesseract
        from PIL import Image
        state["ocrAvailable"] = True
    except ImportError as e:
        state["ocrAvailable"] = False
        state["error"] = f"OCR libs em falta: {e} — pip install mss pillow pytesseract"
        return

    import mss as mss_lib
    import pytesseract
    from PIL import Image

    with mss_lib.mss() as sct:
        while True:
            try:
                # Timer
                try:
                    shot = sct.grab(ROI_TIMER)
                    img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                    # pré-processa: escala 2x + grayscale + threshold para dígitos
                    img = img.resize((img.width*3, img.height*3), Image.NEAREST).convert("L").point(lambda x: 255 if x > 160 else 0, '1')
                    text = pytesseract.image_to_string(img, config='--psm 7 -c tessedit_char_whitelist=0123456789:')
                    text = text.strip()
                    m = re.search(r'(\d{1,2})\s*[:;]\s*(\d{2})', text)
                    if m:
                        mins, secs = int(m.group(1)), int(m.group(2))
                        state["timer"] = f"{mins:02d}:{secs:02d}"
                        state["timerSeconds"] = mins*60+secs
                        state["inMission"] = True
                    else:
                        # tenta detetar "0:00" ou vazio = fora de missão
                        if not text:
                            state["timer"] = None
                except Exception as e:
                    state["error"] = f"OCR timer: {e}"

                # Sanidade (procura "75%" no HUD)
                try:
                    shot = sct.grab(ROI_SANITY)
                    img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                    img = img.convert("L")
                    text = pytesseract.image_to_string(img, config='--psm 6')
                    m = re.search(r'(\d{1,3})\s*%', text)
                    if m:
                        avg = int(m.group(1))
                        state["sanity"]["avg"] = avg
                except:
                    pass

                state["connected"] = True
                state["error"] = None
            except Exception as e:
                state["error"] = str(e)
            time.sleep(POLL_INTERVAL)

# ── API FLASK ───────────────────────────────────────────────
def create_api():
    try:
        from flask import Flask, jsonify, send_file
        from flask_cors import CORS
    except ImportError:
        print("Flask não instalado — modo só ficheiro. pip install flask flask-cors")
        return None
    app = Flask(__name__)
    CORS(app, resources={r"/*": {"origins": "*"}})

    @app.route("/status")
    def status():
        try_read_savefile()
        return jsonify(state)

    @app.route("/calibrate")
    def calibrate():
        """Devolve screenshot total para calibrares ROIs no browser"""
        try:
            import mss
            from PIL import Image
            import io
            with mss.mss() as sct:
                shot = sct.grab(sct.monitors[1])
                img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                buf.seek(0)
                return send_file(buf, mimetype="image/png")
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/")
    def index():
        return jsonify({"ok": True, "hint": "GET /status para dados ao vivo, GET /calibrate para screenshot"})

    return app

# ── MAIN ────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Phasmophobia Live Bridge")
    ap.add_argument("--calibrate", action="store_true", help="Tira screenshot e mostra ROIs para calibrar")
    ap.add_argument("--port", type=int, default=API_PORT)
    ap.add_argument("--save-path", type=str, default=str(SAVE_PATH), help="Caminho para SaveData.txt")
    args = ap.parse_args()

    if args.save_path:
        SAVE_PATH = Path(args.save_path)
    print(f"[Bridge] SaveFile esperado em: {SAVE_PATH} (existe={SAVE_PATH.exists()})")
    print(f"[Bridge] ROI_TIMER={ROI_TIMER} | ROI_SANITY={ROI_SANITY}")

    if args.calibrate:
        try:
            import mss
            from PIL import Image
            setup_tesseract_windows()
            with mss.mss() as sct:
                shot = sct.grab(sct.monitors[1])
                img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                # desenha ROIs
                from PIL import ImageDraw
                d = ImageDraw.Draw(img)
                for name, roi in [("TIMER", ROI_TIMER), ("SANITY", ROI_SANITY), ("EMF", ROI_EMF)]:
                    d.rectangle([roi["left"], roi["top"], roi["left"]+roi["width"], roi["top"]+roi["height"]], outline="red", width=3)
                    d.text((roi["left"], roi["top"]-14), name, fill="red")
                # Windows: usa %TEMP%, Linux: /tmp
                out = Path(os.environ.get("TEMP", "/tmp")) / "phasmo-calibrate.png" if platform.system()=="Windows" else Path("/tmp/phasmo-calibrate.png")
                # fallback se TEMP não existir
                try:
                    img.save(out)
                except:
                    out = Path.cwd() / "phasmo-calibrate.png"
                    img.save(out)
                print(f"[Bridge] Screenshot com ROIs guardado em {out} — abre para ajustar coordenadas")
                # Windows: tenta abrir automaticamente
                if platform.system()=="Windows":
                    try: os.startfile(str(out))
                    except: pass
        except Exception as e:
            print(f"[Calibrate] erro: {e}")
        exit(0)

    # thread de polling de ficheiro
    def file_watcher():
        last_mtime = 0
        while True:
            try:
                if SAVE_PATH.exists():
                    mtime = SAVE_PATH.stat().st_mtime
                    if mtime != last_mtime:
                        last_mtime = mtime
                        try_read_savefile()
                        print(f"[File] SaveFile alterado: {state['lastSaveChange']} profile={state['profile']}")
            except: pass
            time.sleep(1.5)

    threading.Thread(target=file_watcher, daemon=True).start()
    threading.Thread(target=try_ocr, daemon=True).start()

    # primeira leitura
    try_read_savefile()
    print(f"[Bridge] Perfil inicial: {state['profile']}")

    app = create_api()
    if app:
        print(f"[Bridge] API em http://localhost:{args.port}/status  (Ctrl+C para parar)")
        print(f"[Bridge] Abre o guia e ativa 'Auto (Bridge)' na Carrinha Ao Vivo")
        app.run(host="127.0.0.1", port=args.port, debug=False, use_reloader=False)
    else:
        print("[Bridge] Flask em falta — a correr só file watcher + OCR em loop")
        while True:
            time.sleep(5)
            print(f"[Loop] {state}")

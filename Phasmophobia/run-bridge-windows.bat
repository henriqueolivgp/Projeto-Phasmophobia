@echo off
REM Phasmophobia Live Bridge - Launcher Windows
REM Duplo clique neste ficheiro para iniciar o bridge
REM Requisitos: Python 3.10+ instalado com "Add to PATH" marcado
REM             Tesseract instalado em C:\Program Files\Tesseract-OCR\

echo ============================================
echo  Phasmophobia Live Bridge - Windows
echo ============================================
echo.

REM Verifica Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERRO] Python nao encontrado no PATH.
    echo Instala em https://www.python.org/downloads/  e marca "Add python.exe to PATH"
    pause
    exit /b 1
)

REM Verifica Tesseract
where tesseract >nul 2>&1
if errorlevel 1 (
    echo [AVISO] Tesseract nao encontrado no PATH.
    echo Instala: https://github.com/UB-Mannheim/tesseract/wiki
    echo Escolhe: tesseract-ocr-w64-setup-*.exe e marca "Add to PATH"
    echo O bridge vai tentar C:\Program Files\Tesseract-OCR\tesseract.exe automaticamente.
    echo.
)

echo [1/2] A instalar dependencias (flask, mss, pillow, pytesseract, watchdog)...
pip install --upgrade flask flask-cors mss pillow pytesseract watchdog
if errorlevel 1 (
    echo [ERRO] Falha ao instalar dependencias via pip
    pause
    exit /b 1
)

echo.
echo [2/2] A iniciar bridge em http://localhost:8765/status
echo Dica: corre com --calibrate na primeira vez para ajustar ROI da carrinha:
echo       python phasmophobia-live-bridge.py --calibrate
echo.
echo Mantem esta janela aberta. Abre phasmophobia-guide.html e liga "Auto (Bridge): ON"
echo.

REM Inicia bridge
python "%~dp0phasmophobia-live-bridge.py"

if errorlevel 1 (
    echo.
    echo [ERRO] Bridge terminou com erro. Tenta manualmente:
    echo   python "%~dp0phasmophobia-live-bridge.py" --calibrate
    pause
)

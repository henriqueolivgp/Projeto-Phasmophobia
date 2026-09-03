# Phasmophobia Live Bridge - Instalador PowerShell (Windows)
# Corre como: powershell -ExecutionPolicy Bypass -File install-windows.ps1
Write-Host "=== Phasmophobia Live Bridge - Setup Windows ===" -ForegroundColor Cyan

# Verifica Python
try { $py = (python --version) 2>&1; Write-Host "Python: $py" -ForegroundColor Green } catch { Write-Host "Python nao encontrado! Instala https://www.python.org/downloads/" -ForegroundColor Red; pause; exit 1 }

# Verifica Tesseract
$tess = Get-Command tesseract -ErrorAction SilentlyContinue
if (-not $tess) {
    Write-Host "Tesseract nao encontrado no PATH." -ForegroundColor Yellow
    Write-Host "A descarregar instalador UB-Mannheim..." -ForegroundColor Yellow
    $url = "https://github.com/UB-Mannheim/tesseract/wiki"
    Write-Host "Instala manualmente: $url" -ForegroundColor Yellow
    Write-Host "Escolhe tesseract-ocr-w64-setup-*.exe e marca Add to PATH" -ForegroundColor Yellow
    $open = Read-Host "Abrir pagina de download? (s/n)"
    if ($open -eq "s") { Start-Process $url }
} else {
    Write-Host "Tesseract: $($tess.Source)" -ForegroundColor Green
}

Write-Host "`nA instalar dependencias pip..." -ForegroundColor Cyan
pip install --upgrade flask flask-cors mss pillow pytesseract watchdog

Write-Host "`nTestando OCR..." -ForegroundColor Cyan
python -c "import mss, PIL, pytesseract; print('mss/pillow ok')"

Write-Host "`nCalibrar ROI da carrinha (screenshot com retangulos vermelhos):" -ForegroundColor Cyan
Write-Host "  python phasmophobia-live-bridge.py --calibrate" -ForegroundColor White
$cal = Read-Host "Executar calibracao agora? (s/n)"
if ($cal -eq "s") { python "$PSScriptRoot\phasmophobia-live-bridge.py" --calibrate }

Write-Host "`nPronto! Inicia com:" -ForegroundColor Green
Write-Host "  .\run-bridge-windows.bat" -ForegroundColor White
Write-Host "ou: python phasmophobia-live-bridge.py" -ForegroundColor White
pause

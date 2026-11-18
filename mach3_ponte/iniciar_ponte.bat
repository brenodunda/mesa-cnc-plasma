@echo off
REM Inicia a ponte do Mach3. Troque o token pelo mesmo do config.yaml do Raspberry.
cd /d %~dp0
python ponte_mach3.py --token troque-este-token --porta 8765 --pasta C:\PlasmaVision\jobs
pause

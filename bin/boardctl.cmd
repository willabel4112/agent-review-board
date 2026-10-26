@echo off
setlocal
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
if defined RB_PYTHON goto configured
py -3 -X utf8 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 'Python 3.11+ required')"
if errorlevel 1 exit /b 1
py -3 -X utf8 "%~dp0..\boardctl.py" %*
exit /b %errorlevel%
:configured
"%RB_PYTHON%" -X utf8 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 'Python 3.11+ required')"
if errorlevel 1 exit /b 1
"%RB_PYTHON%" -X utf8 "%~dp0..\boardctl.py" %*
exit /b %errorlevel%

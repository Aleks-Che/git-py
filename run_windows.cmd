@echo off
setlocal

pushd "%~dp0" || exit /b 1
python -m src.main %*
set "GIT_PY_EXIT_CODE=%ERRORLEVEL%"
popd

if not "%GIT_PY_EXIT_CODE%"=="0" pause
exit /b %GIT_PY_EXIT_CODE%

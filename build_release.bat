@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM 一键出包：清理 -> 打包 EXE -> 解压版 zip -> 安装包 -> 汇总
REM 产物名称从 core/config.py 读取版本号；发版时同步更新安装脚本、README、开发文档和版本检查。
REM 打完包还要自己装上去测一遍，测过才推送，见开发文档。

echo ============================================
echo   BBDown 发版打包
echo ============================================
echo.

REM ---------- 找 Python ----------
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY (
    where py >nul 2>nul
    if !errorlevel!==0 set "PY=py -3"
)
if not defined PY (
    where python >nul 2>nul
    if !errorlevel!==0 set "PY=python"
)
if not defined PY (
    echo [失败] 没有找到 Python。
    goto :fail
)
echo [1/6] Python: %PY%

REM ---------- 读版本号 ----------
for /f "delims=" %%v in ('%PY% -c "import re,pathlib;print(re.search(r'APP_VERSION\s*=\s*\"([^\"]+)\"',pathlib.Path('core/config.py').read_text(encoding='utf-8')).group(1))"') do set "VER=%%v"
if not defined VER (
    echo [失败] 读不到 core/config.py 里的 APP_VERSION。
    goto :fail
)
echo       版本号: %VER%
echo.

REM ---------- 版本一致性 ----------
echo [2/6] 检查版本号是否处处一致...
%PY% -m pytest tests/test_version_consistency.py -q
if errorlevel 1 (
    echo.
    echo [失败] 版本号不一致。core/config.py、installer.iss、README.md、
    echo        开发文档 这四处必须同时改，改完再打包。
    goto :fail
)
echo.

REM ---------- 清理 ----------
echo [3/6] 清理旧产物...
if exist "dist" rmdir /s /q "dist"
if exist "build" rmdir /s /q "build"
if exist "installer_output" rmdir /s /q "installer_output"
if exist "release_assets\v%VER%" rmdir /s /q "release_assets\v%VER%"
echo.

REM ---------- PyInstaller ----------
echo [4/6] 打包 EXE（这一步比较慢，几分钟很正常）...
%PY% -m PyInstaller build_bbdown_launcher.spec --noconfirm --clean
if errorlevel 1 goto :fail_build
if not exist "dist\BBDown\BBDown.exe" (
    echo [失败] 打包跑完了但没有生成 dist\BBDown\BBDown.exe。
    goto :fail
)
echo.

REM ---------- 解压版 ----------
echo [5/6] 生成解压版 zip...
mkdir "release_assets\v%VER%" 2>nul
powershell -NoProfile -Command "Compress-Archive -Path '.\dist\BBDown' -DestinationPath '.\release_assets\v%VER%\BBDown-%VER%.zip' -Force"
if errorlevel 1 goto :fail
echo.

REM ---------- 安装包 ----------
echo [6/6] 生成安装包...
set "ISCC="
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC (
    where ISCC >nul 2>nul
    if !errorlevel!==0 set "ISCC=ISCC"
)
if not defined ISCC (
    echo [跳过] 没找到 Inno Setup 6 的 ISCC.exe，安装包没生成。
    echo        解压版已经好了，装上 Inno Setup 6 后重跑本脚本即可。
    goto :summary
)
"%ISCC%" installer.iss
if errorlevel 1 goto :fail
copy /y "installer_output\BBDown-%VER%.exe" "release_assets\v%VER%\BBDown-%VER%.exe" >nul

:summary
echo.
echo ============================================
echo   打包完成
echo ============================================
if exist "release_assets\v%VER%\BBDown-%VER%.exe" (
    echo   安装包  release_assets\v%VER%\BBDown-%VER%.exe
) else (
    echo   安装包  未生成
)
if exist "release_assets\v%VER%\BBDown-%VER%.zip" (
    echo   解压版  release_assets\v%VER%\BBDown-%VER%.zip
) else (
    echo   解压版  未生成
)
echo.
echo   还没结束：装上去实测过才能推送。重点测三条——
echo     1. 打开后不要求重新激活（老授权要能延续）
echo     2. set BBDOWN_LICENSE_REQUIRED=0 再启动，仍然要求激活
echo     3. 三个页面各跑一批任务
echo.
pause
exit /b 0

:fail_build
echo.
echo [失败] PyInstaller 打包失败。
echo        如果提示缺少 PyInstaller，先执行：%PY% -m pip install pyinstaller
goto :fail

:fail
echo.
echo 打包中断，上面有报错信息。把报错内容发给开发者。
echo.
pause
exit /b 1

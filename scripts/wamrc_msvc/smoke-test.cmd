@echo off
setlocal EnableExtensions
pushd "%~dp0" || exit /b 1
set "OUTDIR=smoke-output-%RANDOM%-%RANDOM%"
if exist "%OUTDIR%" goto failed
mkdir "%OUTDIR%" || goto failed
..\wamrc.exe --version
if errorlevel 1 goto failed
..\wamrc.exe --llvm-version
if errorlevel 1 goto failed
..\wamrc.exe --target=x86_64 --target-abi=msvc --cpu=generic --disable-simd --bounds-checks=1 --stack-bounds-checks=1 -o "%OUTDIR%\answer-x86_64.aot" answer.wasm
if errorlevel 1 goto failed
..\wamrc.exe --target=aarch64v8 --target-abi=gnu --cpu=generic --disable-simd --bounds-checks=1 --stack-bounds-checks=1 -o "%OUTDIR%\answer-aarch64.aot" answer.wasm
if errorlevel 1 goto failed
..\wamrc.exe --target=armv7 --target-abi=gnueabihf --cpu=cortex-a9 --disable-simd --bounds-checks=1 --stack-bounds-checks=1 -o "%OUTDIR%\answer-armv7.aot" answer.wasm
if errorlevel 1 goto failed
for %%N in (x86_64 aarch64 armv7) do (
  if not exist "%OUTDIR%\answer-%%N.aot" goto failed
  for %%F in ("%OUTDIR%\answer-%%N.aot") do if %%~zF LEQ 8 goto failed
)
echo PASS: compiler executed and produced x64, ARM64 and ARMv7 AOT files.
echo This repeatable test does not execute the generated AOT files.
echo Outputs: %OUTDIR%
popd
exit /b 0
:failed
echo FAIL: a command failed or an AOT file was missing or empty.
popd
exit /b 1

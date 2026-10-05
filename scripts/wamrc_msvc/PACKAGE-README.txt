wamrc 2.4.3 | Windows x64 | native MSVC build

This is an independently built, unsigned binary, not an official WAMR release.
WAMR source commit: 2b25b20474eaf85e1358f9b795de6b2da267cb7a
Source: https://github.com/wasm-micro-runtime/wasm-micro-runtime
No WAMR source patches are applied. The upstream version remains 2.4.3.

Extract the complete directory. In Command Prompt:
  wamrc.exe --version
  tests\smoke-test.cmd

Use a modern Windows x64 system. The executable imports the built-in Windows
path API set (api-ms-win-core-path-l1-1-0.dll); Windows 7 is not supported.
Native runtime testing was performed on Windows Server 2022, not every Windows version.

Basic use:
  wamrc.exe -o output.aot input.wasm

For a different destination CPU/OS/ABI, explicitly select the matching --target,
--target-abi, --cpu and other options. The test ARM options are examples; they
are not a claim of compatibility with a particular PLC or embedded device.

BUILD
- Native Windows Server 2022 x64, Visual Studio 2022 MSVC, Release, Ninja/CMake
- LLVM 18.1.8: 3b5b5c1ec4a3095ab096dd780e84d7ab81f3d7ff
- Static LLVM libraries; default backends AArch64, ARM, Mips, RISCV and X86
- LLVM and WAMR consistently use the static Microsoft CRT (/MT)
- No separate VC runtime, LLVM or MinGW DLL is required by the verified imports
- Only built-in Windows DLLs are permitted by the package validation
- Optional LLVM zlib, zstd, XML, HTTP and DIA dependencies are disabled
- Other than packaging choices, normal WAMR compiler feature defaults are kept

CHECKS PERFORMED BEFORE THIS PACKAGE WAS CREATED
- wamrc.exe --version: 2.4.3
- wamrc.exe --llvm-version: LLVM 18.1.8
- Native execution on a Windows Server 2022 x64 GitHub-hosted runner
- Minimal answer.wasm compiled to x86_64/MSVC, AArch64/GNU and ARMv7/hard-float AOT
- A separately built native x64 iwasm executed both the Wasm and x64 AOT: answer()=42
- PE machine type, system-DLL imports, archive extraction and SHA-256 integrity
- The extracted wamrc.exe also executed successfully

ARM64/ARMv7 AOT files were not executed on ARM hardware. These are smoke tests,
not a full WebAssembly conformance suite or validation of your application.
Mips/RISCV libraries were checked for presence but not smoke-compiled.
The test runtime is not shipped. Smoke-test.cmd repeats the compiler checks;
it does not execute generated AOT files. The complete original test evidence,
commands, source identities and tool versions are in build-info.

INTEGRITY AND LICENSES
SHA256SUMS.txt lists every payload file except that checksum manifest itself.
An additional .sha256 file alongside the archive checks the complete archive.
licenses contains the WAMR and LLVM license texts and retained LLVM notices.
The Microsoft C/C++ runtime is statically linked. Microsoft runtime distribution
information: https://learn.microsoft.com/en-us/visualstudio/releases/2022/redistribution

The build-recipe directory records the actual build script and workflow. Its
two files alone are not a complete copy of the launch kit; the Actions run and
workflow commit are recorded in build-info/build-state.json.

#!/usr/bin/env python3
"""Reproducible-source Windows/MSVC wamrc builder. Uses Python's standard library.

This recipe does not edit either upstream source tree. Commands are logged and
checked; a package is emitted only after native Windows tests have passed.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import time
import zipfile

WAMR_SHA = "2b25b20474eaf85e1358f9b795de6b2da267cb7a"
LLVM_SHA = "3b5b5c1ec4a3095ab096dd780e84d7ab81f3d7ff"
LLVM_VERSION = "18.1.8"
WAMR_VERSION = "2.4.3"
BACKENDS = ["AArch64", "ARM", "Mips", "RISCV", "X86"]
PACKAGE_NAME = "wamrc-2b25b204-windows-x64-msvc"
MAX_DELIVERY_BYTES = 20_000_000
# (module (func (export "answer") (result i32) i32.const 42))
WASM_BYTES = bytes.fromhex(
    "0061736d010000000105016000017f03020100070a0106616e737765720000"
    "0a06010400412a0b"
)

ROOT = Path(os.environ.get("GITHUB_WORKSPACE", Path(__file__).resolve().parents[2]))
SOURCE = ROOT / "_src" / "wamr"
LLVM_SOURCE = ROOT / "_src" / "llvm-project"
BUILD = ROOT / "_build"
INSTALL = ROOT / "_llvm18-msvc"
LOGS = ROOT / "_logs"
STATE = LOGS / "build-state.json"
JOBS = int(os.environ.get("BUILD_JOBS", "2"))


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run(args: list[str | Path], log: str, *, cwd: Path = ROOT,
        allowed_codes: tuple[int, ...] = (0,), read_output: bool = False) -> str:
    """Stream combined stdout/stderr to a bounded-memory log and the Actions UI."""
    LOGS.mkdir(parents=True, exist_ok=True)
    command = [str(arg) for arg in args]
    display = subprocess.list2cmdline(command)
    print(f"\n> {display}", flush=True)
    with (LOGS / log).open("w", encoding="utf-8") as output:
        output.write(f"cwd: {cwd}\ncommand: {display}\n\n")
        with subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                              errors="replace", bufsize=1) as process:
            assert process.stdout is not None
            for line in process.stdout:
                output.write(line)
                print(line, end="", flush=True)
            code = process.wait()
        output.write(f"\nexit_code: {code}\n")
    require(code in allowed_codes, f"Command failed ({code}); see {LOGS / log}")
    # Most outputs are small. Build logs are read only where explicitly needed.
    return (LOGS / log).read_text(encoding="utf-8") if read_output else ""


def capture(args: list[str | Path], *, cwd: Path = ROOT) -> str:
    return subprocess.check_output([str(arg) for arg in args], cwd=cwd,
                                   text=True, encoding="utf-8", errors="replace").strip()


def init_msvc() -> dict[str, str]:
    require(sys.platform == "win32", "Building requires native Windows, not Wine or MinGW.")
    require(JOBS in (2, 4), "BUILD_JOBS must be 2 or 4.")
    vswhere = Path(os.environ["ProgramFiles(x86)"]) / "Microsoft Visual Studio/Installer/vswhere.exe"
    require(vswhere.is_file(), "vswhere.exe is missing from this runner.")
    vs = capture([vswhere, "-latest", "-products", "*", "-version", "[17.0,18.0)",
                  "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                  "-property", "installationPath"])
    require(bool(vs), "Visual Studio 2022 with C++ x64 tools was not found.")
    devcmd = Path(vs) / "Common7/Tools/VsDevCmd.bat"
    # All command text is fixed except the path returned by Microsoft's vswhere.
    # Capture internally: do not print the inherited environment into CI logs.
    command = f'call "{devcmd}" -no_logo -arch=amd64 -host_arch=amd64 && set'
    result = subprocess.check_output(command, shell=True, text=True,
                                     encoding="utf-8", errors="replace")
    for line in result.splitlines():
        key, separator, value = line.partition("=")
        if separator and key and not key.startswith("="):
            os.environ[key] = value
    require(os.environ.get("VSCMD_ARG_TGT_ARCH") == "x64", "MSVC target is not x64.")
    for tool in ("cl", "link", "lib", "ml64", "dumpbin", "cmake", "ninja", "7z"):
        require(shutil.which(tool) is not None, f"Required preinstalled tool is missing: {tool}")
    compiler = Path(shutil.which("cl") or "").resolve()
    require("hostx64" in str(compiler).lower(), "MSVC must use its native x64 host compiler.")
    # No preinstalled LLVM/MinGW package is used for libraries or compilation.
    return {
        "visual_studio": vs,
        "vc_tools_version": os.environ["VCToolsVersion"].strip("\\/"),
        "windows_sdk_version": os.environ["WindowsSDKVersion"].strip("\\/"),
        "compiler": str(compiler),
        "cmake": capture(["cmake", "--version"]).splitlines()[0],
        "ninja": capture(["ninja", "--version"]),
        "crt": "MultiThreaded (/MT)",
        "generator": "Ninja",
    }


def verify_checkout(path: Path, expected: str, prefix: str) -> None:
    actual = capture(["git", "-C", path, "rev-parse", "HEAD"])
    require(actual == expected, f"{prefix} checkout is {actual}, expected {expected}.")
    require(not capture(["git", "-C", path, "status", "--porcelain", "--untracked-files=no"]),
            f"{prefix} tracked source files were modified.")
    run(["git", "-C", path, "show", "-s", "--format=fuller", "HEAD"], f"{prefix}-source.txt")


def common_cmake() -> list[str]:
    return ["-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release",
            "-DCMAKE_C_COMPILER=cl.exe", "-DCMAKE_CXX_COMPILER=cl.exe",
            "-DCMAKE_POLICY_DEFAULT_CMP0091=NEW",
            "-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded",
            "-DBUILD_SHARED_LIBS=OFF"]


def llvm_options() -> list[str]:
    return common_cmake() + [
        f"-DCMAKE_INSTALL_PREFIX={INSTALL.as_posix()}",
        f"-DLLVM_TARGETS_TO_BUILD={';'.join(BACKENDS)}",
        "-DLLVM_DEFAULT_TARGET_TRIPLE=x86_64-pc-windows-msvc",
        "-DLLVM_ENABLE_PROJECTS=", "-DLLVM_ENABLE_RUNTIMES=",
        "-DLLVM_ENABLE_ASSERTIONS=OFF", "-DLLVM_APPEND_VC_REV=ON",
        "-DLLVM_BUILD_LLVM_DYLIB=OFF", "-DLLVM_LINK_LLVM_DYLIB=OFF",
        "-DLLVM_BUILD_TOOLS=OFF", "-DLLVM_INCLUDE_TOOLS=OFF",
        "-DLLVM_BUILD_EXAMPLES=OFF", "-DLLVM_INCLUDE_EXAMPLES=OFF",
        "-DLLVM_INCLUDE_TESTS=OFF", "-DLLVM_INCLUDE_BENCHMARKS=OFF",
        "-DLLVM_INCLUDE_DOCS=OFF", "-DLLVM_INCLUDE_UTILS=OFF",
        "-DLLVM_ENABLE_BINDINGS=OFF", "-DLLVM_ENABLE_IDE=OFF",
        "-DLLVM_ENABLE_LIBEDIT=OFF", "-DLLVM_ENABLE_TERMINFO=OFF",
        "-DLLVM_ENABLE_ZLIB=OFF", "-DLLVM_ENABLE_ZSTD=OFF",
        "-DLLVM_ENABLE_LIBXML2=OFF", "-DLLVM_ENABLE_CURL=OFF",
        "-DLLVM_ENABLE_HTTPLIB=OFF", "-DLLVM_ENABLE_DIA_SDK=OFF",
        "-DLLVM_OPTIMIZED_TABLEGEN=ON", f"-DLLVM_PARALLEL_COMPILE_JOBS={JOBS}",
        "-DLLVM_PARALLEL_LINK_JOBS=1",
    ]


def llvm_recipe_sha256(script: Path | None = None) -> str:
    """Fingerprint LLVM build logic, excluding later tests and packaging.

    The audited first-run logic maps to its original whole-script digest so the
    completed SDK can be reused after a validation-only fix. Any change to an
    LLVM build function or relevant constant gets a different identity. Actual
    toolchain and expanded CMake options are also independently in the key.
    """
    source = (script or Path(__file__)).read_text(encoding="utf-8")
    names = {"require", "sha256", "write_json", "run", "capture", "init_msvc",
             "verify_checkout", "common_cmake", "llvm_options", "llvm",
             "LLVM_SHA", "LLVM_VERSION", "BACKENDS", "ROOT", "LLVM_SOURCE",
             "BUILD", "INSTALL", "LOGS", "STATE", "JOBS"}
    nodes = [node for node in ast.parse(source).body
             if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names)
             or (isinstance(node, ast.Assign) and any(
                 isinstance(target, ast.Name) and target.id in names for target in node.targets))]
    canonical = "\n\n".join(ast.get_source_segment(source, node) for node in nodes)
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    # First successful LLVM build: workflow commit ff3234ff3b77551cf1b6f79b6bae3f0dbf6351dc.
    # The original whole-file digest is for the runner's CRLF checkout.
    if digest == "c57590b70620f878599093e7a2aa64917cf83ffed9bf4cc06cae699da51eb7cd":
        return "9500242d7419fa3006fcfdddf2f5e2f6afe3ffdaedcfd85b2ad454ef73efa8ae"
    return digest


def prepare() -> None:
    toolchain = init_msvc()
    verify_checkout(SOURCE, WAMR_SHA, "wamr")
    header = (SOURCE / "core/version.h").read_text(encoding="utf-8")
    for component, value in zip(("MAJOR", "MINOR", "PATCH"), (2, 4, 3)):
        require(re.search(rf"#define\s+WAMR_VERSION_{component}\s+{value}\b", header) is not None,
                "Pinned source does not report expected WAMR version 2.4.3.")
    run(["cl", "/Bv"], "msvc-version.txt", allowed_codes=(0, 2))
    # Hash LLVM build logic as well as compiler/SDK/CRT/configuration. Do not reuse
    # approximate cache matches or libraries made with another C++ toolchain.
    identity = {"llvm_sha": LLVM_SHA, "toolchain": toolchain,
                "options": llvm_options(), "recipe_sha256": llvm_recipe_sha256()}
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    cache_key = f"llvm-{LLVM_VERSION}-windows2022-x64-msvc-mt-{fingerprint}"
    state = {"wamr_sha": WAMR_SHA, "llvm_sha": LLVM_SHA,
             "llvm_version": LLVM_VERSION, "wamr_version": WAMR_VERSION,
             "llvm_backends": BACKENDS, "toolchain": toolchain,
             "cache_key": cache_key, "parallel_jobs": JOBS,
             "recipe_sha256": sha256(Path(__file__)),
             "llvm_recipe_sha256": identity["recipe_sha256"],
             "workflow_commit": os.environ.get("GITHUB_SHA"),
             "run_url": f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/"
                        f"{os.environ.get('GITHUB_REPOSITORY', '')}/actions/runs/"
                        f"{os.environ.get('GITHUB_RUN_ID', '')}",
             "runner_image": os.environ.get("ImageVersion"),
             "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    write_json(STATE, state)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write(f"cache_key={cache_key}\n")
    free = shutil.disk_usage(ROOT).free
    print(f"Available workspace disk: {free / 2**30:.1f} GiB; cache key: {cache_key}")
    require(free >= 7 * 2**30, "Less than 7 GiB free; use a runner with more disk space.")


def llvm() -> None:
    init_msvc()
    verify_checkout(LLVM_SOURCE, LLVM_SHA, "llvm")
    require(shutil.disk_usage(ROOT).free >= 7 * 2**30,
            "Less than 7 GiB free for the LLVM build; increase runner storage.")
    out = BUILD / "llvm"
    run(["cmake", "-S", LLVM_SOURCE / "llvm", "-B", out] + llvm_options(), "llvm-configure.txt")
    run(["cmake", "--build", out, "--config", "Release", "--parallel", str(JOBS)], "llvm-build.txt")
    run(["cmake", "--install", out, "--config", "Release"], "llvm-install.txt")
    require((INSTALL / "lib/cmake/llvm/LLVMConfig.cmake").is_file(), "LLVM SDK installation missing.")
    for name in ["Core", "Support"] + [f"{backend}CodeGen" for backend in BACKENDS]:
        require((INSTALL / "lib" / f"LLVM{name}.lib").is_file(), f"Missing LLVM{name}.lib.")
    shutil.copy2(LLVM_SOURCE / "llvm/LICENSE.TXT", INSTALL / "LLVM-LICENSE.TXT")
    # LLVM license text includes its retained third-party notices.
    shutil.copy2(out / "CMakeCache.txt", LOGS / "llvm-CMakeCache.txt")
    verify_checkout(LLVM_SOURCE, LLVM_SHA, "llvm")
    state = json.loads(STATE.read_text(encoding="utf-8"))
    cached_logs = INSTALL / "build-evidence"
    cached_logs.mkdir()
    for filename in ("llvm-source.txt", "llvm-configure.txt", "llvm-build.txt",
                     "llvm-install.txt", "llvm-CMakeCache.txt", "msvc-version.txt"):
        shutil.copy2(LOGS / filename, cached_logs / filename)
    write_json(INSTALL / "wamrc-sdk-provenance.json", {
        "cache_key": state["cache_key"], "llvm_sha": LLVM_SHA,
        "toolchain": state["toolchain"], "configuration": llvm_options(),
        "origin_run_url": state["run_url"], "sdk_file_sha256": {
            item.relative_to(INSTALL).as_posix(): sha256(item)
            for item in sorted(INSTALL.rglob("*")) if item.is_file()}})
    # Only the relocatable installed SDK is cached. The large object tree is
    # no longer needed, and removing this generated directory preserves source.
    shutil.rmtree(out)


def verify_sdk() -> None:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    manifest_path = INSTALL / "wamrc-sdk-provenance.json"
    require(manifest_path.is_file(), "Incomplete/unrecognized LLVM cache. Change recipe/cache key and rebuild.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(manifest["cache_key"] == state["cache_key"], "LLVM cache configuration mismatch.")
    require(manifest["llvm_sha"] == LLVM_SHA, "LLVM cache source mismatch.")
    require(manifest["toolchain"] == state["toolchain"], "LLVM cache toolchain mismatch.")
    require(manifest["configuration"] == llvm_options(), "LLVM cache CMake configuration mismatch.")
    actual_files = {item.relative_to(INSTALL).as_posix()
                    for item in INSTALL.rglob("*") if item.is_file() and item != manifest_path}
    require(actual_files == set(manifest["sdk_file_sha256"]), "LLVM cache file inventory mismatch.")
    for name, digest in manifest["sdk_file_sha256"].items():
        file = INSTALL / name
        require(file.is_file() and sha256(file) == digest, f"LLVM cache file mismatch: {name}.")
    shutil.copy2(manifest_path, LOGS / "llvm-sdk-provenance.json")
    for file in (INSTALL / "build-evidence").glob("*"):
        shutil.copy2(file, LOGS / f"sdk-origin-{file.name}")


def wamrc() -> None:
    init_msvc()
    verify_sdk()
    verify_checkout(SOURCE, WAMR_SHA, "wamr")
    options = common_cmake() + ["-DWAMR_BUILD_PLATFORM=windows", "-DWAMR_BUILD_TARGET=X86_64",
        "-DWAMR_BUILD_WITH_CUSTOM_LLVM=1", f"-DLLVM_DIR={(INSTALL / 'lib/cmake/llvm').as_posix()}",
        # wamrc already hardcodes WASM_DISABLE_HW_BOUND_CHECK=1 in its source
        # CMakeLists. Set the matching CMake variable to avoid fetching unused
        # Zydis/Zycore libraries. This does not change compiler behavior.
        "-DWAMR_DISABLE_HW_BOUND_CHECK=1"]
    out = BUILD / "wamrc"
    run(["cmake", "-S", SOURCE / "wamr-compiler", "-B", out] + options, "wamrc-configure.txt")
    run(["cmake", "--build", out, "--config", "Release", "--parallel", str(JOBS)], "wamrc-build.txt")
    shutil.copy2(out / "CMakeCache.txt", LOGS / "wamrc-CMakeCache.txt")
    # This runtime is a test helper, not a general-purpose runtime distribution.
    runtime_options = common_cmake() + ["-DWAMR_BUILD_TARGET=X86_64",
        "-DWAMR_BUILD_INTERP=1", "-DWAMR_BUILD_AOT=1", "-DWAMR_BUILD_JIT=0",
        "-DWAMR_BUILD_FAST_JIT=0", "-DWAMR_BUILD_SIMD=1", "-DWAMR_BUILD_REF_TYPES=1",
        "-DWAMR_DISABLE_HW_BOUND_CHECK=1"]
    runtime_out = BUILD / "iwasm"
    run(["cmake", "-S", SOURCE / "product-mini/platforms/windows", "-B", runtime_out]
        + runtime_options, "iwasm-configure.txt")
    run(["cmake", "--build", runtime_out, "--config", "Release", "--parallel", str(JOBS)], "iwasm-build.txt")
    shutil.copy2(runtime_out / "CMakeCache.txt", LOGS / "iwasm-CMakeCache.txt")
    verify_checkout(SOURCE, WAMR_SHA, "wamr")


def check_pe(executable: Path, label: str) -> list[str]:
    with executable.open("rb") as stream:
        require(stream.read(2) == b"MZ", f"{label} is not a Windows executable.")
        stream.seek(0x3C)
        stream.seek(struct.unpack("<I", stream.read(4))[0])
        require(stream.read(4) == b"PE\x00\x00", f"Invalid {label} PE header.")
        require(struct.unpack("<H", stream.read(2))[0] == 0x8664, f"{label} is not PE x64.")
    output = run(["dumpbin", "/dependents", executable], f"{label}-imports.txt", read_output=True)
    dependencies = sorted(set(re.findall(r"^\s+([\w.-]+\.dll)\s*$", output, re.M | re.I)))
    require(bool(dependencies), f"No PE imports parsed for {label}.")
    # All are built-in Windows components. Fail closed on unexpected DLLs,
    # including MSVCP/VCRUNTIME, MinGW libraries, LLVM, zlib, or DIA components.
    allowed = {"kernel32.dll", "advapi32.dll", "bcrypt.dll", "ntdll.dll", "ole32.dll",
               "shell32.dll", "user32.dll", "ws2_32.dll", "pathcch.dll", "psapi.dll",
               "version.dll", "shlwapi.dll", "oleaut32.dll", "crypt32.dll", "secur32.dll",
               # Windows path API set, documented by Microsoft; no wildcard allowance.
               # https://learn.microsoft.com/en-us/uwp/win32-and-com/win32-apis
               "api-ms-win-core-path-l1-1-0.dll"}
    unexpected = [dll for dll in dependencies if dll.lower() not in allowed]
    require(not unexpected, f"{label} needs unapproved runtime DLLs: {unexpected}")
    return dependencies


def test() -> None:
    init_msvc()
    compiler = BUILD / "wamrc/wamrc.exe"
    runtime = BUILD / "iwasm/iwasm.exe"
    version = run([compiler, "--version"], "wamrc-version.txt", read_output=True)
    require(re.search(r"\b2\.4\.3\b", version) is not None, "wamrc version is not 2.4.3.")
    llvm_version = run([compiler, "--llvm-version"], "wamrc-llvm-version.txt", read_output=True)
    require(re.search(r"\bLLVM 18\.1\.8\b", llvm_version) is not None,
            "The compiler is not linked against LLVM 18.1.8.")
    compiler_imports = check_pe(compiler, "wamrc")
    runtime_imports = check_pe(runtime, "iwasm")
    fixtures = BUILD / "tests"
    fixtures.mkdir(parents=True, exist_ok=True)
    wasm = fixtures / "answer.wasm"
    wasm.write_bytes(WASM_BYTES)
    (fixtures / "answer.wat").write_text(
        '(module (func (export "answer") (result i32) i32.const 42))\n', encoding="utf-8")
    configurations = [
        ("x86_64", "x86_64", "msvc", "generic"),
        ("aarch64", "aarch64v8", "gnu", "generic"),
        ("armv7", "armv7", "gnueabihf", "cortex-a9"),
    ]
    results = []
    for label, target, abi, cpu in configurations:
        aot = fixtures / f"answer-{label}.aot"
        command = [str(compiler), f"--target={target}", f"--target-abi={abi}", f"--cpu={cpu}",
                   "--disable-simd", "--bounds-checks=1", "--stack-bounds-checks=1",
                   "-o", str(aot), str(wasm)]
        run(command, f"aot-{label}.txt")
        require(aot.is_file() and aot.stat().st_size > 8 and aot.read_bytes()[:4] == b"\x00aot",
                f"{label} output is missing or has an invalid AOT header.")
        results.append({"target": label, "compile": "passed", "command": command,
                        "aot_sha256": sha256(aot), "aot_bytes": aot.stat().st_size,
                        "execution": "not run on ARM hardware" if label != "x86_64" else "pending"})
    for kind, input_file in (("wasm", wasm), ("aot", fixtures / "answer-x86_64.aot")):
        result = run([runtime, "-f", "answer", input_file], f"execute-{kind}.txt", read_output=True)
        require(re.search(r"\b0x2a:i32\b", result, re.I) is not None,
                f"Native Windows {kind} test did not return 42 (0x2a:i32).")
    results[0]["execution"] = "passed: native Windows x64 iwasm returned 0x2a:i32"
    verify_checkout(SOURCE, WAMR_SHA, "wamr")
    write_json(LOGS / "validation.json", {
        "status": "passed", "wamrc_version": WAMR_VERSION, "llvm_version": LLVM_VERSION,
        "host": "native Windows Server 2022 x64", "wasm_answer": 42,
        "x64_aot_answer": 42, "tests": results,
        "wamrc_imports": compiler_imports, "iwasm_imports": runtime_imports,
        "wamrc_sha256": sha256(compiler), "iwasm_sha256": sha256(runtime),
        "wamr_tracked_source_unmodified": True})


def package() -> None:
    init_msvc()
    validation_path = LOGS / "validation.json"
    require(validation_path.is_file(), "Native test evidence is missing; refusing to package.")
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    require(validation["status"] == "passed", "Native tests did not pass.")
    compiler = BUILD / "wamrc/wamrc.exe"
    require(sha256(compiler) == validation["wamrc_sha256"], "Compiler changed after testing.")
    verify_checkout(SOURCE, WAMR_SHA, "wamr")
    stage = ROOT / "_package" / PACKAGE_NAME
    require(not stage.exists(), "Package staging exists; run in a fresh workflow.")
    (stage / "licenses").mkdir(parents=True)
    shutil.copy2(compiler, stage / "wamrc.exe")
    shutil.copy2(SOURCE / "LICENSE", stage / "licenses/WAMR-LICENSE.txt")
    shutil.copy2(INSTALL / "LLVM-LICENSE.TXT", stage / "licenses/LLVM-LICENSE.txt")
    # The test runtime is intentionally not in the delivery archive.
    shutil.copytree(BUILD / "tests", stage / "tests")
    shutil.copytree(LOGS, stage / "build-info")
    (stage / "build-recipe").mkdir()
    shutil.copy2(Path(__file__), stage / "build-recipe/build.py")
    shutil.copy2(ROOT / ".github/workflows/wamrc-msvc.yml", stage / "build-recipe/workflow.yml")
    shutil.copy2(ROOT / "scripts/wamrc_msvc/PACKAGE-README.txt", stage / "README.txt")
    shutil.copy2(ROOT / "scripts/wamrc_msvc/smoke-test.cmd", stage / "tests/smoke-test.cmd")
    manifest = "".join(f"{sha256(path)}  {path.relative_to(stage).as_posix()}\n"
                       for path in sorted(stage.rglob("*")) if path.is_file())
    (stage / "SHA256SUMS.txt").write_text(manifest, encoding="utf-8")
    dist = ROOT / "dist"
    require(not dist.exists(), "Delivery directory exists; run in a fresh workflow.")
    dist.mkdir()
    archive = dist / f"{PACKAGE_NAME}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as output:
        for file in sorted(stage.rglob("*")):
            if file.is_file():
                output.write(file, file.relative_to(stage.parent))
    if archive.stat().st_size > MAX_DELIVERY_BYTES:
        archive.unlink()
        archive = dist / f"{PACKAGE_NAME}.7z"
        run(["7z", "a", "-t7z", "-mx=9", "-m0=lzma2", "-md=64m", "-mmt=2",
             archive, stage.name], "archive-create.txt", cwd=stage.parent)
    run(["7z", "t", archive], "archive-test.txt")
    verify = ROOT / "_package-verification"
    run(["7z", "x", "-y", f"-o{verify}", archive], "archive-extract.txt")
    extracted = verify / PACKAGE_NAME
    for line in manifest.splitlines():
        digest, relative = line.split("  ", 1)
        require(sha256(extracted / relative) == digest, f"Archive checksum mismatch: {relative}")
    require((extracted / "SHA256SUMS.txt").read_text(encoding="utf-8") == manifest,
            "Archive file manifest does not match the generated manifest.")
    # Verify the exact packaged executable also starts after extraction.
    run([extracted / "wamrc.exe", "--version"], "packaged-version.txt")
    digest = sha256(archive)
    (dist / f"{archive.name}.sha256").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    size = archive.stat().st_size
    status = "within the 20,000,000-byte delivery limit" if size <= MAX_DELIVERY_BYTES else "OVER the 20,000,000-byte delivery limit"
    summary = (f"Built unpatched WAMR {WAMR_SHA} using VS 2022 MSVC x64 /MT and LLVM {LLVM_VERSION}.\n"
               "Windows version, three AOT compilation targets, and native x64 Wasm/AOT answer()=42 passed.\n"
               "ARM64/ARMv7 AOT was compiled but not executed on ARM hardware.\n"
               f"Archive: {archive.name} ({size:,} bytes; {status})\nSHA-256: {digest}\n")
    (dist / "BUILD-RESULT.txt").write_text(summary, encoding="utf-8")
    print(summary)
    if size > MAX_DELIVERY_BYTES:
        print("::warning::Archive exceeds the attachment limit. Use the Actions artifact link; do not claim it fits.")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as output:
            output.write(summary)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("prepare", "llvm", "wamrc", "test", "package"))
    options = parser.parse_args()
    globals()[options.stage]()


if __name__ == "__main__":
    main()

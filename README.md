# Pinned native-MSVC wamrc build kit

This is a reviewed build recipe, not a prebuilt binary. Preparing the kit does
not launch a job. A successful Windows Actions run is required before calling
the binary built or verified.

## Owner launch

1. Choose a GitHub.com repository you authorize for this build. Review its Actions
   availability, Windows build-minute allowance/budget, and artifact/cache storage.
2. Add `.github/workflows/wamrc-msvc.yml` and `scripts/wamrc_msvc/` from this kit.
   The workflow must be present on the default branch for its manual Run workflow
   control to appear. Any repository write or run needs the owner's authorization.
3. Open **Actions → Build pinned wamrc with MSVC → Run workflow**. Select `2`
   compile jobs for the conservative default; `4` can help on a four-CPU runner.
4. Wait for a green run. Read its summary and download
   `wamrc-2b25b204-windows-x64-msvc`. GitHub may place the delivery archive and its
   checksum/result text inside an outer artifact ZIP. Extract that outer ZIP,
   then extract the inner delivery ZIP or 7z.
5. Check the SHA-256 against the accompanying `.sha256` file, then run
   `wamrc.exe --version` and optionally `tests\smoke-test.cmd` on your Windows PC.

The workflow is manual-only, has no schedule/push/pull-request trigger, publishes
no release, requests only `contents: read`, and disables checkout credential
persistence. It uses the runner's preinstalled VS2022, Python, CMake, Ninja and
7-Zip without installing tools. It does not require any custom credential.
If API dispatch is unavailable, the owner can use the manual button. A narrowly
scoped push trigger is another possible launch route, but is intentionally not
included and must be separately authorized before adding it.

## Build contract

- Fresh WAMR checkout at `2b25b20474eaf85e1358f9b795de6b2da267cb7a`, checked both
  before and after building. No source patch; source version remains `2.4.3`.
- Fresh LLVM 18.1.8 checkout at `3b5b5c1ec4a3095ab096dd780e84d7ab81f3d7ff`, the
  verified target of the `llvmorg-18.1.8` release tag. Sparse source checkout
  includes LLVM, shared CMake modules and third-party support.
- Native Windows Server 2022 x64; VS2022 x64-host/x64-target MSVC, Release, Ninja.
  All static C/C++ libraries consistently use `/MT`; no MinGW or prebuilt LLVM
  libraries are reused. This is not a bit-for-bit reproducible binary claim:
  hosted runner images/toolchain patches can change and are recorded.
- LLVM development headers, static `.lib` files and its exported CMake package
  are installed and cached. All five upstream default backends are built:
  AArch64, ARM, Mips, RISCV and X86.
- Preserve WAMR compiler feature defaults. `WAMR_DISABLE_HW_BOUND_CHECK=1` mirrors
  the compiler's already-hardcoded `WASM_DISABLE_HW_BOUND_CHECK=1`, avoiding
  unnecessary Zydis/Zycore fetching. A separate small iwasm uses explicit software
  bounds checks for native AOT testing and is not included in the delivery.
- Packaging choices differ from upstream's default recipe: static Microsoft CRT;
  optional LLVM zlib/zstd/XML/HTTP/DIA dependencies disabled; Ninja instead of the
  Visual Studio project generator; direct SDK installation instead of NSIS.
  WAMR and LLVM source remain unpatched.

## Verification gates

A delivery archive is created only if these checks pass:

1. Exact source commit, clean tracked files, expected `2.4.3` header and CLI
   version, and linked LLVM version `18.1.8`
2. Executables are PE x64 with only explicitly allowed built-in Windows DLL imports
3. Valid 39-byte Wasm fixture exporting `answer() -> i32`, returning `42`
4. Successful native wamrc generation of x64/MSVC, AArch64/GNU and ARMv7/GNU
   hard-float AOT files with valid AOT magic
5. Native x64 iwasm executes both the Wasm and x64 AOT and prints `0x2a:i32`
6. Archive integrity test, extraction, verification of every payload SHA-256,
   and execution of the exact extracted wamrc binary

ARM output is compile-tested only; Mips/RISCV library presence is checked but
these two backends are not smoke-compiled. No ARM execution, full conformance suite,
Windows client OS compatibility matrix, code signing, or PLC-specific claim is
implied by these tests. Compile flags for a deployment must match its CPU/ABI.

## Time, memory, storage and failure handling

The first LLVM build is the expensive part: plan for roughly one to several
hours, with actual time dependent on runner CPU and service load. This is an
estimate, not a measured run. The job timeout is 350 minutes; LLVM's step timeout
is 285 minutes. LLVM links are limited to one concurrent job. Compile parallelism
defaults to two to reduce memory pressure on smaller private-repository runners.

Standard GitHub Windows runners currently differ by repository visibility:
public repositories have 4 CPUs/16 GB RAM and private repositories 2 CPUs/8 GB RAM;
both list 14 GB SSD. The script checks for at least 7 GiB free after source
checkout but cannot guarantee every build fits. A storage failure requires an
authorized larger runner or a revised recipe, not disabling build features
silently. It only removes its own generated LLVM object tree after successful
installation; source trees are not deleted.

The installed LLVM SDK cache can be hundreds of MB to a few GB. Its key includes
the full LLVM SHA, all options, LLVM build-logic hash, exact MSVC/Windows SDK versions,
CMake/Ninja versions, static CRT, host/target architecture and installation path.
No approximate fallback cache is restored. A manifest verifies the complete
cached SDK file inventory and every SHA-256 before linking. Cache save happens immediately after a complete
LLVM build so a later WAMR failure does not discard that work. Compilation logs
and the originating run URL are retained inside the cache's build evidence.

Validation-only or packaging-only fixes preserve that expensive SDK cache. The
audited LLVM build logic from workflow commit `ff3234ff` maps to its original
whole-script digest; changing an LLVM build function or relevant constant changes
the identity. Cache verification also compares exact toolchain and CMake options.
The full current build-script hash is still recorded separately in build state.

Artifacts expire after 14 days; diagnostic logs after 7 days. Cache availability,
eviction, build minutes and storage charges follow the repository's current plan.
These workflows do not authorize changing billing or runner settings. Inspect
the Diagnostics artifact and the first failing command if a run fails. Only a
successful build/test run emits the delivery artifact.

The package first tries a maximum-compression ZIP and switches to LZMA2 7z if
ZIP exceeds 20,000,000 bytes. The resulting size is measured. If even 7z is over
that limit, the run explicitly warns; use the verified Actions artifact link or
approve another delivery method rather than misrepresenting its size.

## Local preparation checks

Run `python -m unittest discover -s scripts/wamrc_msvc/tests` (standard library
only). Python byte-compilation and YAML parsing can also be done on Linux. These
checks validate the recipe but are not native build or runtime evidence. The
builder intentionally refuses to build on a non-Windows host.

## Reference sources reviewed

- [Pinned upstream Windows CI](https://github.com/wasm-micro-runtime/wasm-micro-runtime/blob/2b25b20474eaf85e1358f9b795de6b2da267cb7a/.github/workflows/compilation_on_windows.yml)
- [Pinned upstream wamrc workflow](https://github.com/wasm-micro-runtime/wasm-micro-runtime/blob/2b25b20474eaf85e1358f9b795de6b2da267cb7a/.github/workflows/build_wamrc.yml)
- [Pinned upstream LLVM build script](https://github.com/wasm-micro-runtime/wasm-micro-runtime/blob/2b25b20474eaf85e1358f9b795de6b2da267cb7a/build-scripts/build_llvm.py)
- [LLVM 18.1.8 release tag object](https://api.github.com/repos/llvm/llvm-project/git/tags/76caf4176cd7877ad5952737d56da6f515ee4602)
- [LLVM CMake documentation](https://llvm.org/docs/CMake.html)
- [GitHub runner specifications](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)
- [Windows 2022 installed software](https://github.com/actions/runner-images/blob/main/images/windows/Windows2022-Readme.md)
- [Manually running a workflow](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)

The checkout, cache and upload-artifact action SHAs were resolved from their
official repositories on 2026-10-05. All action code is immutable in this recipe.

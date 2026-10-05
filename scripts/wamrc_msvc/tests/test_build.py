"""Offline tests of the recipe; these do not compile or execute Windows code."""

import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "build.py"
SPEC = importlib.util.spec_from_file_location("wamrc_builder", SCRIPT)
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class RecipeTests(unittest.TestCase):
    def test_source_pins(self):
        self.assertEqual(builder.WAMR_SHA, "2b25b20474eaf85e1358f9b795de6b2da267cb7a")
        self.assertEqual(builder.LLVM_SHA, "3b5b5c1ec4a3095ab096dd780e84d7ab81f3d7ff")
        self.assertEqual(builder.WAMR_VERSION, "2.4.3")
        self.assertEqual(builder.LLVM_VERSION, "18.1.8")

    def test_static_msvc_configuration(self):
        flags = builder.common_cmake()
        self.assertIn("-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded", flags)
        self.assertIn("-DCMAKE_POLICY_DEFAULT_CMP0091=NEW", flags)
        self.assertIn("-DCMAKE_C_COMPILER=cl.exe", flags)
        self.assertIn("-DCMAKE_CXX_COMPILER=cl.exe", flags)
        self.assertIn("-DCMAKE_BUILD_TYPE=Release", flags)
        self.assertIn("-DBUILD_SHARED_LIBS=OFF", flags)

    def test_llvm_backends(self):
        options = builder.llvm_options()
        self.assertIn("-DLLVM_TARGETS_TO_BUILD=AArch64;ARM;Mips;RISCV;X86", options)
        self.assertIn("-DLLVM_BUILD_LLVM_DYLIB=OFF", options)
        self.assertIn("-DLLVM_LINK_LLVM_DYLIB=OFF", options)
        self.assertIn("-DLLVM_PARALLEL_LINK_JOBS=1", options)

    def test_wasm_structure_and_constant(self):
        data = builder.WASM_BYTES
        self.assertEqual(data[:8], b"\0asm\x01\0\0\0")
        sections = {}
        position = 8
        while position < len(data):
            section_id, length = data[position:position + 2]
            self.assertLess(length, 128)  # This fixture has single-byte LEB sizes.
            position += 2
            sections[section_id] = data[position:position + length]
            position += length
        self.assertEqual(position, len(data))
        self.assertEqual(sections[1], b"\x01\x60\x00\x01\x7f")
        self.assertEqual(sections[3], b"\x01\x00")
        self.assertEqual(sections[7], b"\x01\x06answer\x00\x00")
        self.assertEqual(sections[10], b"\x01\x04\x00\x41\x2a\x0b")

    def test_json_and_hash_helpers(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "nested/state.json"
            builder.write_json(file, {"version": builder.WAMR_VERSION})
            self.assertEqual(json.loads(file.read_text()), {"version": "2.4.3"})
            self.assertEqual(len(builder.sha256(file)), 64)

    def make_pe(self, directory, machine=0x8664):
        data = bytearray(256)
        data[:2] = b"MZ"
        struct.pack_into("<I", data, 0x3C, 0x80)
        data[0x80:0x84] = b"PE\0\0"
        struct.pack_into("<H", data, 0x84, machine)
        path = Path(directory) / "sample.exe"
        path.write_bytes(data)
        return path

    def test_pe_system_imports_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            pe = self.make_pe(directory)
            with patch.object(builder, "run", return_value="    KERNEL32.dll\n    WS2_32.dll\n"):
                self.assertEqual(builder.check_pe(pe, "test"), ["KERNEL32.dll", "WS2_32.dll"])

    def test_pe_dynamic_crt_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            pe = self.make_pe(directory)
            for dependency in ("VCRUNTIME140.dll", "MSVCP140.dll", "libwinpthread-1.dll", "LLVM.dll"):
                with patch.object(builder, "run", return_value=f"    KERNEL32.dll\n    {dependency}\n"):
                    with self.assertRaisesRegex(RuntimeError, "unapproved runtime DLLs"):
                        builder.check_pe(pe, "test")

    def test_non_x64_pe_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            pe = self.make_pe(directory, machine=0x14C)
            with self.assertRaisesRegex(RuntimeError, "not PE x64"):
                builder.check_pe(pe, "test")

    def test_empty_import_output_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            pe = self.make_pe(directory)
            with patch.object(builder, "run", return_value="unrecognized output"):
                with self.assertRaisesRegex(RuntimeError, "No PE imports"):
                    builder.check_pe(pe, "test")


if __name__ == "__main__":
    unittest.main()

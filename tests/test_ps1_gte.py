"""Fixed matrix intrinsics allow original PS1 C without accepting arbitrary submitted assembly."""
import struct

import pytest

from decompnet import toolchains
from decompnet.compiler import SourceRejected, check_source, compile_source
from decompnet.consoles import get
from decompnet.elfobj import extract_function
from decompnet.project import compiler_config


def test_submitted_assembly_stays_forbidden():
    check_source('void f(void *p) { gte_SetRotMatrix(p); gte_SetTransMatrix(p); }')
    with pytest.raises(SourceRejected, match='inline assembly'):
        check_source('void f(void) { __asm__(".word 0x12345678"); }')
    with pytest.raises(SourceRejected, match='inline assembly'):
        check_source('#define gte_SetRotMatrix(p) __asm__("nop")\nvoid f(void *p){gte_SetRotMatrix(p);}')


def test_matrix_transfers_are_not_automatically_set_aside():
    ctc2 = (0x12 << 26) | (6 << 21) | (12 << 16)
    code = struct.pack('<III', ctc2, 0x03E00008, 0)
    assert get('ps').asm_reason(code) is None
    command = struct.pack('<III', 0x4A180001, 0x03E00008, 0)
    assert 'GTE' in get('ps').asm_reason(command)


@pytest.mark.parametrize('toolchain', ['gcc-2.6.3-psx', 'gcc-2.7.2-psx', 'gcc-2.8.1-psx'])
def test_intrinsics_compile_as_gte_register_transfers(tmp_path, toolchain):
    if toolchains.installed(toolchain) is None:
        pytest.skip('needs installed PS1 GCC')
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': toolchain, 'flags': ['-O2', '-G0']}})
    result = compile_source(cfg, '', 'void f(void *p) { gte_SetRotMatrix(p); gte_SetTransMatrix(p); }')
    assert result.ok, result.log
    fn = extract_function(result.obj, 'f')
    words = struct.unpack('<' + 'I' * (len(fn.code) // 4), fn.code)
    controls = [(w >> 11) & 31 for w in words if w >> 26 == 0x12 and (w >> 21) & 31 == 6]
    assert controls == list(range(8))

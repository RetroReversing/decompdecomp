"""Exact function verdicts and standalone-export placement are separate evidence."""
import struct
from io import BytesIO

from elftools.elf.elffile import ELFFile

from decompnet.app import candidate_diagnostics as diagnostics
from decompnet.app.engine.judging import JudgeMixin
from decompnet.arch.mips_image import linked_object
from decompnet.rom.match import LinkResult


def object_file():
    obj = bytearray(linked_object('f', 0, bytes.fromhex('03e0000800000000')))
    struct.pack_into('>H', obj, 16, 1)  # A homemade ET_REL input, not an already-linked image.
    return bytes(obj)


def test_noncontiguous_constants_are_not_advertised_as_ready_for_export():
    obj = object_file()
    ready = diagnostics.export_layout(obj, 'f', True, {'.rodata':{0:4096,8:4104}})
    assert ready['ready'] and 'complete byte-identical rebuild' in ready['message']
    blocked = diagnostics.export_layout(obj, 'f', True, {'.rodata':{0:4096,8:4200}})
    assert blocked['ready'] is False and 'non-contiguous' in blocked['message']
    assert diagnostics.export_layout(obj, 'f', False, {})['state'] == 'unmatched'
    assert diagnostics.export_layout(b'COFF', 'f', True, {})['ready'] is None


def test_extra_bodies_are_reported_without_changing_the_function_verdict(monkeypatch):
    obj = bytearray(linked_object('f\0extra',0,bytes(16)))
    struct.pack_into('>H',obj,16,1)
    elf = ELFFile(BytesIO(obj));symtab = elf.get_section_by_name('.symtab')
    symbols = bytearray(symtab.data())
    struct.pack_into('>I',symbols,16+8,8)
    symbols += struct.pack('>IIIBBH',3,8,8,0x12,0,1)
    offset = len(obj);obj += symbols
    struct.pack_into('>II',obj,elf['e_shoff']+2*elf['e_shentsize']+16,offset,len(symbols))
    assert diagnostics.export_layout(bytes(obj),'f',True,{})['ready'] is False
    candidate = object()
    monkeypatch.setattr('decompnet.rom.match.link',lambda *args:LinkResult(candidate,True,[],{'.rodata':{0:4096,8:4200}}))
    class Net(JudgeMixin):
        def is_rom(self, proj):return True
        def _rom_target(self, fn):return None
        def symbols(self, pid):return None
        def reader(self, proj, fn):return None
    net=Net();fn={'id':1,'name':'f'};proj={'id':1}
    got, exact, problems = net._evaluate(fn,proj,object_file())
    assert got is candidate and exact and not problems
    assert net._candidate_checks(fn,proj,object_file())['export']['ready'] is False


def test_unknown_addresses_are_classified_separately_from_instruction_differences():
    out = diagnostics.unresolved(['`color` is not a known symbol.', '`color` is not a known symbol.',
                                  '`table` is not a known symbol.'])
    assert out['symbols'] == ['color','table'] and 'before random permutations' in out['message']
    assert not diagnostics.unresolved(['your data differs from the ROM'])


def test_a_prefix_requires_builder_verification_instead_of_claiming_export_readiness():
    obj=bytearray(linked_object('f',0,bytes(16)))
    struct.pack_into('>H',obj,16,1)
    elf=ELFFile(BytesIO(obj));symbols=elf.get_section_by_name('.symtab')
    struct.pack_into('>II',obj,symbols['sh_offset']+16+4,8,8)
    result=diagnostics.export_layout(bytes(obj),'f',True,{})
    assert result['state']=='requires_normalization' and result['ready'] is None


def test_linked_byte_verdict_takes_precedence_over_symbolic_annotations():
    assert 'Linked bytes verified identical' in diagnostics.text({'linked_bytes':{'exact':True}})
    assert 'do not repair' in diagnostics.text({'linked_bytes':{'exact':True}})
    assert diagnostics.text({'linked_bytes':{'exact':False}}) == ''

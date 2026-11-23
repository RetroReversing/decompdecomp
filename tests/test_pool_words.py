"""The constants a function loads through `$at` reach the library's literal families (decomprepair `words`)."""
import struct

from decompnet.app import draft_repairs, library_registry, pool_words, promoted_entry_repairs
from decomprepair import registry

ASM = '''glabel f
/* 0 80001000 3C018010 */  lui        $at, %hi(D_800D2EF0)
/* 4 80001004 D4220000 */  ldc1       $ft3, %lo(D_800D2EF0)($at)
/* 8 80001008 3C018010 */  lui        $at, %hi(D_800D2F00)
/* C 8000100C C4200000 */  lwc1       $fv0, %lo(D_800D2F00)($at)
/* 10 80001010 3C028010 */  lui        $v0, %hi(D_800D2F08)
/* 14 80001014 C4400000 */  lwc1       $fv1, %lo(D_800D2F08)($v0)
/* 18 80001018 3C018010 */  lui        $at, %hi(D_800D2F10)
/* 1C 8000101C C4200000 */  lwc1       $fv0, %lo(D_800D2F10)($at)
/* 20 80001020 D4220000 */  ldc1       $ft3, %lo(D_800D2F10)($at)
'''
HALF = struct.pack('>d', 0.5)
HUNDRED = struct.pack('>f', 100.0)


class Symbols:
    def resolve(self, name):
        return 0x800D2F00 if name == 'D_800D2F00' else None


class Net:
    def __init__(self, rom=True):
        self.rom = rom
        self.asked = []

    def is_rom(self, proj):
        return self.rom

    def symbols(self, project_id):
        return Symbols()

    def reader(self, proj, fn):
        def read(address, size):
            self.asked.append((address, size))
            return {0x800D2EF0: HALF, 0x800D2F00: HUNDRED}.get(address)
        return read

    def fork_slug(self, slug):
        return None


PROJECT = {'arch': 'mips', 'big_endian': True, 'id': 1, 'slug': 's'}


def test_only_symbols_loaded_through_at_as_one_width_are_constants():
    assert pool_words.loads(ASM) == {'D_800D2EF0': 8, 'D_800D2F00': 4}  # $v0 is a real global, a mixed one is not a constant


def test_the_words_are_the_game_bytes_in_the_games_order_and_width():
    net = Net()
    words = pool_words.read(net, PROJECT, {'asm': ASM})
    assert words == {'D_800D2EF0': 0x3FE0000000000000, 'D_800D2F00': 0x42C80000}
    assert (0x800D2EF0, 8) in net.asked and (0x800D2F00, 4) in net.asked  # D_ names carry their address
    little = pool_words.read(Net(), {**PROJECT, 'big_endian': False}, {'asm': ASM})
    assert little['D_800D2F00'] == int.from_bytes(HUNDRED, 'little')


def test_a_project_that_is_not_a_rom_or_not_mips_has_no_words():
    assert pool_words.read(Net(rom=False), PROJECT, {'asm': ASM}) == {}
    assert pool_words.read(Net(), {**PROJECT, 'arch': 'ppc'}, {'asm': ASM}) == {}


def test_the_profile_scope_carries_the_words_and_the_draft_pass_takes_them_out_again(monkeypatch):
    monkeypatch.setattr('decompnet.project.compiler_for', lambda cfg, vram, fn: {'toolchain': 'x'})
    monkeypatch.setattr(promoted_entry_repairs, 'profile_scope', lambda arch, profile: {'language': 'c', 'int_bits': 32})
    net = Net()
    net.cfg = lambda proj: {}
    scope = promoted_entry_repairs.scope(net, PROJECT, {'asm': ASM, 'vram': 0x80001000})
    assert scope['language'] == 'c' and scope['words']['D_800D2F00'] == 0x42C80000
    assert draft_repairs._split_words(scope, None) == ({'language': 'c', 'int_bits': 32}, scope['words'])
    assert draft_repairs._split_words({'language': 'c'}, None) == ({'language': 'c'}, None)
    assert draft_repairs._split_words(scope, {'x': 1})[1] == {'x': 1}


def test_a_declared_module_that_needs_words_gets_them_from_the_draft_pass(monkeypatch):
    seen = []

    def variants(source, name, words=None):
        seen.append(words)
        return []

    variants.__module__ = 'decomprepair.probe_words'
    probe = registry.Repair('probe_words', ('probe_words',), variants, ('source', 'name', 'words'), 1, ('draft', 'local'), ())
    monkeypatch.setattr(registry, 'all', lambda: [probe])
    draft_repairs.variants('void f(void)\n{\n    g();\n}\n', 'f', 'mips', asm='nop', limit=None,
                           entry_scope={'language': 'c', 'int_bits': 32, 'words': {'D_1': 5}})
    assert seen and seen[0] == {'D_1': 5}
    context = library_registry.draft_context('s', 'f', '', 'mips', 'nop', (), {'D_1': 5})
    assert context['words'] == {'D_1': 5} and 'words' not in library_registry.draft_context('s', 'f', '', 'mips', 'nop', ())
    local = library_registry.local_calls(context)
    assert local == [('decomprepair.registry.run', ('probe_words',), {'source': 's', 'name': 'f', 'words': {'D_1': 5}})]

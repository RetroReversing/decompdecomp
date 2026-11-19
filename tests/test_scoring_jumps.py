"""A branch is compared by where it goes from itself, not by the address it was linked at.

Two objects can hold the same function at different offsets — a delinked target keeps each function in its own
section, a compiled one may keep them together — and then every branch of a 100% function would read as different.
"""

from decompnet.scoring import _relative_jump


def test_the_same_branch_agrees_across_two_offsets():
    """The real case: one function at 0x648 and the same one at 0x1b8, branches to the same places."""
    assert _relative_jump("0x664", 0x648) == _relative_jump("0x1d4", 0x1b8) == ".L1c"
    assert _relative_jump("0x640", 0x658) == _relative_jump("0x1b0", 0x1c8) == ".L-18"


def test_a_resolved_address_and_the_symbol_that_names_it_are_equal():
    """The game's `lis r10, 0x9200` and a candidate's `lis r10, DAT_92005640@h` are one instruction: the symbol
    names its own address, so both say 0x9200. This was a real function's whole remaining difference (99.8%)."""
    from decompnet.scoring import Insn, _classify

    resolved = Insn(0, "lis", "r10, 0x9200", "lis r10, 0x9200")
    symbolic = Insn(0, "lis", "r10, <DAT_92005640@h>", "lis r10, <DAT_92005640@h>")
    assert _classify(resolved, symbolic) == (0, " ")

    low = Insn(4, "addi", "r9, r10, 0x5640", "addi r9, r10, 0x5640")
    low_symbolic = Insn(4, "addi", "r9, r10, <DAT_92005640@l>", "addi r9, r10, <DAT_92005640@l>")
    assert _classify(low, low_symbolic) == (0, " ")


def test_a_near_miss_address_is_still_a_difference():
    """Only the symbol's own value counts; one bit off is still a different operand."""
    from decompnet.scoring import Insn, _classify

    resolved = Insn(0, "lis", "r10, 0x9201", "lis r10, 0x9201")
    symbolic = Insn(0, "lis", "r10, <DAT_92005640@h>", "lis r10, <DAT_92005640@h>")
    assert _classify(resolved, symbolic)[0] > 0


def test_a_literal_where_the_game_references_its_data_still_differs():
    """The equality is one-way: the game's address against YOUR relocation. A bare literal on your side is a real
    difference (the game reaches its data, you don't), so it must not be smoothed over."""
    from decompnet.scoring import Insn, _classify

    target_symbolic = Insn(0, "lis", "r10, <DAT_92005640@h>", "lis r10, <DAT_92005640@h>")
    candidate_literal = Insn(0, "lis", "r10, 0x9200", "lis r10, 0x9200")
    assert _classify(target_symbolic, candidate_literal)[0] > 0


def test_objdiff_rows_that_spell_the_same_address_agree():
    """objdiff's own rows are normalised the same way, since its percent is what the app scores."""
    from decompnet.forks.objdiff import _rows_agree

    resolved = {"instruction": {"formatted": "lis r10, 0x9200"}}
    symbolic = {"instruction": {"formatted": "lis r10, DAT_92005640@h"}}
    assert _rows_agree(resolved, symbolic) is True

    assert _rows_agree({"instruction": {"formatted": "lis r10, 0x9201"}}, symbolic) is False
    assert _rows_agree({"instruction": {"formatted": "addi r10, r10, 0x1"}}, symbolic) is False


def test_a_branch_that_goes_elsewhere_still_differs():
    """Normalising the base must not hide a real difference in where a branch goes."""
    assert _relative_jump("0x664", 0x648) != _relative_jump("0x668", 0x648)
    assert _relative_jump("0x1c", 0x0) != _relative_jump("0x20", 0x0)


def test_a_backwards_branch_reads_by_its_distance():
    """Left as a signed distance so it can't be confused with a forward branch of the same magnitude."""
    assert _relative_jump("0x48", 0x50) == ".L-8"
    assert _relative_jump("0x58", 0x50) == ".L8"


def test_a_half_printed_signed_is_the_same_operand():
    """A half is 16 bits and the disassembly prints displacements and immediates signed: the game's
    `addi r11, r11, -0x59c0` is the low half 0xA640 that `DAT_9200A640@l` names. Read unsigned only, every low half
    of 0x8000 or more stayed a difference, and identical code scored below 100%."""
    from decompnet.scoring import Insn, _classify

    signed = Insn(4, "addi", "r11, r11, -0x59c0", "addi r11, r11, -0x59c0")
    symbolic = Insn(4, "addi", "r11, r11, <DAT_9200A640@l>", "addi r11, r11, <DAT_9200A640@l>")
    assert _classify(signed, symbolic) == (0, " ")

    load = Insn(8, "lwz", "r3, -0x59c0(r11)", "lwz r3, -0x59c0(r11)")
    load_symbolic = Insn(8, "lwz", "r3, <DAT_9200A640@l>(r11)", "lwz r3, <DAT_9200A640@l>(r11)")
    assert _classify(load, load_symbolic) == (0, " ")

    high = Insn(0, "lis", "r11, -0x6dff", "lis r11, -0x6dff")  # 0x9201, signed: @ha of 0x9200A640
    high_symbolic = Insn(0, "lis", "r11, <DAT_9200A640@ha>", "lis r11, <DAT_9200A640@ha>")
    assert _classify(high, high_symbolic) == (0, " ")


def test_a_signed_half_one_off_still_differs():
    from decompnet.scoring import Insn, _classify

    symbolic = Insn(4, "addi", "r11, r11, <DAT_9200A640@l>", "addi r11, r11, <DAT_9200A640@l>")
    assert _classify(Insn(4, "addi", "r11, r11, -0x59bf", "addi r11, r11, -0x59bf"), symbolic)[0] > 0
    # a whole address is not a half: it is never read signed
    whole = Insn(0, "li", "r3, -0x6dff5a00", "li r3, -0x6dff5a00")
    assert _classify(whole, Insn(0, "li", "r3, <DAT_9200A600>", "li r3, <DAT_9200A600>"))[0] > 0


def test_objdiff_rows_with_a_signed_half_agree():
    from decompnet.forks.objdiff import _rows_agree

    assert _rows_agree({"instruction": {"formatted": "addi r11, r11, -0x59c0"}},
                       {"instruction": {"formatted": "addi r11, r11, DAT_9200A640@l"}}) is True
    assert _rows_agree({"instruction": {"formatted": "lwz r3, -0x59c0(r11)"}},
                       {"instruction": {"formatted": "lwz r3, DAT_9200A640@l(r11)"}}) is True

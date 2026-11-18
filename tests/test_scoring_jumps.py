"""A branch is compared by where it goes from itself, not by the address it was linked at.

Two objects can hold the same function at different offsets — a delinked target keeps each function in its own
section, a compiled one may keep them together — and then every branch of a 100% function would read as different.
"""

from decompnet.scoring import _relative_jump


def test_the_same_branch_agrees_across_two_offsets():
    """The real case: one function at 0x648 and the same one at 0x1b8, branches to the same places."""
    assert _relative_jump("0x664", 0x648) == _relative_jump("0x1d4", 0x1b8) == ".L1c"
    assert _relative_jump("0x640", 0x658) == _relative_jump("0x1b0", 0x1c8) == ".L-18"


def test_a_branch_that_goes_elsewhere_still_differs():
    """Normalising the base must not hide a real difference in where a branch goes."""
    assert _relative_jump("0x664", 0x648) != _relative_jump("0x668", 0x648)
    assert _relative_jump("0x1c", 0x0) != _relative_jump("0x20", 0x0)


def test_a_backwards_branch_reads_by_its_distance():
    """Left as a signed distance so it can't be confused with a forward branch of the same magnitude."""
    assert _relative_jump("0x48", 0x50) == ".L-8"
    assert _relative_jump("0x58", 0x50) == ".L8"

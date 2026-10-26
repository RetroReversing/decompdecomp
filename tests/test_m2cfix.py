"""m2c's usual mistakes undone (m2cfix.py): the drafts here are made up, small and about nothing in particular."""

from decompnet import m2cfix

CALL_ASM = """glabel func_80001000
addiu $sp, $sp, -0x18
sw $ra, 0x10($sp)
jal func_80002000
addiu $a1, $zero, 0x1
lw $ra, 0x10($sp)
jr $ra
addiu $sp, $sp, 0x18
endlabel func_80001000
"""


def test_the_macros_are_written_out_innermost_first():
    src = ("#ifndef M2C_DEFINES\n#define M2C_DEFINES\ntypedef s32 M2C_UNK;\n#endif\n"
           "void f(void *p) { M2C_FIELD(M2C_FIELD(p, void **, 4), s32 *, 8) = 1; }")
    out = m2cfix.expand_macros(src)
    assert "M2C_" not in out
    assert "(*(s32 *)((char *)((*(void **)((char *)(p) + 4))) + 8)) = 1;" in out


def test_a_call_passes_on_the_argument_registers_the_code_leaves_alone():
    """The code sets only $a1 for the call: $a0 is the caller's own, passed on, and the function takes it."""
    draft = "s32 func_80002000(s32);\nvoid func_80001000(void) {\n    func_80002000(1);\n}\n"
    out = m2cfix.refine_draft(draft, "func_80001000", CALL_ASM)
    assert "void func_80001000(s32 arg0)" in out and "func_80002000(arg0, 1);" in out
    assert "func_80002000();" in out  # (its prototype takes whatever the code passes)


def test_nothing_is_added_to_a_call_that_has_its_arguments():
    draft = "void func_80001000(s32 arg0) {\n    func_80002000(arg0, 1);\n}\n"
    out = m2cfix.refine_draft(draft, "func_80001000", CALL_ASM)
    assert "func_80002000(arg0, 1);" in out and "arg0, arg0" not in out


def test_every_argument_up_to_the_highest_is_declared():
    draft = "s32 func_80001000(s32 arg2) {\n    return arg2;\n}\n"
    out = m2cfix.refine_draft(draft, "func_80001000", "glabel func_80001000\njr $ra\naddu $v0, $a2, $zero\n")
    assert "func_80001000(s32 arg0, s32 arg1, s32 arg2)" in out  # (m2c named only the one it reads)
    draft = "void func_80001000(void) {\n    func_80002000(1);\n}\n"
    asm = "glabel f\njal func_80002000\naddiu $a2, $zero, 1\n"
    assert "func_80001000(s32 arg0, s32 arg1)" in m2cfix.refine_draft(draft, "func_80001000", asm)


def test_a_global_at_an_offset_is_a_variable_of_its_own():
    draft = "extern M2C_UNK D_80100000;\nvoid func_80001000(void) {\n    M2C_FIELD(&D_80100000, s16 *, 0x10) = 3;\n}\n"
    out = m2cfix.refine_draft(draft, "func_80001000", "glabel func_80001000\njr $ra\nnop\n")
    assert "extern short D_80100010;" in out and "D_80100010 = 3;" in out


def test_colour_bytes_are_stored_through_unsigned_fields():
    draft = "void func_80001000(void *arg0) {\n    M2C_FIELD(arg0, s8 *, 0x90) = 0xFE;\n    M2C_FIELD(arg0, s8 *, 0x91) = 0x1E;\n}\n"
    out = m2cfix.refine_draft(draft, "func_80001000", "glabel func_80001000\njr $ra\nnop\n")
    assert "M2C_FIELD(arg0, u8 *, 0x90) = 0xFE;" in out and "M2C_FIELD(arg0, s8 *, 0x91) = 0x1E;" in out


def test_x_equals_x_or_c_is_a_compound_assignment():
    draft = "void func_80001000(void *arg0) {\n    M2C_FIELD(arg0, s32 *, 0x80) = (s32) (M2C_FIELD(arg0, s32 *, 0x80) | 4);\n}\n"
    out = m2cfix.refine_draft(draft, "func_80001000", "glabel func_80001000\njr $ra\nnop\n")
    assert "M2C_FIELD(arg0, s32 *, 0x80) |= 4;" in out


def test_a_pointer_stepped_by_a_byte_count_is_a_byte_pointer():
    draft = ("void func_80001000(s16 *arg0) {\n    s16 *var_a0;\n\n    var_a0 = arg0;\n    *var_a0 = 0;\n"
             "    var_a0 += 0x24;\n}\n")
    out = m2cfix.refine_draft(draft, "func_80001000", "glabel func_80001000\njr $ra\nnop\n")
    assert "char *var_a0;" in out and "*(s16 *)var_a0 = 0;" in out and "var_a0 += 0x24;" in out


def test_a_header_it_cant_read_gives_no_refined_draft():
    assert m2cfix.refine_draft("void other(void) {\n}\n", "func_80001000", "glabel f\njr $ra\nnop\n") is None


def test_the_permuters_inline_helper_is_static():
    src = "inline char *inline_fn(void *a)\n{\n  return (char *) a;\n}\n\nvoid f(void *a) { inline_fn(a); }\n"
    out = m2cfix.static_helpers(src)
    assert out.startswith("static inline char *inline_fn") and m2cfix.static_helpers(out) == out

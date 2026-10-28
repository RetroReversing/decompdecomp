"""Advisory search weights from the ordinary judge's mismatches; all mutations remain available."""
import re

BOOSTS = {
    'stack': ('perm_reorder_decls', 'perm_temp_for_expr', 'perm_split_assignment', 'perm_randomize_internal_type'),
    'register': ('perm_reorder_decls', 'perm_reorder_stmts', 'perm_expand_expr', 'perm_temp_for_expr', 'perm_commutative'),
    'call': ('perm_randomize_function_type', 'perm_randomize_external_type', 'perm_refer_to_var'),
    'constant': ('perm_float_literal', 'perm_cast_simple', 'perm_add_mask', 'perm_xor_zero'),
    'control': ('perm_condition', 'perm_reorder_stmts', 'perm_ins_block', 'perm_empty_stmt'),
}


def classify(rows):
    changed = [row for row in rows if row and row[0] != ' ']
    markers = {row[0] for row in changed}
    text = ' '.join(str(cell) for row in changed for cell in row[1:])
    if re.search(r'\b(?:jalr?|callq?|bl|bctrl)\b', text):
        return 'call'
    if 's' in markers:
        return 'stack'
    if markers and markers <= {'r'}:
        return 'register'
    if 'i' in markers or re.search(r'\b(?:lui|li|ldc1|lwc1|movabs)\b', text):
        return 'constant'
    return 'control' if markers & {'<', '>', '|', 'o'} else ''


def boosts(rows):
    # Multiply existing upstream weights, never invent unknown mutations or remove the compiler's defaults.
    return dict.fromkeys(BOOSTS.get(classify(rows), ()), 3)

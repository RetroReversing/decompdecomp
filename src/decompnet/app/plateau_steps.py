"""One more repair step from a structural rewrite that didn't improve on its own: bounded, never adopted alone.

A deep pass keeps only steps that lower the score. Some exact rewrites need two at once: an N64 function's loop was
exact only with a pointer local taken out and the loop restructured, and each alone scored the same as before; another
needed a missing argument (471, against the saved 461) and then its symbol held in a local (exact). Kept as they were,
the pass dropped the first step and never reached the second. A structural rewrite that comes out within MARGIN of its
parent's score, with different output, gets one burst of follow-up proposals from its own C, queued first. Only the
judge's normal improvement test adopts anything: a pair that doesn't beat the saved best is discarded like any trial.
"""
from __future__ import annotations

# (the plateau is the parent's score up to this much worse: 471 against 461 is 2%)
MARGIN = 0.05
BURSTS = 4
CHILDREN = 6

# Rewrites that change a function's structure (control flow, records, pointers, calls, loops) rather than one
# register or one statement's order: these are the steps whose effect another structural step can unlock.
STRUCTURAL = (
    'goto_backward_loop', 'goto_forward_else', 'goto_shared_tail', 'goto_loop', 'goto_search_loop',
    'shared_step_loop', 'found_inside_loop', 'break_search_loop', 'counted_call_loop', 'masked_counter_loop',
    'u16_counter_loop', 'short_counter_loop', 'do_constant_body', 'duplicate_return_node', 'nested_guard',
    'guard_inversion', 'switch_case_order', 'switch_default_in_if', 'switch_range', 'compare_tree_switch',
    'split_case_labels', 'constant_exit_return', 'flag_returns', 'returned_flag', 'negated_flag_return',
    'conditional_ternary', 'comma_flag_condition', 'call_into_arms',
    'symbol_address_local', 'stored_pointer_read', 'global_pointer_local', 'early_base_local', 'offset_base_local',
    'operand_temporary', 'address_temporary', 'loop_address_inline', 'field_load_hoist', 'assignment_at_use',
    'load_before_store', 'deferred_stores', 'word_block_copy', 'float_record_copy', 'stack_argument_record',
    'inferred_record_layout', 'field_width', 'symbol_access_width', 'unknown_table_index', 'pointer_byte_step',
    'target_call_arity', 'passthrough_argument', 'saved_register_argument', 'leading_parameters', 'float_prototype',
    'float_register_parameter', 'parameter_mask_hoist', 'parameter_mask_drop', 'narrow_widening',
    'unsigned_narrow_local', 'delay_slot_duplicate', 'target_constant',
)


def plateau(family: str, parent_score, score, parent_output=None, output=None) -> bool:
    """A structural step that neither improved nor fell more than MARGIN behind its parent, and changed the code."""
    if family not in STRUCTURAL or parent_score is None or score is None or score <= 0 or parent_score <= 0:
        return False
    if parent_output is not None and output is not None and parent_output == output:
        return False  # (the same instructions: nothing for a second step to build on)
    return parent_score <= score <= parent_score * (1 + MARGIN)


def select(proposals, family: str, seen) -> list[tuple[str, str]]:
    """Up to CHILDREN follow-ups: other structural families first (they are the ones that chain), then the rest; a
    family may follow itself (a second proposal of the same rewrite held a symbol in a local)."""
    fresh = [(f, c) for f, c in proposals if c not in seen]
    ranked = sorted(range(len(fresh)), key=lambda i: (fresh[i][0] not in STRUCTURAL, fresh[i][0] == family, i))
    return [fresh[i] for i in ranked[:CHILDREN]]

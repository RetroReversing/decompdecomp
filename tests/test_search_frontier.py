from decompnet.app import permuter_focus, repair_ranking, search_frontier


def test_known_relocation_rendering_does_not_hide_register_misses():
    rows = [('i','lw $a0, -0x7ce0($a0)','lw $a0, %lo(D_800C8320)($a0)'),
            ('r','andi $s0, $s0, 0xffff','andi $v1, $s0, 0xffff')]
    assert permuter_focus.classify(rows) == 'register'
    assert rows[0][0] == 'i'  # the real diff and numeric score remain untouched
    assert repair_ranking.order(['constant_local','masked_value_reuse'],rows)[0] == 'masked_value_reuse'
    assert search_frontier.structure(rows) == 1


def test_unknown_symbols_wrong_bits_registers_addends_and_hi_values_remain_mismatches():
    for target, candidate in [
        ('lw $a0, -0x7ce4($a0)','lw $a0, %lo(D_800C8320)($a0)'),
        ('lw $a0, -0x7ce0($a0)','lw $a1, %lo(D_800C8320)($a0)'),
        ('lw $a0, -0x7ce0($a0)','lw $a0, %lo(renamed)($a0)'),
        ('lw $a0, -0x7ce0($a0)','lw $a0, %lo(D_800C8320+4)($a0)'),
        ('lui $a0, 0x800c','lui $a0, %hi(D_800C8320)'),
    ]:
        assert permuter_focus.normalized([('i',target,candidate)])[0][0] == 'i'
    assert permuter_focus.normalized([('i','lui $a0, 0x800d','lui $a0, %hi(D_800C8320)')])[0][0] == ' '
    renamed=[('i','lw $a0, -0x7ce0($a0)','lw $a0, %lo(renamed)($a0)')]
    assert permuter_focus.normalized(renamed,lambda name:0x800c8320)[0][0] == ' '


def test_frontier_keeps_numeric_best_and_distinct_structural_alternatives_with_bounded_memory():
    frontier = search_frontier.Frontier()
    frontier.offer('numeric', 10, [('r','r1','r2')]*7)
    frontier.offer('moved', 15, [('o','lw','')])
    frontier.offer('register', 12, [('r','s0','v1')]*2)
    for i in range(30):
        frontier.offer('bad'+str(i), 200+i, [('|','target','candidate'+str(i))]*30)
    assert len(frontier.nodes) <= search_frontier.WIDTH
    assert {'numeric','moved','register'} <= {n['source'] for n in frontier.nodes}
    assert frontier.choose(1,'fallback') != 'fallback'

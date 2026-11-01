from decompnet.app import inferred_layout_repairs as repair


def test_consistent_repeated_accesses_make_a_candidate_local_record():
    source = 'void f(void *p) { *(int*)((char*)p+20) = 1; use(*((int *) (((char *) p) + 20))); *(short*)((char*)p+24) = 2; }'
    variants = repair.variants(source, 'f', 'mips')
    assert len(variants) == 1
    text = variants[0][1]
    assert 'unsigned char pad_0[20]; int field_14; short field_18;' in text
    assert text.count('((__repair_record_p*)p)->field_14') == 2
    assert 'void f(void *p)' in text
    assert 'typedef struct' in text


def test_conflicting_overlapping_misaligned_or_dynamic_layouts_are_rejected():
    source = 'void f(void *p) { use(*(int*)((char*)p+20)); use(*(int*)((char*)p+20)); use(*(short*)((char*)p+24)); }'
    for invalid in (source.replace('short', 'char').replace('p+24','p+21'),
                    source.replace('p+24','p+25'), source.replace('p+24','p+offset'),
                    source.replace('*(short*)','*(double*)'), source.replace('void *p','volatile void *p')):
        assert not repair.variants(invalid, 'f', 'mips')
    assert not repair.variants(source, 'f', 'mips64')
    assert not repair.variants(source.replace('p+20', 'p+24'), 'f', 'mips')


def test_comments_strings_and_other_functions_are_not_inferred():
    source = 'void g(void *p) { use(*(int*)((char*)p+20)); use(*(int*)((char*)p+20)); use(*(int*)((char*)p+24)); }\nvoid f(void) { puts("*(int*)((char*)p+24)"); }'
    assert not repair.variants(source, 'f', 'mips')

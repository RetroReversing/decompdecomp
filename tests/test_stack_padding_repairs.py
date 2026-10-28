import pytest

from decompnet.app import draft_repairs, local_experiments, stack_padding_repairs


def test_unreferenced_integer_padding_can_be_removed_or_halved():
    source = 'int f(void) { int pad[4]; return work(); }'
    choices = stack_padding_repairs.variants(source, 'f')
    assert len(choices) == 2
    assert any('pad[' not in s for s in choices)
    assert any('int pad[2];' in s for s in choices)
    assert all('return work();' in s for s in choices)


@pytest.mark.parametrize('declaration,tail', [
    ('volatile int pad[4];', ''), ('const int pad[4];', ''),
    ('int pad[4]={0};', ''), ('int pad[4];', 'use(pad);'),
    ('int pad[4];', 'use(sizeof(pad));'), ('int pad[4];', 'use(&pad);'),
    ('struct { int pad[4]; } entry;', ''), ('int padding[65];', ''),
])
def test_padding_repairs_preserve_observable_storage_and_aggregate_fields(declaration, tail):
    assert not stack_padding_repairs.variants('void f(void) {'+declaration+tail+'}', 'f')


def test_padding_repairs_keep_other_definitions_and_ignore_comments():
    prefix = 'void other(void) { int pad[4]; }\n'
    source = prefix+'void f(void) { int pad[2]; /* pad is unused */ work(); }'
    choices = stack_padding_repairs.variants(source, 'f')
    assert choices and all(s.startswith(prefix) for s in choices)
    assert all('/* pad is unused */' in s for s in choices)
    assert not stack_padding_repairs.variants('void f(void) {\n#define N 4\nint pad[N];}', 'f')


def test_padding_repairs_reach_both_local_search_entry_points():
    labels = {}
    choices = draft_repairs.variants('void f(void) { int pad[4]; work(); }', 'f', 'mips', labels=labels)
    assert any(labels[s] == 'stack_padding' for s in choices)
    assert 'stack_padding' in local_experiments.FAMILIES

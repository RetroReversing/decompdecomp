from decompnet.app.source_next_steps import prototype_hints, recommendations, untested_profiles


def test_helper_float_argument_and_unsigned_return_disagreements_are_review_leads():
    hints = prototype_hints('unsigned material(void *p); int sound(int id, float volume);',
                            'void f(void){material(p);sound(1,0);}',
                            'int material(void *p); void sound(int id, int volume);')
    assert len(hints) == 2 and 'float' in hints[1]
    assert all('declarations were not changed' in h for h in hints)
    assert not prototype_hints('int sound(int a);','void f(void){sound(1);}','extern int sound(int a);')


def test_revision_guidance_preserves_better_draft_and_never_patches_control_flow():
    rows = {'missing_calls':{'save':1}, 'extra_calls':{'write':1},
            'target':{'branches':['beq'],'exits':1},'candidate':{'branches':[],'exits':1}}
    hints = recommendations('void f(void){write();}', 'void f(void){save();}', rows)
    assert 'saved-only save' in hints[0] and 'preserve the better draft' in hints[0]
    assert 'No branches were patched' in hints[1]
    assert not recommendations('void f(void){}','void f(void){}',{})


def test_profile_budget_advances_past_cached_choices_and_deduplicates():
    choices = [['-O2'],['-g'],['-g'],['-O1'],['-O3'],['-O0']]
    assert untested_profiles(choices,['-O2'],tuple,lambda k:k==('-g',)) == [['-O1'],['-O3']]
    assert untested_profiles(choices,['-O2'],tuple,lambda k:k in {('-g',),('-O1',),('-O3',)}) == [['-O0']]


def test_register_only_advice_checks_prototypes_before_body_rewrites():
    assert 'float zero arguments' in recommendations('','',{'register_only':True})[0]


def test_parameter_names_do_not_create_false_signature_disagreements():
    assert not prototype_hints('unsigned sound(int id, float volume);',
                               'void f(void){sound(1,0);}',
                               'extern unsigned sound(int number, float level);')

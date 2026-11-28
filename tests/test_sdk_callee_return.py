"""SDK return contracts are used only with a full-byte identity proof."""
import hashlib

import pytest

from decompnet import consoles
from decompnet.app import capture_followup_repairs, sdk_assembly
from decompnet.consoles.n64 import sdk_assembly as n64_sdk
from tests.test_claims import _game


def test_sdk_identity_exposes_the_reference_return_type(monkeypatch):
    code = bytes([1]) * 112
    monkeypatch.setitem(n64_sdk.KNOWN_C, hashlib.sha256(code).hexdigest(), ('__vsVol', 112, 'test'))
    assert n64_sdk.identify(code)['return_type'] == 's16'
    monkeypatch.setitem(n64_sdk.KNOWN_C, hashlib.sha256(code).hexdigest(), ('other', 112, 'test'))
    assert 'return_type' not in n64_sdk.identify(code)


@pytest.mark.parametrize('proof,expected', [
    ({'name': '__vsVol', 'return_type': 's16'}, 's16'),
    ({'name': 'other', 'return_type': 's16'}, 's32'),
    ({'name': '__vsVol', 'return_type': 'void'}, 's32'),
    (None, 's32'),
])
def test_generation_uses_only_exact_matching_sdk_return(tmp_path, monkeypatch, proof, expected):
    net, _ = _game(tmp_path, [('f', 4, 'open'), ('__vsVol', 4, 'matched')])
    original = 's32 __vsVol(void *a, void *b) { return 0; }'
    with net.store.tx() as db:
        db.execute("UPDATE functions SET target=?, matched_source=? WHERE name='__vsVol'", (b'code', original))
    proj = net._project('game')
    fn = net._function('f', 'game')
    monkeypatch.setattr(consoles, 'of', lambda cfg: object())
    monkeypatch.setattr(sdk_assembly, 'known_proof', lambda console, project, target: proof)
    evidence = capture_followup_repairs.callee_evidence(net, proj, fn, 'int f(void) { return __vsVol(0, 0); }')
    assert evidence.startswith(expected + ' __vsVol(')
    assert net._function('__vsVol', 'game')['matched_source'] == original

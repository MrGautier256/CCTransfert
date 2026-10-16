import json

from cctransfert.jsonio import RawNumber, dumps_js, loads_raw


def test_dumps_js_compact_et_utf8_brut():
    o = {"a": "é😀", "b": [1, None, True, False], "c": {"d": "x\"y\\z\n\t\x01"}}
    assert dumps_js(o) == '{"a":"é😀","b":[1,null,true,false],"c":{"d":"x\\"y\\\\z\\n\\t\\u0001"}}'


def test_substitut_isole_echappe_comme_js():
    o = loads_raw('{"x":"\\ud83d"}')
    assert dumps_js(o) == '{"x":"\\ud83d"}'


def test_nombres_conserves_tels_quels():
    src = '{"a":1.0,"b":1e21,"c":12345678901234567890,"d":-0.5,"e":2.50}'
    o = loads_raw(src)
    assert isinstance(o["a"], RawNumber)
    assert dumps_js(o) == src


def test_aller_retour_lignes_style_claude_code():
    lines = [
        '{"type":"user","message":{"role":"user","content":"réponds juste ok\\n/tmp/x"},"uuid":"u","timestamp":"2026-10-16T09:38:56.489Z"}',
        '{"type":"cost-state","totalCostUSD":0.0021176200000000006,"startTime":1791462392393,"modelUsage":{}}',
        '{"type":"x","s":"guillemets \\" antislash \\\\ et contrôle \\u001b et   brut"}',
    ]
    for ln in lines:
        assert dumps_js(loads_raw(ln)) == ln
        assert json.loads(dumps_js(loads_raw(ln))) == json.loads(ln)

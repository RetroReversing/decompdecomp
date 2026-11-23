"""The constants a function loads through `$at`, read from the game's data for the library's literal families.

m2c names a float or double the game keeps with its data by its address (`extern f64 D_800D2EF0;`). The library's
`float_literal_global` and `double_literal_global` write such a symbol as the literal it holds, which the game's compiler
loaded from its pool (`lui $at, %hi(S)` then `lwc1`/`ldc1 ... %lo(S)($at)`) and not through an address register: that is
the difference the saved best keeps as register and order rows, and no spelling of the global fixes it (a 315-instruction
tile-rectangle builder: 5980 against 855 with `0.5` for the global). The value is game data the C cannot show, so the
caller reads it: this is the caller. `read` returns {symbol: the integer its bytes hold, in the game's byte order}, four
bytes for a symbol loaded with `lwc1`, eight for one loaded with `ldc1`, for every symbol the function loads that way; it
is what the registry calls `words`. ROM projects on MIPS only; any symbol it cannot place or read is left out."""
import re

LIMIT = 16
_LOAD = re.compile(r'\b(?P<op>lwc1|ldc1)\s+\$\w+\s*,\s*%lo\(\s*(?P<symbol>[A-Za-z_]\w*)\s*\)\s*\(\s*\$at\s*\)')
_NAMED = re.compile(r'D_([0-9A-Fa-f]{8})\Z')


def loads(asm):
    """{symbol: bytes loaded} for each symbol the assembly loads only as a float (4) or a double (8) through `$at`."""
    found = {}
    for hit in _LOAD.finditer(re.sub(r'/\*.*?\*/', ' ', asm or '')):
        size = 4 if hit['op'] == 'lwc1' else 8
        if found.setdefault(hit['symbol'], size) != size:
            found[hit['symbol']] = 0  # loaded both ways: not a constant
    return {symbol: size for symbol, size in found.items() if size}


def read(net, proj, fn):
    if dict(proj).get('arch') != 'mips' or not getattr(net, 'is_rom', lambda p: False)(proj):
        return {}
    wanted = loads(dict(fn).get('asm') or '')
    if not wanted:
        return {}
    order = 'big' if proj['big_endian'] else 'little'
    words = {}
    try:
        symbols = net.symbols(proj['id'])
        reader = net.reader(proj, fn)
        for symbol, size in sorted(wanted.items())[:LIMIT]:
            address = symbols.resolve(symbol)
            if address is None and (named := _NAMED.match(symbol)):
                address = int(named[1], 16)
            data = reader(address, size) if address is not None else None
            if data is not None and len(data) == size:
                words[symbol] = int.from_bytes(data, order)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}
    return words

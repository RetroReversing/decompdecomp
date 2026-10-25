"""Fixed GTE matrix transfers for PS1 C: callers choose operands, never instruction words.

These small register-transfer templates implement the documented matrix layout (nine signed halfwords,
then three translation words). They are compiler-provided intrinsics; submitted inline assembly stays forbidden.
"""


def _macro(name: str, operations: list[str], clobbers: list[int]) -> str:
    code = ''.join(operation + ';' for operation in operations).rstrip(';')
    lost = ', '.join(f'"${register}"' for register in clobbers)
    return f'#define {name}(matrix) __asm__ volatile ("{code}" : : "r"(matrix) : {lost})\n'


def header() -> str:
    rotation = ['lw $12, 0(%0)', 'lw $13, 4(%0)', 'ctc2 $12, $0', 'ctc2 $13, $1',
                'lw $12, 8(%0)', 'lw $13, 12(%0)', 'lw $14, 16(%0)',
                'ctc2 $12, $2', 'ctc2 $13, $3', 'ctc2 $14, $4']
    translation = ['lw $12, 20(%0)', 'lw $13, 24(%0)', 'ctc2 $12, $5',
                   'lw $14, 28(%0)', 'ctc2 $13, $6', 'ctc2 $14, $7']
    return ('#ifndef DECOMP_PS1_GTE\n#define DECOMP_PS1_GTE\n'
            + _macro('gte_SetRotMatrix', rotation, [12, 13, 14])
            + _macro('gte_SetTransMatrix', translation, [12, 13, 14]) + '#endif\n')

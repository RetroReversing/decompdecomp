"""Actionable compiler diagnostics without modifying declarations or guessing types."""
import re


def hints(text, source):
    if not any(marker in text for marker in ('COMPILE ERROR','BUILD ERROR')):
        return ''
    advice = []
    if re.search(r'for\s*\(\s*(?:int|unsigned|s32|u32|size_t)\s+\w+', source):
        advice.append('This candidate declares a loop variable inside for(...). For a C89 compiler, declare it at the start of its block, then assign it in for(...).')
    if not advice and 'parse error' in text and re.search(r'(?m)^\s*(?:int|s32|u32|float|double|char|unsigned)\s+\w+', source):
        advice.append('Check C89 declaration placement: declarations must precede statements in each block. Also check the first reported line for a missing delimiter; later errors may be cascades.')
    names = list(dict.fromkeys(re.findall(r"[`‘']([A-Za-z_]\w*)['’] undeclared", text) + re.findall(r"(?:undeclared identifier|unknown type name) ['‘]([A-Za-z_]\w*)['’]", text)))[:3]
    if names:
        advice.append('Read get_context(function=...) and get_function(section="declarations") for '+', '.join(names)+'. Reuse proven declarations; do not invent symbol addresses or types.')
    if 'conflicting types' in text or 'redefinition' in text:
        advice.append('Compare the candidate declarations with get_context(function=...). Remove or correct the candidate-local duplicate; do not change the shared header just to silence this error.')
    if not advice:
        advice.append('Fix the first compiler error before tuning assembly. Get the function-specific context if its declarations are missing; follow-up errors can be cascades.')
    return '\nCompiler error guidance (advisory):\n'+'\n'.join('- '+line for line in advice[:3])

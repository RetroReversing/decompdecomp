"""Function naming state, separate from whether its C has matched."""

import json
import re
import time

_GENERATED = re.compile(r"_?(?:func|fn|FUN|sub)_[0-9a-fA-F]+(?:_[0-9a-fA-F]+)*(?:@.*)?$")


# Giving a symbol a compiler builtin's spelling can change calls or reject existing prototypes.
BUILTIN_NAMES = frozenset("memcpy memmove memset memcmp memchr strlen strchr strrchr strcpy strncpy strcmp "
                          "strncmp strcat strncat strstr sqrt sqrtf sin sinf cos cosf tan tanf fabs fabsf "
                          "abs labs malloc calloc realloc free printf sprintf snprintf puts putchar exit abort".split())


def generated(name: str) -> bool:
    return bool(_GENERATED.fullmatch(name))


def state(fn) -> dict:
    meta = json.loads(fn["meta"] or "{}")
    naming = meta.get("naming")
    return {"named": not generated(meta.get("real") or fn["name"]), "renamed": bool(naming), "naming": naming}


def record(db, fid, old, new, **provenance):
    row = db.execute("SELECT meta FROM functions WHERE id=?", (fid,)).fetchone()
    meta = json.loads(row["meta"] or "{}")
    previous = meta.get("naming", {})
    meta["naming"] = {"original_name": previous.get("original_name", old), "previous_name": old,
                      "name": new, "method": "rename", "at": time.time(), **provenance}
    db.execute("UPDATE functions SET meta=? WHERE id=?", (json.dumps(meta), fid))

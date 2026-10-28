# Free local source preparation and permutation

The permuter can use explicitly mapped local reference source before its usual deterministic and random searches.
This is optional: ROM-only projects keep working without it. Reference code from another version is a useful seed,
not proof that a function matches the retail game.

## Set it up

Open the game's **Settings → Local source preparation**:

1. Select the reference source folder and any SDK/include folders it needs. These folders are read-only inputs;
   the app does not execute reference build scripts or download SDKs.
2. Map each intended destination explicitly, one per line:
   `func_80001000 = update_objects @ objects.c`.
   The path is relative to the reference folder. Ambiguous/duplicate mappings are refused. Matching a name or a
   source location does not establish a match; no source-derived address is added to the destination symbol map.
3. Supply a declarations-only header with the necessary types and prototypes when possible. It must live inside a
   selected folder. Otherwise the app tries the mapped file's includes and declarations, or the current project
   header if there are no includes. Complex conditional includes, compiler extensions or initialized globals may
   need a smaller prepared header. An unresolved include is reported rather than guessed.
4. If retail and debug layouts differ, enter numeric overrides such as `OBJECT_LIMIT=128`. Existing definitions
   are replaced before dependent structs are compiled. Overrides do not infer the correct value or add a missing
   definition. Add any known global aliases separately: `gReferenceObjects = D_80010000`.
5. Enable preparation and **Save source settings**, then **Try preparation pass**. The button runs a background job
   with a progress bar and per-function diagnostics. The permuter also runs a preparation pass before its usual work.

A pass is bounded to eight compiler attempts and 20 seconds between attempts. One compiler invocation can finish
past that deadline, within the compiler's normal timeout. Unchanged trials (including compiler failures) are
cached. Another pass continues with untried mapped functions. Edited reference bodies, headers, mappings, macro
values or destination build inputs give those candidates another chance. The original function body keeps its
line breaks and original line number: IDO can schedule code differently when source line information changes.
Token replacements leave comments and string literals alone. A mapped `static` entry point is made externally
visible for the isolated compile, without shifting its lines. Its original body remains unchanged.

## What gets saved

For ROM projects, a prepared candidate is compiled and linked at the original address using the same judge as
agent-written C. A strictly better candidate gets a private per-function header and becomes the saved seed. Both
are committed together only if the target, current seed, header version, symbols and project config still agree
with the state used for judging. Failed or worse candidates leave the project unchanged. Already-matched functions
and shared headers are never rewritten by preparation. Extra emitted function bodies are refused because they
can collide in a whole-game export.

Imported projects use their existing build and objdiff judge. Their reference bodies must compile against that
build's headers; the app does not inject a reference header into their build setup. An incompatible header needs a
normal reviewed source/header edit in that project. This limitation is tracked in ROADMAP.md.

Local reference paths, mappings and macro overrides stay in machine settings and are not added to portable project
config or saved exports. **Accepted candidate C and its declarations are project work**: they are included when you
save/export that project. Review them before publishing a source-assisted decompilation.

## How the free search uses the result

- Deterministic repairs use measured mismatch evidence to rank applicable families: stack storage/declarations,
  register lifetimes, call signatures, constants or control flow. No family is excluded solely by this heuristic.
- Each random turn first tries up to eight cached local ordering probes: adjacent equal-constant stores and
  shift/add operand order. They share the turn deadline and normal judge, avoiding random drift for these cheap cases.
- Random decomp-permuter search boosts relevant existing compiler weights by three, retaining every other upstream
  weight. Focused batches alternate with upstream defaults so a misleading category cannot dominate the turn.
  The fallback rewrites use the same focus. This changes search order, never the verdict.
- A fresh baseline compile supplies evidence for the exact seed and context. An in-process bounded cache avoids
  compiling that same baseline every pass. Every generated candidate still goes through the normal judge.
- An improved external seed or changed header, effective compiler, target or symbols renews the existing bounded
  random-turn allowance. The permuter's own improvements do not renew it. Existing counters are adopted on first
  observation, and claimed functions wait until released. For imported projects, common build manifests and the
  generated per-function header are tracked too; an explicit new pass/session retries imported preparation trials.

This makes more work automatic without paid model calls. It cannot guarantee all remaining functions: version
changes, incorrect mappings, unknown data layouts, missing source, assembly and undiscovered compiler settings
still need investigation. An isolated function match is also not a replacement for a verified whole-image rebuild.

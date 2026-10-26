<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset=".github/assets/logo-dark.svg">
    <img alt="decompdecomp" src=".github/assets/logo-light.svg" width="560">
  </picture>
</p>

<p align="center">
  <a href="LICENSE"><img alt="AGPL-3.0 license" src="https://img.shields.io/badge/license-AGPL--3.0-157a49?style=flat-square"></a>
  <img alt="Linux" src="https://img.shields.io/badge/platform-Linux-15140f?style=flat-square">
  <img alt="macOS" src="https://img.shields.io/badge/platform-macOS%20Intel%20%2F%20Apple%20Silicon-15140f?style=flat-square">
  <img alt="Windows" src="https://img.shields.io/badge/platform-Windows%2010%20%2F%2011-15140f?style=flat-square">
  <a href="https://discord.gg/hhEm3zZagB"><img alt="Discord" src="https://img.shields.io/badge/Discord-join-5865F2?style=flat-square&logo=discord&logoColor=white"></a>
  <img alt="Agents: Claude Code, Codex, opencode, Antigravity, any API model" src="https://img.shields.io/badge/agents-Claude%20Code%20%C2%B7%20Codex%20%C2%B7%20opencode%20%C2%B7%20API%20models-157a49?style=flat-square">
</p>

decompdecomp is an app that decompiles games you own. AI agents write C or C++ for each function of the game. The
game's original compiler builds it. When the result is the same as the game, byte for byte, the function is
matched. In the end you get a project that builds your game again, exactly.

**New in 1.0.3-alpha: much faster, in its own window.** The app is ready the moment it opens (3.7 s before, 15 ms
now). The map of a big game updates ten times faster after a match. With 16 agents on two games, the app answers in
under a tenth of a second (1.4 s before), and agents restart by themselves after a crash or a usage limit.

<p align="center">
  <img alt="decompdecomp on Demo Quest 64: the autopilot matches 20 functions for free, then a helper matches six more, each judged byte for byte." src=".github/assets/demo.webp" width="900">
  <br>
  <sub>The app on Demo Quest 64, the demo ROM that comes with it. To keep the recording short, the helper is a
  stand-in for a model that hands in the demo's own C; the app compiles and judges every function for real.</sub>
</p>

## Choose work per agent

In **Start agents → Work selection**, choose a queue for the workers you are starting. Launch an expensive
model with **Largest first**, then start a cheaper model with **Untried first** or **Easiest first**. You can also
choose **Closest to matching**, **Finish source files**, or **Random**. **Automatic** keeps the existing adaptive
scheduler and model routing.

**Only functions below match %** restricts automatic claims to functions whose best recorded match percentage is
strictly below the threshold, plus functions with no scored attempt. For example, 80 includes unscored functions
and those below 80%, but excludes one at exactly 80%. This is the app's comparison score, not an estimate of work
remaining. **Untried first** means no compile attempts, and moves tried functions back rather than excluding them.

**Max tries per function** caps that worker's recorded candidate submissions on each function. Compile errors
count; the counter persists across sessions and restarts. At the limit, the best candidate and notes stay saved,
and the function is released for another worker. Raising the limit permits more attempts; blank means unlimited.
The final allowed attempt can still match. Compiler comparisons and flag searches are separate diagnostic work,
not candidate submissions covered by this cap.

Each worker's card in **Agents** has **Work selection** to change it independently. The setting survives sessions
and restarts and applies after the current claim finishes. Explicit strategies override adaptive routing and
release ordering; claim ownership and the worker's own recent-release cooldown still apply. Configured queues
claim one function at a time so neighbouring batches cannot bypass the filter. The percentage filter also applies to named function requests. The same settings work across consoles, ROM and project mode, and team rooms with
an updated host. Polishing uses its separate queue.


For local library research, see [Comparing local SDK libraries and source](docs/local-sdk-comparisons.md).

## RetroReversing fork

This fork of [NDelic's decompdecomp](https://github.com/n-delic/decompdecomp) adds project import, ROM handling and matching features. Native macOS support is now upstream.
The original decomp.wtf and decompdecomp credits and licenses are preserved. Report fork issues to
[RetroReversing/decompdecomp](https://github.com/RetroReversing/decompdecomp/issues).

## Independently linked code images

Projects with compressed game code or overlays can export their verified linked functions through
`decompnet.forks.linkedimages.export`. These are normal objdiff units: original-image bytes form immutable
references, and only source-backed, relocated bodies that pass the project's image gates become candidates.
Assembly passthrough functions remain in the denominator without counting as recovered C. Source checksums
invalidate stale candidates. Population identifiers distinguish overlays even when their RAM addresses overlap.

The overview shows code-byte-weighted totals and a breakdown by image. This covers known function extents;
data and unidentified regions are excluded. These special image snapshots are read-only in the app: change their
sources through the project's promotion/build workflow and use **Check again**. Ordinary object units keep their
existing agent workflow. See [the export contract](docs/linked-image-progress.md).

## Imported project builds

Builds use an existing `.venv` or `venv` in the project for its Python tools and dependencies. The app does not
install those dependencies into its own runtime. Failed builds retain the first diagnostic even when later
warnings or extraction progress would otherwise hide it.

Automatic references can cover shared source directories and per-segment overlay directories. When a source
rebuild is incomplete, references are derived independently from the pinned original image: each resolved code
and data section must match its original bytes before any configuration is published. Candidate objects never
supply reference bytes. Function matches remain separate from a successful whole-game rebuild.

## Autopilot on imported projects

Autopilot can try machine drafts in imported projects with writable C files and a supported draft decompiler.
For source-less units with decompdecomp's verified Splat references, it creates separate C candidate files and
checks that the project's own build can compile them before enabling attempts. It preserves existing source,
Makefiles, compiler settings and the assembly used for the linked baseline. Unsupported builds report what
blocked preparation; zero attempts are not described as a completed search with no matches. Drafts still pass
the same compiler and objdiff judge as agent submissions. A matching candidate is not a full linked-ROM rebuild.

## Free attempts from similar code

Autopilot tries verified C from compatible games, tiny-function templates, and locally supplied library C.
Every accepted candidate must compile and link to the destination's exact bytes. No AI tokens are used.

- **Better near-miss repairs:** eligibility uses percentage similarity, so a single changed operand no longer
  excludes an otherwise close function. Assembly differences guide casts, signedness, literal and ordering edits.
  Agreeing high/low address references can also propose a known destination symbol or an automatic address name, alone or combined with a
  constant or explicit field-offset edit. Literal pointer addresses can be corrected too.
  Literal base-plus-offset expressions and array indices use the stride evidenced by the old compiled address;
  adjacent symbolic-high/numeric-low address pairs account for signed low-half carry. These can combine with
  explicit field-offset edits. Small candidate-local structs can also have byte padding resized or inserted
  before a used field, based on aligned memory operands. These proposals support simple scalar/pointer fields
  and common fixed-width aliases; packed types, bitfields, unions and ambiguous offsets are skipped. Shared
  headers are never rewritten. Ambiguous mappings, dynamic indices and unproven strides are skipped.
  Numeric ID macros used once to index an explicit 32-bit table can be corrected from aligned word offsets;
  ambiguous declarations, macro uses and relocations are skipped.
  Saved best attempts get the same address checks before wider donor searches; probe feedback and failed edits
  are cached and share the ordinary search budget. These edits affect only candidate C; shared symbols and types stay unchanged. An already-installed decomp-permuter
  supplies a small additional batch when time remains.
- **Regional differences:** verified transfers teach symbol mappings and explicit pointer offsets. Offset
  mappings need two independent matching pairs and no conflicting evidence. Named struct definitions are not rewritten.
- **Tiny functions:** constants, arithmetic, getters, setters and simple forwarding wrappers can be proposed
  without another project containing matched C.
- **Local libraries:** add SDK archive folders once in global **Settings → Local SDK libraries**. Each game
  detects matching signatures automatically; its Settings can select libraries explicitly, including disabling all.
  For advanced source matching, in the project SDK panel, supply an object archive and optionally its C source file/folder and a
  self-contained declarations header. Matching fingerprints select C to try with the project's compiler.
  Sources stay local; nothing is downloaded or built by an SDK's scripts. Import with the compiler settings you
  intend to use; reimport after changing them. Ambiguous definitions are excluded.
- **Automatic follow-up:** a new verified match queues compatible projects on one low-priority worker, so useful
  code can spread without repeatedly clicking Run again. Stop pauses that project's automatic reuse until Run again.

Settings lets you disable automatic reuse and adjust its time and compile limits (defaults: two minutes and
128 attempts). A whole automatic wave shares one budget, including exact transfers. Manual autopilot keeps its
existing exact-transfer phase, then shares the additional budget between templates, libraries and similarity repairs.
The last search reports verified bytes, attempts and bytes per minute of elapsed time, not CPU time.

In Settings, optionally enable **deeper iterative repairs** after normal autopilot. This experimental phase is
**off by default**, with its own additional 120-second / 128-attempt budget (both adjustable). Normal matching
finishes first and keeps its full allowance; automatic background reuse does not start this extra phase.
Iterative repairs try up to three edit rounds and keep the best three improving/equal-score alternatives.
Each function gets at most eight new compilations per pass. A persistent round-robin cursor continues with the
next function on the next pass, instead of repeatedly prioritising the closest candidates. Unfinished frontiers
survive restarts. The phase records its own results and adds a summary note; only exact judge matches count.


Global-address repairs also track symbolic MIPS upper halves through straight-line instructions to later
load/store offsets. Register clobbers, branches, calls and unknown instructions stop tracking; conflicting
consumers are rejected. Full-address comparison also resolves C identifiers whose address is shown as another symbol plus an offset
in disassembly. Reconstructed addresses only propose local C edits and still require exact verification.

Repairs retain the original donor when the candidate's provenance is known. Successful repairs can then
teach symbol mappings for other candidates from that same donor. New data/callee mappings need agreement
from two independently verified function pairs; conflicts, reopened functions and changed donor code invalidate
the evidence. Each proposed transfer still goes through the exact compiler/link judge. Old saved candidates
can recover provenance only when recorded donor history reproduces their adapted C exactly.

Saved candidates also get bounded constant-only repairs when no address correction is proposed. Aligned
MIPS immediate and fixed-shift differences can guide literal, explicit multiply/divide and shift edits.
Repeated constants are tried individually (up to four occurrences), with small consistent group edits and
combinations capped at eight proposals. Comments, strings, symbol addresses and suffixes are preserved;
register/control-flow changes, signed expressions and implicit type/array-stride changes are left alone.

Saved candidates can also get a local MIPS signature/argument repair when their only non-relocated
instruction differences move ordinary integer/pointer arguments to later registers. The repair can insert an
unused parameter before a live incoming argument, or forward untouched incoming registers through a small
single-call wrapper. Only the candidate's declarations change; conflicting shared prototypes fail compilation.
Floats, aggregate/variadic parameters, stack arguments and complex wrappers are left alone. These shared rules
can help any compatible MIPS ROM game; they are not specific to a title, and other processors/object projects
need their own calling-convention and source-context support.

When eligible near misses exist, similarity transfers use at most three quarters of the remaining search budget;
the rest is reserved for repairs. The transfer cursor is retained so the next pass continues through the queue.

Use **Run again** to resume, including after restarting the app. Completed trials are remembered; changes to
candidate C, headers, symbol addresses, relevant mappings, targets or compiler settings make them eligible again.
A running compile or analysis step can finish after the time limit; no new candidate starts once it is reached.

A donor's proven flags are also tested with an already-installed toolchain. A byte match under those flags appears
as a **compiler** result with C saved in the function's notes. Review the settings before accepting it: these
trials do not change settings or count as matched progress. Similarity scores are never verified progress.

These additions support MIPS ROM projects (N64, PS1, PS2 and PSP). Imported repository projects still use exact
transfers; adapting similar functions across source files and object relocations is planned.

## ROM library

Set **Settings → ROM library** to the folder containing your extracted game files. When an imported project
needs its game, decompdecomp searches that folder recursively for the project's expected SHA-1 and fills in a
matching path. N64 .z64, .v64 and .n64 byte orders are recognized regardless of filename. Confirm ownership and
click **Use path** to continue. The search does not unpack archives or start a build.

If setup fails after accepting your ROM, reopen the project or retry: its verified copy is detected first and
**Retry setup** reuses it without opening the file chooser. Projects without a declared SHA-1 still need manual
selection. Clear the folder setting to disable library searches. On POSIX systems, hashes are cached in memory until files change
or the app restarts. Windows rehashes files because timestamps may not reliably reflect overwrites; original files are never moved by the search.

## Install

decompdecomp runs on **Linux and Windows 10/11** on x86-64, and **macOS 12+** on Intel or Apple Silicon. On Linux, install it with one command. The installer also gets
the free compilers and keeps decompdecomp up to date:

```
curl -fsSL https://raw.githubusercontent.com/RetroReversing/decompdecomp/main/site/public/install.sh | bash -s -- --source branch:main
```

Upstream Linux packages remain available from the [releases](https://github.com/n-delic/decompdecomp/releases):

```
sudo apt install ./decompdecomp_*_amd64.deb        # Debian, Ubuntu, Pop!_OS, Mint
sudo dnf install ./decompdecomp-*.x86_64.rpm       # Fedora
chmod +x decompdecomp-*-x86_64.AppImage && ./decompdecomp-*-x86_64.AppImage   # any other distribution
```

On **Windows**, download and open `decompdecomp-<version>-windows-x64-setup.exe` from the
[releases](https://github.com/n-delic/decompdecomp/releases). Setup installs the Microsoft Edge WebView2 Runtime and
.NET Framework 4.8 when they are missing, then opens the app installer. It may ask for administrator permission and
needs an internet connection to download missing Microsoft components. The app installer lets you choose where
decompdecomp goes and whether to add a desktop shortcut (selected by default); it always adds a shortcut to Start.

To install from source with [uv](https://docs.astral.sh/uv/getting-started/installation/) instead, open a new
PowerShell window and run:

```powershell
uv tool install --python 3.12 "decompnet @ https://github.com/n-delic/decompdecomp/archive/refs/heads/main.tar.gz"
decomp setup
decomp shortcut
decomp
```

The app opens in a native Windows window. Its shortcuts appear on the desktop and in Start, and its tray icon
brings back a window closed while helpers work. Free tools use Windows builds where available. Projects stay in your Windows `Decomp` folder.

> [!IMPORTANT]
> **PS1, PS2 and most N64 games need WSL 2 on Windows.** Their period compilers (GCC for PlayStation, EE-GCC and
> CodeWarrior for PS2, KMC GCC for N64) use their Linux builds through WSL 2 on Windows. Without it,
> every compile of such a game fails. Set it up before you import one: run `wsl --install` in an administrator
> PowerShell, restart if asked, then open Ubuntu once to finish its setup.
>
> GameCube, Wii, DS and Windows PC games, and N64 games built with IDO, don't need WSL: their compilers run on
> Windows itself.

### macOS (Intel and Apple Silicon)

For an app you can put in `/Applications`, download the archive for your processor from the
[RetroReversing releases](https://github.com/RetroReversing/decompdecomp/releases):
`macos_arm64` for Apple Silicon or `macos_x86_64` for Intel. Unzip it, move
`decompdecomp.app` into Applications, and open it from Finder. The app includes its own Python and
dependencies, so moving it does not depend on a uv installation. It is not signed or notarized yet;
macOS may require you to approve its first launch in Privacy & Security. Install free compilers
and tools from the app's Setup screen. Replace the app from a later release to update it.

The source installer is another option. It creates a launcher in `~/Applications` backed by
an external uv installation. To install from a checkout:

```sh
git clone --branch main https://github.com/RetroReversing/decompdecomp.git
cd decompdecomp
bash site/public/install.sh
```

The installer uses uv and Python 3.12, installs the Cocoa/WebKit bridge, gets the free tools, and creates
`~/Applications/decompdecomp.app`. Open it from Finder or run `decomp`. Projects stay in `~/Decomp`;
toolchains live in `~/Library/Application Support/decompnet/toolchains`. Xcode Command Line Tools
(`xcode-select --install`) are needed on Intel Macs for N64's MIPS binutils, and on both Mac architectures to
build PS1 GCC 2.6.0, 2.6.3 and 2.8.0 from pinned source on first use. The ready-made 2.7.2 (PSX and CDK) and 2.8.1 compiler binaries are for
Apple Silicon. Finder launches preserve the PATH
from installation so your installed Node and helper CLIs remain available; rerun `decomp shortcut` after
changing their locations. This source install has no signed DMG.
Installs from `main` track that branch in this fork when checking for updates.

**Apple Silicon:** the app, WebKit window and modern tools run natively. The pinned N64 KMC-compatible
GCC 2.7.2 and binutils 2.6 archives contain Intel programs, so install Apple's Rosetta runtime before setup:

```sh
softwareupdate --install-rosetta
```

Read and accept Apple's license if you agree, then rerun `decomp setup`. No proprietary game compiler is
installed automatically. IDO still needs your explicit license acceptance in Compilers & tools.

| Mac capability | Availability |
| --- | --- |
| N64 ROM import, compile, match and export | KMC GCC via Rosetta on Apple Silicon; directly on Intel |
| PS1 GCC 2.6.0, 2.6.3, 2.7.2 PSX/CDK, 2.8.0 and 2.8.1 | Native Apple Silicon tools with maspsx; 2.6.0, 2.6.3 and 2.8.0 also build from pinned source on Intel Macs |
| PS1 GCC 2.5.7, EGCS 2.91.66 and GCC 2.95.2 | Linux/WSL builds; native macOS builds are not available yet |
| Native window, file/folder pickers and export downloads | Cocoa and system WKWebView, no GTK or X11 |
| m2c, decomp-permuter and objdiff | Pinned Mac/Python builds |
| Other consoles and public decomp projects | Only where their pinned compiler/tool builds support this Mac |
| Linux-only PS1/PS2 period compilers | Unavailable on macOS; use a supported Linux host |
| Keeping helpers working after closing the window | Run `decomp` again to reopen; no Mac menu bar tray icon yet |

For development: `uv sync`, `uv run decomp` (separate `~/Decomp-dev` state), and `uv run pytest -q`.
On a Mac desktop, `DECOMP_TEST_COCOA=1 uv run pytest -q tests/test_macos.py` also exercises a hidden
WKWebView against a fresh app, including authentication, external links and close confirmation.
The homemade N64 demo tests need `brew install llvm lld`, with `$(brew --prefix llvm)/bin` and
`$(brew --prefix lld)/bin` on PATH, plus the free tools from `decomp setup`.


## Start

Type `decomp`, or open decompdecomp from your apps menu. It opens in a window of its own.

1. **Open a game.** Pick a ROM, a disc image or a program file of a game that you own. To try the app first, open
   Demo Quest 64. For an N64, PlayStation or PlayStation 2 game, the app offers to install all of that console's
   compilers first. Then it tests which one built the game. Tick the box only if you accept the proprietary ones.
2. **Start agents.** Any model with an API key (DeepSeek, OpenRouter, OpenAI, a local model), or Claude Code,
   Codex, opencode and Antigravity CLI. Run several, and different models: they work on different functions at the
   same time.
3. **Watch the map.** Each square is a function. It turns green when its function matches. The autopilot matches
   the easy ones for free, before any agent starts.
4. **Export** (in the project's header). You get a decomp project that builds your game again, byte for byte.

To inspect agent costs, open **What it said and did → Session transcript**. Each new
session keeps the full app prompt (including guides and project instructions), exposed
replies, app tool arguments/results, and reported input/cache/output usage. API helpers
also record each request after context trimming, including tool definitions. CLI provider
system prompts, hidden reasoning, shell output and internal compaction are not available.
The transcript is local, paged separately from the short live feed, survives restarts,
and redacts known credentials. It contains source and can grow with long sessions;
older sessions recorded before this feature cannot be reconstructed. Token counts and
dollar costs are shown only when reported, not estimated or assumed to be zero.

You can also continue a community decomp: paste the address of its repository and pick your own copy of its game.

Some community projects do not provide `objdiff.json`. After you supply the matching game and the project's build
succeeds, decompdecomp can generate it for simple N64, PS1 and PS2 splat projects. It requires a SHA-1 in the splat
config, built C objects and one unstripped ELF beside a rebuilt image with the same SHA-1. The build tools still
need to work on your computer; this does not port a project's build system.

References come from a separate all-assembly split of your verified game. Each reference's linked code and data
must reproduce the game's original bytes. Candidate C objects are never copied into the reference set. Generated
files stay under the private `.decomp-wtf/objdiff-references/` folder. Existing `objdiff.json` and `expected/` stay
untouched. Custom layouts, overlays and compressed code need manual setup; the app reports the missing requirement.


Already have repositories on disk? Open **Continue a community decomp**, then **Import checked-out projects**.
Choose their parent folder and click **Scan folder**. It looks through platform subfolders and lets you select
projects to import. Review their contribution rules first. By default, clone separate copies containing only
committed files. You can instead use existing checkouts to keep game files, tools and uncommitted work. Those stay on their
current branch; later, agents commit accepted changes there. Nothing is pushed automatically.

Importing only adds projects to the app. Open each one to review setup and measure progress before starting agents.
Unsupported build systems still need manual setup from the project's README. A ROM project's saved `progress/`
folder uses **Open a saved project** instead. The scan stops at each checkout, ignores hidden/build/tool folders,
and reports when a size or depth limit means you should scan a smaller folder.


When you close the window while agents work, decompdecomp asks you: stop them and quit, or keep them working.
**Quit** ends the app and everything it started.

## Coming from decomp.wtf or an older version (1.0.x)

Your projects stay, decomp.wtf's too: decompdecomp continues decomp.wtf and reads the same folder and database. On
Linux, the new version opens the same `~/Decomp` folder, with every match, name and note in it.
The old Windows installer kept its projects inside a WSL distribution. The Windows app now runs natively and uses
`%USERPROFILE%\Decomp`; it uses WSL for some Linux-only compilers, but doesn't scan WSL for projects automatically.

To move projects from the old Windows install, close its app, then copy the whole `Decomp` folder from
`\\wsl.localhost\decomp-wtf\home\decomp\Decomp` to `%USERPROFILE%\Decomp`. Include the hidden `.decomp` folder
inside it: that holds the project database and saved matches. The app reconnects projects to their copied folders.
Did you keep a project outside the `Decomp` folder, in `Documents` for example? Move that project's folder into
`%USERPROFILE%\Decomp` too, and keep its folder name: the app finds a moved project by its name there.

**Open a saved project** is for something else: a `progress/` folder that decompdecomp saved, with a
`decompdecomp.json` in it. Use it if you only have a project's `progress/` folder, with your own copy of the game.
A project's own folder isn't a save, and neither is its `.decomp.wtf.json`: that file says how the project builds.
Keep that file's name as it is. decompdecomp still reads it as `.decomp.wtf.json`.

1. **Install the new version** the same way as before: the install command above or the latest `.deb`, `.rpm`,
   AppImage or Windows setup bundle. The installer removes an older `.deb` or `.rpm` of decompdecomp, so your apps menu shows
   one. Going the other way, from the install command to a package? Remove the old copy first: `uv tool uninstall decompnet`.
2. **Start it** with `decomp` or from your apps menu. It opens in its own window now, not in your browser.
3. **Start your agents again.** Helper agents that were working in the old version don't restart by themselves the
   first time. Open each project and start them; from then on they restart by themselves.

What changed on the way:

- **Agents run inside the app only.** `decomp connect` and `decomp mcp` are gone. On its first start, the new
  version removes the old `decomp` entries from your agents' settings (Claude Code, Codex, opencode, Antigravity and
  others), and its home page says what it removed. Before that, those entries started the old app again whenever
  you closed it.
- **The Source tab is now the Export button** in the project's header.
- **Windows projects and compilers.** The app server and window run natively on Windows. Linux-only period compilers
  run through WSL 2, using temporary Linux-side build stages so old 32-bit tools work. Run `decomp shortcut` once to
  put native shortcuts on the Windows desktop and in Start. Setup installs WebView2 and .NET Framework 4.8 when missing;
  pywebview is installed with the app.

## Moving to another computer

decompdecomp saves the work on each N64, PlayStation and PlayStation 2 project as plain files in the project's `progress/`
folder, every few minutes: matched C, best attempts, headers, names, notes and settings. It never contains the game,
so you can keep it in a git repository. GameCube, Wii and PC projects and community decomps are git repositories
already: push them.

The simplest way is to copy your whole `~/Decomp` folder to the same place on the new computer. Copy the hidden
`.decomp` folder in it too: it holds the matches of every project. decompdecomp then finds every project. To take a
single N64, PlayStation or PlayStation 2 project, or to keep it in git:

**Share or commit only `progress/`.** The rest of a project's folder is made from your copy of the game, and the
game's code is its rights holder's: keep it to yourself.

```
~/Decomp/<project>/
├── progress/         your work: share this, commit this
│   ├── src/              each matched function's C
│   ├── best/             the closest attempt at each function that doesn't match yet
│   ├── include/          the shared headers every function is compiled with
│   ├── decompdecomp.json the game's fingerprint (never the game), compiler, layout, names, notes, settings
│   └── README.md         what this is and how to continue
├── rom.z64           your copy of the game: never share it
├── asm/  assets/     taken out of the game: never share them
└── src/  include/  *.ld  *.yaml  *.txt   the build decompdecomp makes from the game, again on any computer
```

1. Copy the project's `progress/` folder to the new computer, or push it to a git repository.
2. On the new computer, in decompdecomp: **Open a saved project**. Pick the folder (or paste the repository's
   address) and your own copy of the game.
3. decompdecomp checks it's the same game, rebuilds the project, and judges every saved function again.

## Platforms

| Platform | Status |
| :-- | :-- |
| Nintendo 64 | Recommended |
| PlayStation (PS1) | Beta |
| PlayStation 2 | Beta |
| PSP | Beta |
| GameCube, Wii | Beta |
| Windows PC games | Beta |
| Nintendo DS | Beta |
| Game Boy Advance, 3DS, Switch, Xbox, Xbox 360 | Community decomps only |

1.0.3 rewrote most of the app, so every platform needs testing again, Nintendo 64 too. Try it on a game you own and
[tell us how it went](https://github.com/n-delic/decompdecomp/issues): what worked, what didn't, what was confusing. Reports are how a platform goes
from beta to recommended.

Proprietary compilers (CodeWarrior, Visual C++, IDO) are installed only after you accept their licenses in the app.

## Questions people ask

**Is this just Ghidra?** No. Ghidra (and IDA) show you C so you can *read* a program. That C usually doesn't compile,
and when it does, the game's compiler makes different machine code from it. decompdecomp keeps a function only when
its C, built by the game's own compiler, gives exactly the original bytes. That's what lets the result build the
game again, the base for ports and mods.

**What does the C look like?** At first, raw. A match proves the code is right, not that it's easy to read. Here is
one function of Demo Quest 64 as the autopilot matched it, and the same function made readable. Both compile to
the same bytes:

```c
/* As matched */
void func_80000A0C(s32 arg0) {
    s32 temp_v0;

    temp_v0 = D_8004AE30 + (arg0 * (D_8004AE58 + 1));
    D_8004AE30 = temp_v0;
    if (temp_v0 > 0xF423F) {
        D_8004AE30 = 0xF423F;
    }
}
```

```c
/* Made readable: adds points for a hit, times the combo, up to the highest score. */
void add_score(s32 points) {
    g_score += points * (g_player.combo + 1);
    if (g_score > MAX_SCORE) {
        g_score = MAX_SCORE;
    }
}
```

Agents do that second step too: once half a game matches, **Start agents** offers **Polish matched code**. Helpers
name functions, globals and parameters, describe structs in the shared header and add a comment, and a change is
kept only when the function still matches byte for byte. Names are the agent's reading of the code: the bytes
prove what a function does, not what its author called it.

**Has a whole game been decompiled with it?** Not yet. Games get far (hundreds or thousands of matched functions),
and the export rebuilds the game byte for byte from matched C plus assembly for what isn't matched yet. A game at
100%, polished, is the goal, and the first one will be announced here.

**What do I end up with?** The source code of a game you own, that builds it again. A DS game started from scratch
reached 2,400 matched functions (58% of its code) in under two days, about half of them matched by free models, and
its project built a game that boots in an emulator: matched functions from their C, the rest from the original's code
until agents get to it. It isn't overnight: the last functions are the big ones, and they take time and tokens. But
every match stays matched, and each one makes the project more of what a game's source is: something you can read,
change, port and mod.

## Help make it better

decompdecomp is built in the open, and it's meant to be easy to work on. Read [CONTRIBUTING.md](CONTRIBUTING.md)
first. A copy of decompdecomp that runs from a git checkout is a separate app, with its own folder, so it never
touches the projects of the decompdecomp you have installed.

- [The guide](docs/GUIDE.md): how it works, agents, teams, platforms and the export.
- [ROADMAP.md](ROADMAP.md): what's planned, and which feature works on which platform.
- [Issues](https://github.com/n-delic/decompdecomp/issues): problems and requests.
- [Discord](https://discord.gg/hhEm3zZagB): questions, progress on your games, and talking with the people building it.

## Use it responsibly

- Use only games that you own. decompdecomp contains no game code and supplies no ROMs.
- Everything runs on your computer. Nothing of your game is sent anywhere, except to the AI model you choose.
- Do not publish the code that it makes from a game. That code belongs to the rights holder of the game.
- Follow the rules of any project that you contribute to.

## Credits

decompdecomp continues [decomp.wtf](https://github.com/i12bp8/decomp.wtf) by
[Sam Wolfs (@i12bp8)](https://github.com/i12bp8), who made it up to version 0.10.6 and then archived it. Thank you,
Sam. decompdecomp is not related to decomp.wtf, and its author does not endorse it.

decompdecomp uses splat, spimdisasm, objdiff, decomp-toolkit, m2c, Ghidra, decomp-permuter, wibo and the decompals
compilers. The
[guide](docs/GUIDE.md#built-on) lists them.

## License

decompdecomp is free software under the [GNU Affero General Public License version 3](LICENSE) © 2026 NDelic.
Anyone who gives or sells a copy of it, changed or not, must give its full source under the same license. So must
anyone who runs a changed copy for others to use over a network, a hosted room for example.

The code that came from decomp.wtf was released under the MIT license by Sam Wolfs; its notice is in
[LICENSE-decomp.wtf](LICENSE-decomp.wtf).

<p align="center"><sub>Not related to Nintendo, Sony or Microsoft. Platform names are trademarks of their owners.</sub></p>

### RNC-packed N64 programs

The N64 importer recognizes RNC method-1 blocks, verifies both CRC16 checksums, and checks the decoded bytes for
code before adding them to the analysis image. Method 2 and encrypted/locked streams are not supported. A small
initial function count can still mean that the game uses another packing format or an undiscovered loader.

For RNC-expanded exports, `baserom.z64` is the expanded analysis image and `baserom.cartridge.z64` is the original
normalized cartridge. Keep both private. The build first checks the linked analysis image, then reconstructs the
packed blocks using literals from the newly linked output and the original stream's coding decisions. It rejects
changes incompatible with those references and checks the complete cartridge against the original. This template
operation supports matching builds; it is not a general compressor for mods. Other expanded packing formats fail
export with an explanation until they have a verified cartridge reconstruction.

An identical rebuild can still contain assembly and binary fallbacks. Check the reported number of functions
selected as C separately; an identical cartridge does not mean that every function has been decompiled.

For imported overlays missing their original strings or tables, see [supplemental original overlay data](docs/OVERLAY_DATA.md).

Verified full-image imports include linked assembly routines as target-only units: assembly bytes never count
as recovered C. Assembly wrappers containing whole binary programs are not treated as single functions; an
explicit coverage notice identifies their exclusion from function progress.

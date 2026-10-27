# Linux-only compilers on macOS

The optional Linux runner lets macOS use explicitly supported Linux compiler
packs. Native compilers still run directly. Windows uses the existing WSL path;
Linux uses its normal compiler sandbox.

## Set up once

1. Install **Colima and the Docker CLI**. With Homebrew: `brew install colima docker`.
2. In **Compilers & tools**, choose **Prepare Linux runner**. This starts the
   app's dedicated Colima profile, `decompdecomp`, and downloads a digest-pinned
   Python/Linux runtime. Apple Silicon uses Colima's Rosetta and i386 translation.
   The first setup can take several minutes. Colima/VZ requires a supported recent
   macOS release; follow any Rosetta setup instructions Colima reports.
3. Install a compiler marked **Linux VM**. Proprietary packs still require
   explicit license acceptance before the VM starts or any compiler is installed.
4. Select that compiler for a project or test it on a function. Experimental
   profiles remain explicit choices, excluded from routine installed-profile searches.

If the VM is stopped after a reboot, choose **Prepare Linux runner** again.
The status check never starts a VM or downloads anything. The app does not change
your active Docker context or use the default Colima profile. It connects only to
its named profile's local Unix socket, ignoring ambient remote Docker settings.
Host Colima/Docker are user-managed infrastructure; the compiler packs, host math
libraries and container runtime image are pinned by the app.

## What a compile does

- Copies the compiler/dependencies into an immutable guest cache, keyed by install
  manifests and adapter source. Linux filesystems avoid old compilers' inode limits.
- Copies source and headers into a fresh container work folder. It mounts no host
  folders and has no network. Only requested object outputs are copied back.
- Runs as an unprivileged user with read-only compiler files, no capabilities,
  process/memory/file limits and a timeout. Cancellation removes the job's container,
  including compiler subprocesses.
- Returns the object to the same linker and byte judge used by native compilers.
  Model tokens are not involved in these local compiler calls.

The VM defaults to 2 CPUs and 3 GiB RAM. Each job has a 1 GiB physical-memory cap,
a 2 GiB virtual-address cap, a 30-second CPU cap and a 64 MiB output-file limit.
Input/output transfers are bounded to 128 MiB in total. The VM stays available
between compiles; each job gets its own container. Cached tool snapshots live in
the VM's Docker volume `decompdecomp-compiler-tools`. New installs get new caches;
old snapshots are retained so active jobs cannot lose their compiler.

## Coverage

Original EGCS 1.1.2-4 and GCC 4.4.0 have passed real macOS-to-VM compilation,
ordinary engine byte-match comparisons, calls/data relocation checks, floating
point, branches, export-style relative-path builds, timeouts and cancellation
on Apple Silicon. The GNU integration tests use only homemade code.

IDO 4.1/5.2/6.0, IDO 5.3 C++ and MIPSpro 7.4.4 are connected to the same runner,
including guest Python for their adapters. Their real compiler execution remains
pending explicit license acceptance. This is not a claim that those five packs
have passed end-to-end matching tests. Physical Intel Mac and Windows/WSL checks
remain outstanding.

The runner is shared by `CompilerConfig`, function compilation and `run_in`, so
ROM-mode diagnostics and exported build helpers use the same path. Imported
projects retain their own build scripts; arbitrary imported Make/Ninja builds
are not automatically moved into the VM. Native rebuilds of the original GNU
packs and native modern-macOS IRIX emulation remain future options.

## Isolated development checks

Use a scratch toolchain root and a separate VM profile, never the installed app's
projects or the user's default Colima VM. After installing only the desired packs
through Setup/`toolchains.install`, run:

```sh
DECOMPNET_TOOLCHAINS=/path/to/scratch/toolchains \
DECOMP_LINUX_VM_PROFILE=decomp-compiler-tests \
DECOMP_LINUX_VM_TESTS=1 PYTHONPATH=src \
python -m pytest tests/test_linuxvm.py tests/test_n64_irix_compilers.py -q
```

Tests never download or install compilers. `DECOMP_LINUX_VM_PROFILE` changes only
the app-owned profile selection for isolated checks. Stop that scratch profile
after testing with `colima stop decomp-compiler-tests`.

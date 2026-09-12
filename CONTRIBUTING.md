# Contributing

This is a small, hobby-scale reverse-engineering/build-system project —
contributions are welcome, but keep these in mind:

- **HDL/kernel/U-Boot/Buildroot source itself isn't in this repo.**
  `firmware/src/` is cloned fresh by `setup.sh` from the real upstream
  fork (see the root README). If you have a fix for something in there,
  turn it into a patch under `firmware/patches/` (see the existing two
  for the format/style — `git diff` against a clean `setup.sh` checkout,
  or `git diff --cached` for new files) rather than committing the
  generated source tree itself.
- **Test before opening a PR.** There's no CI for this repo (Vivado
  can't reasonably run in a hosted runner), so "I ran `build_all.sh`
  end-to-end and flashed the result" is the bar — mention what you tested
  in the PR description.
- **Scripts over documentation-only claims.** If you're fixing a build
  bug, prefer fixing it in `firmware/scripts/build_all.sh` (or the
  relevant `.tcl`) over just documenting a manual workaround, so the next
  person doesn't have to rediscover it.
- Bug reports: please use the issue templates (build failure vs. hardware
  mismatch) — they ask for the specific details (stage, OS/tool versions,
  logs) that actually speed up debugging a build system like this one.

---
name: Build failure
about: build_all.sh (or setup.sh) failed partway through
title: ""
labels: build
---

**Which stage failed?** (see the `=== [N/7] ... ===` banners in the output)

**Host OS + version** (`lsb_release -a`):

**Vivado/Vitis version** (`vivado -version`):

**Exact error output** (the last ~30 lines before the script exits, or a
link to the full log — `build_all.sh` doesn't overwrite its own stdout, so
`./scripts/build_all.sh 2>&1 | tee build.log` captures everything):

**What you changed, if anything, before this happened** (HDL edit, patch
edit, different upstream commit, etc. — leave blank if you ran the steps
exactly as documented):

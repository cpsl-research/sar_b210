# User configs

Your saved run configs live here as `<name>.yaml` (everything else in this directory is gitignored).
`configs/default.yaml` is shipped and read-only: change a copy, then save it as a new name.
Saving goes through `b210_sar.configs.save_user`, which validates first (B210 hardware limits in
`configs/hw_limits.yaml` plus internal consistency) and never overwrites.

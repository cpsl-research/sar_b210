# 5 - Design a waveform in the GUI

Goal: turn a range resolution and a maximum range into a saved, valid config, and see what limits it. Needs [tutorial 1](01_gui.md).

1. **Open the Configs tab.** From the sar_b210 repo root, start the GUI (`env -u PYTHONPATH uv run --extra gui python -m b210_sar.gui`), open `http://localhost:8010/` and press **Configs**. The GUI keeps running in that terminal; use a second one for step 6.

2. **Read the default.** Select `default`. The **Waveform construction** card shows frequency against time (two chirps, their overlap, the stitched 75 MHz band) and, below it, each step as retune, wait, dwell, with the sweep period (36.4 ms). Why the band sets range resolution and the sweep period sets velocity: [the primer](../theory/stepped_frequency_primer.md), sections 2-5.

3. **Generate from targets.** In the left panel enter range resolution `2`, max range `3000`, leave max velocity empty, chirps per dwell `50`, base `default`, and press **Generate**. (Base is the config whose other parameters are kept.) Expect a table of targets against achieved values (range resolution 2.00 m, R_amb 3000.6 m, v_amb 0.84 m/s, 2 steps, sweep 36.4 ms) and `no issues`. Terms ($R_{amb}$, $v_{amb}$, retune/wait/dwell, $M$): [primer](../theory/stepped_frequency_primer.md), sections 3-5.
   Now enter max velocity `5` and Generate again. The velocity is unreachable: each step spends 9.2 ms retuning and 8 ms settling regardless of the dwell, so even one chirp per dwell (the best possible) gives only 0.89 m/s, not 5; with your 50 chirps it is 0.84. It keeps the closest config (v_amb 0.84 m/s) and warns `velocity target unreachable: best v_amb = 0.84 m/s for 5 m/s asked ...`.

4. **Load it as a draft.** Press **Load result as draft**. The card redraws for the draft, and the parameter fields are editable. Under **Parameters**, set `dwell_s` (seconds) to `0.000500446428571`: 25 chirps instead of 50, each $T_c$ = 1121 samples / 56 MS/s = 20.018 µs (see `chirp_length_s` just above it). The diagram, issues and numbers update: the sweep drops to 35.4 ms and v_amb rises to 0.87 m/s. Dwell must be a whole number of chirps, so `0.0005` is refused: the error appears in red below the parameter fields, and Save is disabled.

5. **Save under a new name.** Type `my_waveform` in **save as** and press **Save as new**. This writes `configs/user/my_waveform.yaml`; an existing or shipped name is refused. On success the list shows `my_waveform` as a user config and the issues area says `saved as my_waveform`. While the draft has errors, Save is disabled unless you tick **save even with errors**; the file is then marked `# INVALID` and listed as INVALID.

6. **Check it from the CLI.**

```bash
env -u PYTHONPATH uv run python -m b210_sar.configs --check my_waveform; echo "exit $?"
```

   Expect `valid`, the parameter budget (`range resolution 2.00 m`, `sweep time 35.40 ms`), the line `delay-Doppler (planned, M=64): range res 2.00 m, R_amb 3001 m, v span +/-0.867 m/s, dv 2.71 cm/s`, and `exit 0`. To clean up, select `my_waveform` in the list and press **Delete** (top of the card, user configs only); confirm.

# 5 - Design a waveform in the GUI

Goal: turn a range resolution and a maximum range into a saved, valid config, and see what limits it. Needs [tutorial 1](01_gui.md).

1. **Open the Configs tab.** Start the GUI (`env -u PYTHONPATH uv run --extra gui python -m b210_sar.gui`), open `http://localhost:8010/` and press **Configs**.

2. **Read the default.** Select `default`. The **Waveform construction** card shows frequency against time (two chirps, their overlap, the stitched 75 MHz band) and, below it, each step as retune, wait, dwell, with the sweep period (36.4 ms). Why the band sets range resolution and the sweep period sets velocity: [the primer](../theory/stepped_frequency_primer.md), sections 2-5.

3. **Generate from targets.** In the left panel enter range resolution `2`, max range `3000`, leave max velocity empty, chirps per dwell `50`, base `default`, and press **Generate**. Expect a table of targets against achieved values (range resolution 2.00 m, R_amb 3000.6 m, v_amb 0.84 m/s, 2 steps, sweep 36.4 ms) and `no issues`.
   Now enter max velocity `5` and Generate again. The velocity is unreachable: each step spends 9.2 ms retuning and 8 ms settling regardless of the dwell, so even one chirp per dwell gives only 0.89 m/s. It keeps the closest config (v_amb 0.84 m/s) and warns `velocity target unreachable: best v_amb = 0.84 m/s for 5 m/s asked ...`.

4. **Load it as a draft.** Press **Load result as draft**. The card redraws for the draft, and the parameter fields are editable. Set `dwell_s` to `0.000500446428571` (25 chirps instead of 50). The diagram, issues and numbers update: the sweep drops to 35.4 ms and v_amb rises to 0.87 m/s. Dwell must be a whole number of chirps, so `0.0005` is refused with an error.

5. **Save under a new name.** Type `my_waveform` in **save as** and press **Save as new**. This writes `configs/user/my_waveform.yaml`; an existing or shipped name is refused. While the draft has errors, Save is disabled unless you tick **save even with errors**; the file is then marked `# INVALID` and listed as INVALID.

6. **Check it from the CLI.**

```bash
env -u PYTHONPATH uv run python -m b210_sar.configs --check my_waveform; echo "exit $?"
```

   Expect `valid`, the parameter budget (`range resolution 2.00 m`, `sweep time 35.40 ms`), the line `delay-Doppler (planned, M=64): range res 2.00 m, R_amb 3001 m, v span +/-0.867 m/s, dv 2.71 cm/s`, and `exit 0`. Delete the config from the GUI (**Delete**) when done.

Next: [4, troubleshooting](04_troubleshooting.md).

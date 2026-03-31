# Rewrite Drawer

Graph-rewriting simulation rendered as plotter-ready linework. A test bed for [autocritic](https://github.com/mccoyspace/autocritic) — the critic-driven image evaluation system.

## What It Does

- Simulates a graph rewrite rule inspired by Wolfram Physics: `{{x,y},{x,z}} -> {{x,y},{x,w},{y,w},{z,w}}`
- Animates growth in a browser UI with adjustable seed, frame count, event rate, layout, and random seed
- Exports the current frame as raw SVG, optimized SVG, optional `vpype`/`vpype-gcode` output, and machine-oriented G-code for a GRBL plotter server
- Can preview and send G-code over a GRBL plotter server's websocket protocol

## Run It

```bash
pip install -r requirements.txt
python3 run_local.py
```

Then open [http://127.0.0.1:8010](http://127.0.0.1:8010).

## Use with autocritic

Rewrite Drawer serves as a generator for autocritic's improvement loop. With both running:

```bash
# In one terminal:
python3 run_local.py

# In another:
python3 -m autocritic run --critic wolfflin --generator rewriter --model openai:gpt-5.4-mini --iterations 5
```

The critic evaluates each generated image through an art-theory lens and steers the simulation parameters toward more compositionally coherent results.

## Notes

- The rewrite engine uses the binary graph rule `{{x,y},{x,z}} -> {{x,y},{x,w},{y,w},{z,w}}`
- The UI animates by grouping many rewrite events into each visible frame
- G-code export uses `vpype gwrite` with the profile you select. `gcodemm` is the safest generic default
- The app reads `gcodeServer-config.txt` for machine defaults if present
- Machine-oriented output assumes `1 canvas unit = 1 mm`
- Exported files are written to `exports/`

## License

MIT

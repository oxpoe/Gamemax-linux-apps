# Contributing

Thanks for helping — the most valuable contribution is **hardware reports**: which cooler
works, which firmware revision differs, which sensor channel has to be picked. Use the
*Hardware report* issue template, or send a pull request.

## Before opening a pull request

The sender must stay dependency-free (standard library only) and must never write to the
device unless the user asked for it.

```bash
# nothing is written to the panel:
python3 src/gamemax_report.py --dry-run
python3 src/gamemax_report.py --list-sensors

# the tray app self-checks without a display, and can exercise every action:
uv run --with pyside6 python src/gamemax_gui.py --selftest
uv run --with pyside6 python src/gamemax_gui.py --selftest-actions

# build the AppImage (needs uv + network on first run):
bash packaging/build_appimage.sh
```

`--selftest-actions` really does start/stop the service and toggle autostart, then leaves
the service running — expected.

## Style

- Python 3.10+, no new dependencies, comments only where the code cannot explain itself.
- GUI strings are Indonesian (the original author's language); code and docs are English.
- Keep the protocol knowledge in `docs/reverse-engineering.md` and the README table in sync
  when you learn something new about the panel.

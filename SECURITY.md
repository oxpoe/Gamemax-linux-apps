# Security

This is a small local tool. The relevant surface:

- It writes 64-byte HID reports to the panel matching `VID 0x5131 / PID 0x2007`
  (plus a udev rule that grants the logged-in user access to that `hidraw` node).
- The tray app runs `systemctl --user` and, for the udev rule, `pkexec sh` on a script it
  writes to `/tmp`. Nothing else is executed, and no data leaves the machine.
- No secrets are stored. The config file (`~/.config/gamemax-report/config.json`) holds
  sensor channel names and display settings only.

If you find a problem — for example a way to make the helper scripts run something else,
or a path that writes outside the documented locations — please open an issue (or a
private security advisory on Gitea). Do not test on hardware you do not own.

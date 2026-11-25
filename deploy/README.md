# Running the daily job on a schedule

Nothing here is installed by the repository. These are the steps to install it on one computer, the issuer named in the registration.

## Before installing

1. The code is checked out at the commit the registration pins, with its environment built: `uv sync`.
2. The records repository is checked out beside it, and this computer can push to it.
3. A rehearsal gives the same scores digest as the other computer for the same snapshot:
   `python -m quant_loop.shadow emit --session <date> --snapshot artifacts/snapshots/<date> --dry-run`
4. The computer does not sleep in the evening, and its clock is synchronised.

## Install

```
mkdir -p ~/.config/quant-rapper ~/.config/systemd/user
printf 'CODE_DIR=%s\nRECORDS_DIR=%s\n' "$HOME/path/to/Quant_rapper" "$HOME/path/to/Quant_rapper_runtime_data" > ~/.config/quant-rapper/job.env
cp deploy/systemd/quant-rapper-*.service deploy/systemd/quant-rapper-*.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now quant-rapper-daily.timer quant-rapper-missed.timer
loginctl enable-linger "$USER"      # so the timers run after a reboot without anyone logged in
```

## Check

```
systemctl --user list-timers 'quant-rapper-*'
journalctl --user -u quant-rapper-daily.service -n 50
systemctl --user --failed
```

## What each unit does

| Unit | When (New York) | Does |
| --- | --- | --- |
| `quant-rapper-daily` | weekdays 19:00 | snapshot, the rule's tickets, the shadow tickets, then commits and pushes the records. A failed run is retried every 15 minutes, up to four times |
| `quant-rapper-missed` | Tuesday to Saturday 08:00 | lists decisions with no ticket written in time; shows as failed when there are any |

A market holiday needs no handling: the job works on the latest session that has closed, and a second run for a session writes nothing twice.

## Moving to another computer

Only one computer issues. Stop and disable both timers on the old one before enabling them on the new one, and change `issuer` in the registration through a recorded amendment.

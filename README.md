# sof-profiles

Admin-assigned player GUID for SoF1, carried in `team_red_blue`.

## Setup

Three pieces: game server (`.func` addons), **export-fire**, **userinfo_rcon.py**.
Optional **systemd** units in `systemd/` wrap the two Python services.

### Checklist

`systemd/install.sh` only helps with **step 3** (and only the host-side Python
services — not the game server itself). Everything else is manual.

| # | What | `install.sh` | Notes |
|---|------|--------------|--------|
| 1 | Deploy `profiles.func`, `ext_trigger.func` | — | `sofplus/addons/` (names not starting with `-`) |
| 2 | `user-<PORT>` path for export-fire | — | Symlink **only if** the game uses `User/` — see below |
| 3 | Python services + `RCON_PASSWORD` | **partial** | Install units, set env (incl. rcon — must match server), `systemctl enable --now` |
| 4 | `sofplus-cvars.cfg` | — | Set `_sp_sv_limit_userinfo_change` to `1` — see below |
| 5 | Debug-Stress-Testing | — | `prof_admin_add`, connect, `prof_apply`, `prof_enforce` |

**Persists on disk:** `profiles.func`, `registry.cfg`, `sofplus-cvars.cfg`, addon files.

### `user-<PORT>` path (export-fire needs the port in the folder name)

**export-fire** and **userinfo_rcon.py** watch `…/user-<PORT>/sofplus/data/…` and
read the **UDP game port from the folder name** (e.g. `user-28921` → rcon port
`28921` for `dumpuser`). They do not infer the port from a plain `User/` folder.

**Check your layout first:**

| Your server already uses… | What to do |
|---------------------------|------------|
| `user-<PORT>/` as its real user tree (e.g. Wine/sof-server convention) | **Nothing.** Point `SOF_USER_ROOT` / `--root` at that folder. |
| `User/` only (no `user-<PORT>` yet) | Create a **symlink** so Python sees the port in the path while SoF keeps using `User/`. |
| `user-<PORT>` already exists (anything other than a new symlink you plan to add) | **Do not overwrite.** Use the existing path as-is, or pick a layout that already matches export-fire. |

**Safe symlink** (only when the game uses `User/` and `user-<PORT>` does **not**
exist yet). Do **not** use `ln -sfn` — `-f` would replace an existing file or
symlink without warning:

```bash
cd "<SoF root>"
PORT=28921    # your game UDP port
TARGET="user-$PORT"

if [ -e "$TARGET" ]; then
  echo "$TARGET already exists — skip; use it as SOF_USER_ROOT (do not overwrite)"
elif [ ! -d User ]; then
  echo "User/ not found — fix your SoF root path first"
else
  ln -s User "$TARGET"
  echo "Created $TARGET -> User"
fi
```

Result: same files on disk, two names — `User/sofplus/data/…` (game) and
`user-28921/sofplus/data/…` (Python + port in path).

### systemd (recommended)

The install script only copies unit files — it does **not** start the Python
processes. `systemctl enable --now` is the same as `enable` plus `start` in one
command. The lines below:

- **`start`** (`--now`) — run `userinfo-rcon` / `export-fire` immediately (profiles
  need these while the game server is up).
- **`enable`** — start them automatically whenever the **host** reboots. Without
  `enable`, a reboot stops the processes and snapshots break until you
  `systemctl start` them again by hand.

**RCON password** — only `userinfo_rcon.py` needs this (not export-fire). The script
sends UDP `rcon dumpuser` when a userinfo trigger fires. It reads `RCON_PASSWORD`
from the environment — **not** from the game. Set it to the same value as the
server’s `rcon_password` (what you use after `rcon` in-game). Wrong password → no
snapshots, `prof_enforce` stays `pending`. Keep it out of git.

**export-fire already running** (another feature on the same host):

```bash
./systemd/install.sh --rcon-only
# edit /etc/sof-profiles/env:
#   SOF_PROFILES=/path/to/sof-profiles
#   RCON_PASSWORD=<same as server rcon_password>
./systemd/install.sh --rcon-only
sudo systemctl enable --now userinfo-rcon
```

`userinfo-rcon.service` loads `/etc/sof-profiles/env` via `EnvironmentFile=`.

**Fresh host** (no export-fire yet):

```bash
./systemd/install.sh
# edit /etc/sof-profiles/env:
#   SOF_USER_ROOT, SOF_EXPORT_FIRE, SOF_PROFILES, RCON_PASSWORD (see above)
./systemd/install.sh
sudo systemctl enable --now export-fire userinfo-rcon
```

Prerequisites: SoFplus + stufftext mod, Python 3.9+, a running **export-fire**
subscriber endpoint (from **sof-export-fire** or your existing setup), server rcon
enabled.

### Manual (no systemd)

```bash
# export-fire (sof-export-fire project) — no rcon password needed
python3 sof_export_fire.py --root "<SoF>/user-<PORT>" --serve 127.0.0.1:8765

# userinfo_rcon — RCON_PASSWORD must match server rcon_password
export RCON_PASSWORD='<rcon_password>'
python3 /path/to/userinfo_rcon.py
```

Optional env: `RCON_HOST` (default `127.0.0.1`), `RCON_PORT` (default `0` = port from
`user-<PORT>` in each trigger). Snapshots land in
`<SoF user>/sofplus/data/userinfo/snapshot_<slot>.cfg`.

### `sofplus-cvars.cfg`

Edit `<SoF user>/sofplus-cvars.cfg` (e.g. `User/sofplus-cvars.cfg`) and set:

```text
set _sp_sv_limit_userinfo_change 1
```

Reduces userinfo feedback loops. This file is loaded on server start — no console
step each restart.

When the server boots, SoFplus also loads `profiles.func` from `sofplus/addons/`,
which registers all `prof_*` commands automatically.

**If you edited `profiles.func` without restarting the server**

```text
sp_sc_func_load_file sofplus/addons/profiles.func
sp_sc_func_exec profiles_init
```

### Debug-Stress-Testing

1. `python3 userinfo_rcon.py --mint`
2. `prof_admin_add <guid> <nickname>`
3. Connect client → `prof_apply 0 <guid>`
4. `prof_enforce` — slot line should show `ok`
5. Team-menu collapse test — see **Rcon testing** (`swap` alias) or use `sp_sv_client_swap 0`

If `pending` and no `snapshot_*.cfg`, check export-fire, userinfo-rcon, and `RCON_PASSWORD`.

## Commands

Run in server console or rcon. All `prof_*` commands register on boot via
`profiles.func` (`sp_sc_func_alias`).

| Command | Args | What it does |
|---------|------|--------------|
| `prof_admin_add` | `<guid> <nickname>` | Add to roster; auto-saves |
| `prof_admin_del` | `<guid> <nickname>` | Remove by guid **or** nickname (other `""`) |
| `prof_apply` | `<slot> <guid>` | Push roster guid to slot |
| `prof_enforce` | — | Health-check every connected player — see below |
| `prof_get_slot_by_id` / `by_nick` | guid / nickname | → `_prof_found_slot` |
| `prof_register` | `<slot>` | Request snapshot for slot |
| `prof_admin_save` / `load` | — | Save/load `registry.cfg` |

### `prof_enforce` — check all players and fix lost guids

Walks every **connected, non-spectator** slot. For each one it reads the latest
`snapshot_<slot>.cfg` (from `dumpuser`) and compares `team_red_blue` to the admin
registry.

**Per-slot line** (one of):

| Status | Meaning | Action |
|--------|---------|--------|
| `ok` | Roster player's guid is present in `team_red_blue` | None — identity looks correct |
| `guest` | Not on the roster (no guid, or guid unknown to admin) | None |
| `wrong` | Roster player, but userinfo collapsed to bare `0`/`1` (team menu, swap, etc.) | **Fix:** stufftext `blue-/red-<guid>-<bit>` back onto the client |
| `pending` | No snapshot file yet | Requests a new snapshot (needs export-fire + userinfo_rcon) |

**Summary line** at the end, e.g.:

```text
enforce: 2 ok, 1 wrong, 1 pushed, 0 manual, 0 guests, 0 pending
```

- `pushed` — `wrong` slots that were stufftext-fixed
- `manual` — fix was printed but not sent (`_prof_use_stufftext 0`)
- `guests` / `pending` — counts from the table above

**When to run it:** after `prof_apply`, after a `swap` test, when you suspect a
roster player lost their guid, or any time you want a status report. Automatic
restore on userinfo change usually handles collapse without this, but `prof_enforce`
is the manual “scan everyone and repair” command.

## How it works

```
Admin registry (disk)                         Per-slot runtime (memory)
~reg_<guid> → nickname                        _prof_remembered_guid_<slot>  anchor (survives collapse)
~guid_by_<nickname_clean> → guid               _prof_guid_<slot>             last good snapshot
                                              _prof_nickname_<slot>         registry nickname
                                              _prof_is_registered_<slot>    1 = roster player on this slot
        │                                              │
        └──────────── fn_reg_lookup ─────────────────┘

team_red_blue = blue-<guid>-0 | red-<guid>-1   — or bare 0/1 after team menu / swap
              (legacy all-digit <guid><0|1> still accepted on read)
```

### Snapshots

A **snapshot** is `userinfo_rcon.py` running `rcon dumpuser <slot>` on the game
server, parsing the userinfo block, and writing
`sofplus/data/userinfo/snapshot_<slot>.cfg`. The server then execs that file
(`fn_userinfo_read`) after a short timer (see `_prof_userinfo_delay` below).

**Snapshot is requested when:**

| Trigger | Why |
|---------|-----|
| Player connects | `fn_client_begin` |
| `team_red_blue` changes | `_sp_sv_on_client_userinfo_change` → `fn_userinfo_changed` |
| `prof_register <slot>` | Manual refresh |
| `prof_apply` / `prof_enforce` | No snapshot yet, or retry after miss |

On userinfo change, `fn_try_restore_live` may re-push the remembered guid
**immediately** (no snapshot). The snapshot still runs for audit/bind and to
update `_prof_guid_<slot>` from dumpuser.

### `_prof_userinfo_delay` (default 200ms)

Used only in `fn_request_snapshot`: after `ext_trigger` fires, the server waits
this long before `fn_userinfo_read` execs `snapshot_<slot>.cfg`. That gap lets
`userinfo_rcon.py` finish `rcon dumpuser` and write the file — the game cannot
read a snapshot that does not exist yet.

If the file is still missing, `fn_userinfo_read` retries up to
`_prof_userinfo_tries` times (default 3) with the same delay. Lower the delay
for faster feedback; raise it only if you see frequent `pending` audits on a
slow host. Set `0` if you know snapshots are always ready instantly.

### Why `_prof_guid_<slot>` and `_prof_remembered_guid_<slot>`?

Both hold guid **digits only** (no team bit). They split **what userinfo says now**
from **who this slot is for the whole connection**:

| Cvar | Updated when | Purpose |
|------|----------------|---------|
| `_prof_guid_<slot>` | After each snapshot whose dumpuser `team_red_blue` parses as guid+bit | Mirror of last good dumpuser read |
| `_prof_remembered_guid_<slot>` | Once, on first valid guid (then kept until disconnect) | Anchor used to rebuild `team_red_blue` after collapse |

**Normal play** — client sends e.g. `blue-602380633711624767525303-0`. Both guid
cvars hold `602380633711624767525303` (digits only, no color prefix).

**After team menu / swap** — engine collapses userinfo to bare `0` or `1`. Snapshot
is no longer parseable as a guid (`_prof_guid_valid = 0`); `_prof_guid_<slot>` is
cleared or stale. `_prof_remembered_guid_<slot>` still holds
`602380633711624767525303`, so `fn_try_restore_live` / `fn_maybe_fixup` can
stufftext `blue-<remembered_guid>-0` or `red-<remembered_guid>-1` back.

Without two variables we would lose the guid the moment userinfo collapsed.

| Term | Meaning |
|------|---------|
| `guid` | 24-digit id inside `team_red_blue` (bearer token; not the color prefix) |
| `nickname` | Your label for that player in the registry |
| `nickname_clean` | `nickname` sanitised to `a-z0-9` (reverse lookup key) |
| **Roster player** | Someone you added with `prof_admin_add` — the server knows their guid and admin nickname. Anyone else connected is a **guest**. |
| `_prof_is_registered_<slot>` | `1` when this **slot** is currently a roster player (guid applied or confirmed via snapshot). Cleared on disconnect. |

**Flow:** `prof_admin_add` → `prof_apply` → userinfo change → dumpuser snapshot → lookup
`~reg_<guid>` → bind or re-push `_prof_remembered_guid_` on collapse. Mint:
`python3 userinfo_rcon.py --mint`.

## State

**Registry** (`profiles/registry.cfg`): `~reg_<guid>` → nickname,
`~guid_by_<nickname_clean>` → guid. Created on first `prof_admin_add` / save
(`sp_sc_cvar_save` creates the directory if missing).

**Per-slot:** `_prof_remembered_guid_`, `_prof_guid_`, `_prof_team_`,
`_prof_nickname_`, `_prof_is_registered_` (cleared on disconnect / map change).

## Team sources

| Source | Guid? | Use |
|--------|-------|-----|
| `team_red_blue` (dumpuser snapshot) | Yes | Audit, registry |
| `_sp_sv_info_client_team` | No | Team bit when re-pushing remembered guid |

## `userinfo_rcon.py`

Watches export-fire `userinfo` events → `rcon dumpuser <slot>` →
`snapshot_<slot>.cfg`. `RCON_PASSWORD` and paths: **Setup step 3**. Other env:
`EXPORT_FIRE_HOST`/`PORT`, `RCON_HOST`/`PORT`, `VERBOSE`.

## Rcon testing

Not needed for normal server operation. Use when testing over rcon if your client
cannot pass multi-argument `prof_*` commands reliably.

**Load `profiles_aliases.cfg`** (latch + `fn_*_entry` wrappers) in server console:

```text
sp_sc_exec_file /path/to/profiles_aliases.cfg
```

**Team-menu collapse test** — optional shortcut after the above:

```text
sp_sc_alias swap sp_sv_client_swap #{1}
swap 0
```

**Or** set `_prof_cli_*` and call an entry function directly:

```text
set _prof_cli_guid 602380633711624767525303
set _prof_cli_nickname slot0test
sp_sc_func_exec fn_admin_add_entry
```

See `profiles_aliases.cfg` for which `_prof_cli_*` cvars each `fn_*_entry` expects.

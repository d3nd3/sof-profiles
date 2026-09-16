# sof-profiles

Admin-assigned player GUID for SoF1, carried in `team_red_blue`.

## How it works

```
Admin registry (disk)                         Per-slot runtime (memory)
~reg_<guid> → nickname                        _prof_remembered_guid_<slot>  anchor (survives collapse)
~guid_by_<nickname_clean> → guid               _prof_guid_<slot>             last good snapshot
                                              _prof_nickname_<slot>         registry nickname
                                              _prof_is_registered_<slot>    1 = registered player
        │                                              │
        └──────────── fn_reg_lookup ─────────────────┘

team_red_blue = <guid digits><0|1>   — or bare 0/1 after team menu / swap
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

**Normal play** — client sends `6023806337116247675253030` (guid + blue). Both
cvars hold `602380633711624767525303`.

**After team menu / swap** — engine collapses userinfo to bare `0` or `1`. Snapshot
is no longer parseable as a guid (`_prof_guid_valid = 0`); `_prof_guid_<slot>` is
cleared or stale. `_prof_remembered_guid_<slot>` still holds
`602380633711624767525303`, so `fn_try_restore_live` / `fn_maybe_fixup` can
stufftext `remembered_guid + current_team_bit` back.

Without two variables we would lose the guid the moment userinfo collapsed.

| Term | Meaning |
|------|---------|
| `guid` | 24-digit id in `team_red_blue` (bearer token) |
| `nickname` | Your label for that player in the registry |
| `nickname_clean` | `nickname` sanitised to `a-z0-9` (reverse lookup key) |
| `_prof_is_registered_<slot>` | `1` when this slot is a known registered player |

**Flow:** register → apply → userinfo change → dumpuser snapshot → lookup
`~reg_<guid>` → bind or re-push `_prof_remembered_guid_` on collapse. Mint:
`python3 userinfo_rcon.py --mint`.

## Setup

Three pieces: game server (`.func` addons), **export-fire**, **userinfo_rcon.py**.
Optional **systemd** units in `systemd/` wrap the two Python services.

### Checklist

| # | What | Notes |
|---|------|--------|
| 1 | Deploy `profiles.func`, `ext_trigger.func` | `sofplus/addons/` (names not starting with `-`) |
| 2 | Symlink `user-<PORT>` → `User` | Port = game UDP port; triggers write under `user-<PORT>/` |
| 3 | `mkdir -p …/sofplus/data/profiles` | Registry dir for `registry.cfg` |
| 4 | Start userinfo_rcon (export-fire if needed) | `systemd/install.sh` or manual (below) |
| 5 | Server console setup | Once per server **restart** — see below |
| 6 | Verify | `prof_admin_add`, connect, `prof_apply`, `prof_enforce` |

**Persists on disk:** `profiles.func`, `registry.cfg`, addon files.  
**Redo each server restart:** console setup (step 5). SoFplus does not save `sp_sc_alias` to disk.  
**Redo if host reboots:** userinfo_rcon (and export-fire only if you manage it here).

### systemd (recommended)

**export-fire already running** (another feature on the same host):

```bash
./systemd/install.sh --rcon-only
# edit /etc/sof-profiles/env — SOF_PROFILES, RCON_PASSWORD; EXPORT_FIRE_HOST/PORT if not defaults
./systemd/install.sh --rcon-only
sudo systemctl enable --now userinfo-rcon
```

**Fresh host** (no export-fire yet):

```bash
./systemd/install.sh
# edit /etc/sof-profiles/env — SOF_USER_ROOT, SOF_EXPORT_FIRE, SOF_PROFILES, RCON_PASSWORD
./systemd/install.sh
sudo systemctl enable --now export-fire userinfo-rcon
```

Prerequisites: SoFplus + stufftext mod, Python 3.9+, a running **export-fire**
subscriber endpoint (from **sof-export-fire** or your existing setup), server rcon
enabled.

### Manual (no systemd)

```bash
# export-fire (sof-export-fire project)
python3 sof_export_fire.py --root "<SoF>/user-<PORT>" --serve 127.0.0.1:8765

# userinfo_rcon (this repo)
export RCON_PASSWORD='<rcon_password>'
python3 /path/to/userinfo_rcon.py
```

Snapshots land in `<SoF user>/sofplus/data/userinfo/snapshot_<slot>.cfg`.

### Server console setup (after each restart)

Open the **server console** or connect via **rcon** (in-game `rcon <password> …`, or any
rcon client). Run the following there. This is not a shell script — each line is a
command the **game server** must execute.

**What loads automatically**

When the server starts, SoFplus loads `profiles.func` from `sofplus/addons/`. That
registers hooks and three commands: `prof_admin_save`, `prof_admin_load`, `prof_enforce`.

**What you must run manually**

Commands like `prof_admin_add` and `prof_apply` live in `profiles_aliases.cfg`. That
file is **not** exec'd by the game. Copy its entire contents into the server
console/rcon and press Enter (one block paste is fine).

**1. Recommended cvar**

```text
set _sp_sv_limit_userinfo_change 1
```

**2. Optional `swap` shortcut** (handy for testing team-menu collapse)

```text
sp_sc_alias swap sp_sv_client_swap #{1}
```

**3. Register `prof_*` commands** — paste all lines from `profiles_aliases.cfg`:

```text
sp_sc_alias prof_register 'set _prof_cli_slot #{1}; sp_sc_func_exec fn_register_entry'
sp_sc_alias prof_get_slot_by_id 'set _prof_cli_guid #{1}; sp_sc_func_exec fn_get_slot_by_id_entry'
…
```

(The repo file has the full list; paste the whole file.)

After this you can type e.g. `prof_admin_add <guid> <nickname>` in console/rcon.

**If you edited `profiles.func` without restarting the server**

```text
sp_sc_func_load_file sofplus/addons/profiles.func
sp_sc_func_exec fn_profiles_init
```

You still need step 3 above after a full server restart — only `profiles.func`
reload is covered by the two lines here.

### Verify

1. `python3 userinfo_rcon.py --mint`
2. `prof_admin_add <guid> <nickname>`
3. Connect client → `prof_apply 0 <guid>`
4. `prof_enforce` — slot line should show `ok`
5. `swap 0` — auto-restore or run `prof_enforce` again; snapshot should show full guid+team

If `pending` and no `snapshot_*.cfg`, check export-fire, userinfo-rcon, and `RCON_PASSWORD`.

## Commands

Run in server console or rcon. Commands marked *paste* need `profiles_aliases.cfg`
run once after each server restart; others load from `profiles.func` on boot.

| Command | Args | What it does |
|---------|------|--------------|
| `prof_admin_add` *paste* | `<guid> <nickname>` | Register; auto-saves |
| `prof_admin_del` *paste* | `<guid> <nickname>` | Remove by guid **or** nickname (other `""`) |
| `prof_apply` *paste* | `<slot> <guid>` | Push registered guid to slot |
| `prof_enforce` | — | Health-check every connected player — see below |
| `prof_get_slot_by_id` / `by_nick` *paste* | guid / nickname | → `_prof_found_slot` |
| `prof_admin_save` / `load` | — | Save/load `registry.cfg` |

### `prof_enforce` — check all players and fix lost guids

Walks every **connected, non-spectator** slot. For each one it reads the latest
`snapshot_<slot>.cfg` (from `dumpuser`) and compares `team_red_blue` to the admin
registry.

**Per-slot line** (one of):

| Status | Meaning | Action |
|--------|---------|--------|
| `ok` | `team_red_blue` contains a registered guid | None — player identity looks correct |
| `guest` | Guid in userinfo is missing or not in registry | None — unregistered player |
| `wrong` | Registered player, but userinfo collapsed to bare `0`/`1` (team menu, swap, etc.) | **Fix:** stufftext `remembered_guid + team_bit` back onto the client |
| `pending` | No snapshot file yet | Requests a new snapshot (needs export-fire + userinfo_rcon) |

**Summary line** at the end, e.g.:

```text
enforce: 2 ok, 1 wrong, 1 pushed, 0 manual, 0 guests, 0 pending
```

- `pushed` — `wrong` slots that were stufftext-fixed
- `manual` — fix was printed but not sent (`_prof_use_stufftext 0`)
- `guests` / `pending` — counts from the table above

**When to run it:** after `prof_apply`, after a `swap` test, when you suspect a
registered player lost their guid, or any time you want a status report. Automatic
restore on userinfo change usually handles collapse without this, but `prof_enforce`
is the manual “scan everyone and repair” command.

**Rcon latch:**

```text
set _prof_cli_guid 602380633711624767525303
set _prof_cli_nickname slot0test
sp_sc_func_exec fn_admin_add_entry
```

## State

**Registry** (`profiles/registry.cfg`): `~reg_<guid>` → nickname,
`~guid_by_<nickname_clean>` → guid.

**Per-slot:** `_prof_remembered_guid_`, `_prof_guid_`, `_prof_team_`,
`_prof_nickname_`, `_prof_is_registered_` (cleared on disconnect / map change).

## Team sources

| Source | Guid? | Use |
|--------|-------|-----|
| `team_red_blue` (dumpuser snapshot) | Yes | Audit, registry |
| `_sp_sv_info_client_team` | No | Team bit when re-pushing remembered guid |

## Migration

Legacy `_prof_reg_*`, `_prof_nick_*`, `~nick_*`, `~id_by_label_*` → `~reg_*` /
`~guid_by_*` on load. Re-`prof_apply` after shortening 62-digit guids.

## `userinfo_rcon.py`

Watches export-fire `userinfo` events → `rcon dumpuser <slot>` →
`snapshot_<slot>.cfg`. Env: `RCON_PASSWORD` (required), `EXPORT_FIRE_HOST`/`PORT`,
`RCON_HOST`/`PORT`, `VERBOSE`.

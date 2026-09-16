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
| 5 | Rcon once per server process | `_sp_sv_limit_userinfo_change 1`, `swap` alias, paste `profiles_aliases.cfg` |
| 6 | Verify | `prof_admin_add`, connect, `prof_apply`, `prof_enforce` |

**Persists on disk:** `profiles.func`, `registry.cfg`, addon files.  
**Redo each server start:** `profiles_aliases.cfg` lines, `sp_sc_alias swap …`.  
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

### Rcon aliases (each process)

```text
set _sp_sv_limit_userinfo_change 1
sp_sc_alias swap sp_sv_client_swap #{1}
```

Paste `profiles_aliases.cfg`. Reload after editing `profiles.func`:

```text
sp_sc_func_load_file sofplus/addons/profiles.func
sp_sc_func_exec fn_profiles_init
```

### Verify

1. `python3 userinfo_rcon.py --mint`
2. `prof_admin_add <guid> <nickname>`
3. Connect client → `prof_apply 0 <guid>`
4. `prof_enforce` — slot line should show `ok`
5. `swap 0` — auto-restore or run `prof_enforce` again; snapshot should show full guid+team

If `pending` and no `snapshot_*.cfg`, check export-fire, userinfo-rcon, and `RCON_PASSWORD`.

## Commands

| Command | Args | What it does |
|---------|------|--------------|
| `prof_admin_add` | `<guid> <nickname>` | Register; auto-saves |
| `prof_admin_del` | `<guid> <nickname>` | Remove by guid **or** nickname (other `""`) |
| `prof_apply` | `<slot> <guid>` | Push registered guid to slot |
| `prof_enforce` | — | Audit all slots (per-slot lines) and stufftext-fix wrong `team_red_blue` |
| `prof_get_slot_by_id` / `by_nick` | guid / nickname | → `_prof_found_slot` |

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

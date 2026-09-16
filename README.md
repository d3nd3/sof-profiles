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
(`fn_userinfo_read`) — typically ~1s later (`_prof_userinfo_delay`, default 1000ms).

**Snapshot is requested when:**

| Trigger | Why |
|---------|-----|
| Player connects | `fn_client_begin` |
| `team_red_blue` changes | `_sp_sv_on_client_userinfo_change` → `fn_userinfo_changed` |
| `prof_register <slot>` | Manual refresh |
| `prof_apply` / `prof_audit` / `prof_enforce` | No snapshot yet, or retry after miss |

On userinfo change, `fn_try_restore_live` may re-push the remembered guid
**immediately** (no snapshot). The snapshot still runs for audit/bind and to
update `_prof_guid_<slot>` from dumpuser.

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

**There is no install script in this repo.** Setup is manual. Three pieces must
run together: the game server (with `.func` addons), **export-fire** (watches
trigger files), and **userinfo_rcon.py** (dumpuser snapshots).

### What is automated vs manual

| Step | Automated? | How |
|------|------------|-----|
| Load `ext_trigger.func` | Yes | SoFplus `spf_sc_addons_init` loads `sofplus/addons/*.func` and calls `ext_trigger_init` |
| Load `profiles.func` | Yes | Same loader calls `profiles_init` → `fn_profiles_init` |
| export-fire server | **No** | You start `sof_export_fire.py` yourself |
| `userinfo_rcon.py` | **No** | You start it yourself (systemd, screen, etc.) |
| `profiles_aliases.cfg` | **No** | Paste into rcon **once per server process** |
| `sp_sc_alias swap …` | **No** | Paste into rcon **once per server process** |
| Registry on disk | Partial | `fn_profiles_init` runs `fn_admin_load`; add players with `prof_admin_add` |

Files in `sofplus/addons/` whose name starts with `-` are **skipped** by the
addon loader (SoFplus convention for disabled addons).

### Prerequisites

- SoF1 server with **SoFplus** and the buddy **stufftext** mod (server must be
  able to `stufftext` `team_red_blue` to clients).
- **Python 3.9+** on the host running export-fire and `userinfo_rcon.py`.
- **sof-export-fire** (sibling project; provides `sof_export_fire.py`).
- Server **rcon** enabled; you need `rcon_password` for `userinfo_rcon.py`.
- Know your game **UDP port** (e.g. `28921`) — used for `user-<PORT>` paths and
  rcon.

### 1. Deploy files

Copy into the server's SoF **user tree** (Wine path example):

```text
<SoF user>/sofplus/addons/profiles.func
<SoF user>/sofplus/addons/ext_trigger.func
```

Keep a copy of `userinfo_rcon.py` and `profiles_aliases.cfg` somewhere on the
host (repo checkout is fine). They are not loaded by the game directly.

Create the registry directory if missing (first save also creates the file):

```bash
mkdir -p "<SoF user>/sofplus/data/profiles"
```

### 2. Per-server `user-<PORT>` symlink

export-fire and triggers write under `user-<PORT>/sofplus/data/`, not a generic
`User/` folder. For each game instance:

```bash
cd "<SoF root>"
ln -sfn User "user-<PORT>"    # e.g. user-28921 -> User
```

Use the **game UDP port** in the name. `userinfo_rcon.py` reads the port from
the trigger path (`user-28921` → rcon port `28921` when `RCON_PORT=0`).

### 3. Start export-fire

From the **sof-export-fire** project, point `--root` at that server's user tree
(the symlinked `user-<PORT>` directory):

```bash
python3 sof_export_fire.py --root "<SoF root>/user-<PORT>" --serve 127.0.0.1:8765
```

Leave this running. It watches `sofplus/data/ext_trigger/*.cfg` and pushes
`userinfo` events to subscribers.

Defaults match `userinfo_rcon.py`: host `127.0.0.1`, port `8765`.

### 4. Start userinfo_rcon.py

In another terminal (same host as export-fire is simplest):

```bash
export RCON_PASSWORD='<server rcon_password>'
# optional if game is not on localhost:
# export RCON_HOST=127.0.0.1
# export RCON_PORT=28921
# optional if export-fire is not on defaults:
# export EXPORT_FIRE_HOST=127.0.0.1
# export EXPORT_FIRE_PORT=8765

python3 /path/to/userinfo_rcon.py
```

With `RCON_PORT=0` (default), each trigger event supplies the port from the
`user-<PORT>` path. Set `VERBOSE=1` to log each dumpuser round-trip.

On success you should see `subscribing to userinfo on …` and, when players
connect or change team, files appearing under:

```text
<SoF user>/sofplus/data/userinfo/snapshot_<slot>.cfg
```

### 5. Server cvars and aliases (each process)

In **server rcon or console**, once after each server start:

```text
set _sp_sv_limit_userinfo_change 1
sp_sc_alias swap sp_sv_client_swap #{1}
```

Then paste the full contents of `profiles_aliases.cfg` (registers `prof_admin_add`,
`prof_apply`, etc.). `sp_sc_alias` does not persist across restarts.

### 6. Load or reload profiles

If both `.func` files are already in `sofplus/addons/`, a normal SoFplus boot
runs `profiles_init` automatically. After editing `profiles.func` without a
full restart:

```text
sp_sc_func_load_file sofplus/addons/profiles.func
sp_sc_func_exec fn_profiles_init
```

`fn_profiles_init` registers hooks, aliases for `prof_admin_save` / `load` /
`audit` / `enforce`, and loads `sofplus/data/profiles/registry.cfg`.

### 7. Verify

1. **Mint a guid:** `python3 userinfo_rcon.py --mint`
2. **Register:** `prof_admin_add <guid> <nickname>` (or latch + `fn_admin_add_entry`)
3. **Connect a test client**, then: `prof_apply 0 <guid>`
4. **Audit:** `sp_sc_func_exec prof_audit` — slot should show `ok`
5. **Swap test:** `swap 0` (needs alias above); `prof_enforce` or wait for auto
   restore; `dumpuser` / snapshot should show full guid+team again

If audit shows `pending` and no `snapshot_*.cfg` files appear, the chain is
broken — check export-fire is running, `userinfo_rcon.py` is connected, and
`RCON_PASSWORD` matches the server.

### 8. After server restart

| Must redo | Persists on disk |
|-----------|------------------|
| `profiles_aliases.cfg` lines | `profiles.func`, registry.cfg |
| `sp_sc_alias swap …` | `ext_trigger.func` |
| Restart export-fire + userinfo_rcon.py | Registered guids in registry |

Functions are `fn_*`; console commands stay `prof_*` via aliases and
`sp_sc_func_alias`.

## Commands

| Command | Args | What it does |
|---------|------|--------------|
| `prof_admin_add` | `<guid> <nickname>` | Register; auto-saves |
| `prof_admin_del` | `<guid> <nickname>` | Remove by guid **or** nickname (other `""`) |
| `prof_apply` | `<slot> <guid>` | Push registered guid to slot |
| `prof_audit` / `prof_enforce` | — | Status / fix collapsed `team_red_blue` |
| `prof_find_by_id` / `by_name` | guid / nickname | → `_prof_found_slot` |

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

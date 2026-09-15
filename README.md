# sof-profiles

Admin-assigned player identity for SoF1 servers, carried in `team_red_blue`.

## Idea

`team_red_blue` holds `<62 random digits><team 0/1>`. The game reads it
with `atoi` and uses the smallest bit (0 = blue, 1 = red); parity survives
`atoi` overflow, so the last char stays the team bit and the leading digits
are the player identity (~206 bits). Budget, worst case not typical case:
keys/values cap at 63 usable chars, so a maxed `team_red_blue` costs
13 + 63 + 2 = 78 of the 512 info-string bytes, leaving a guaranteed 434
for everything else (the engine rejects over-budget sets instead of
truncating, so overflow is impossible). With the default 14-key block
(146 bytes fixed overhead) the other values share ~303 bytes; shortening
the token would free only ~30 bytes there, so the full 62 digits stay.

The admin owns the nickname -> identity registry. The server never invents
bindings and never pushes without an admin order; player-side notices never
print another identity's key.

## Layout

- `profiles.func`: in-game side (hooks, registry, audit/apply/enforce).
  Drop into `sofplus/addons/` next to `ext_trigger.func`.
- `ext_trigger.func`: generic trigger transport (canonical source, also here).
- `userinfo_rcon.py`: external side. Subscribes to `userinfo` trigger events
  on the sof-export-fire socket bus, sends UDP `rcon dumpuser <slot>`,
  parses the reply, and writes `sofplus/data/userinfo/snapshot_<slot>.cfg`
  for the script to exec back. Stdlib only.

## Setup

1. Copy `profiles.func` and `ext_trigger.func` to
   `user-<PORT>/sofplus/addons/` (or `base/sofplus/addons/`). Requires the
   buddy `stufftext` mod for server-pushed keys; without it everything
   degrades to printed `set ... u` instructions.
2. Run one sof-export-fire server per SoF root:
   `python sof_export_fire.py --root <SoF root> --serve 127.0.0.1:8765`.
3. Run `userinfo_rcon.py` with `RCON_PASSWORD` set (server
   `rcon_password`). Optional env: `EXPORT_FIRE_HOST/PORT`, `RCON_HOST`,
   `RCON_PORT` (0 = game port from the event), `RCON_TIMEOUT/QUIET`,
   `EXTRA_USERINFO_KEYS`, `VERBOSE=1`.
4. Set `_sp_sv_limit_userinfo_change 1` on the server (see below).

## Admin workflow

Console calls drop function args, so admin input goes through input cvars
first (`set` keeps full values, including 62-digit identities):

```text
python3 userinfo_rcon.py --mint        # secrets-minted 62-digit identity
set _prof_admin_nick <nickname>
set _prof_admin_id <identity>
prof_admin_add                         # register (autosaves registry.cfg)
prof_audit                             # report: ok / wrong (slot, name, value) / guest / pending
set _prof_admin_slot <slot>
prof_apply                             # push the REGISTERED key now (refuses unknowns)
prof_enforce                           # audit + push fixes for registered mismatches
set _prof_admin_nick <nickname>
prof_admin_del                         # unregister (autosaves)
```

Nickname keys are sanitized (colors stripped, only `0-9a-z` kept, same
convention as `spf_sv_rcon`): `Bob` registers as `ob`, all-caps names are
rejected. Per-slot state for other addons: `_prof_id_<slot>`,
`_prof_team_<slot>`, `_prof_name_<slot>`, `_prof_member_<slot>` (1 only for
registry-verified bindings). Lookups: `prof_find_by_id`,
`prof_find_by_name` (-> `_prof_found_slot`). Hook: `set
_prof_userinfo_hook myfunc`.

## Why `_sp_sv_limit_userinfo_change 1`

Every userinfo change fires a snapshot round-trip (file event, socket
dispatch, UDP rcon, snapshot write, delayed re-read). Unlimited changes let
one client flood that pipeline, and let a player flap `team_red_blue`
faster than enforcement converges — auditing a moving target. Limit 1 makes
the server warn and ignore rapid changes, which keeps the event rate bounded
and gives `prof_audit` / `prof_enforce` a stable value to check and fix.

## Trust notes

- Userinfo is visible to connected players, so identity keys protect
  against outsiders and casual spoofing, not against a determined sniffer
  already on the server. Treat keys as bearer tokens among players.
- Mismatch notices to players are generic on purpose; assigned keys are
  only ever unicast to the holder's own slot (or pushed silently).
- Pushes always preserve the known team digit and refuse unknown teams
  (snapshot requested instead) — the server never flips teams blind.

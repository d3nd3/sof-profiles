# sof-profiles

Admin-assigned player identity for SoF1 servers, carried in `team_red_blue`.

## Idea

`team_red_blue` holds `<62 random digits><team 0/1>`. The game reads it
with `atoi` and uses the smallest bit (0 = blue, 1 = red); parity survives
`atoi` overflow, so the last char stays the team bit and the leading digits
are the player identity (~206 bits).

The admin registers **identities** and optional **labels** (human nicknames
the admin chooses). Client userinfo `name` is never used for matching — a
player may have an empty name. Recognition is purely by the identity digits
presented in `team_red_blue`.

## Layout

- `profiles.func`: in-game side (hooks, registry, audit/apply/enforce).
- `ext_trigger.func`: generic trigger transport.
- `userinfo_rcon.py`: external snapshot bridge (UDP `rcon dumpuser`).

## Setup

1. Copy `profiles.func` and `ext_trigger.func` to `sofplus/addons/`.
   Requires buddy `stufftext` mod for server-pushed keys.
2. Symlink `user-<PORT>` → `User` in the SoF root. Run export-fire with
   `--root <SoF root>/user-<PORT>` (not the full game tree):
   `python sof_export_fire.py --root <SoF root>/user-<PORT> --serve 127.0.0.1:8765`.
3. Run `userinfo_rcon.py` with `RCON_PASSWORD` set.
4. Set `_sp_sv_limit_userinfo_change 1` on the server.
5. For remote rcon `sp_sc_func_exec`:
   `sp_sc_alias swap 'sp_sv_client_swap #{1}'`

## Registry

Stored in `sofplus/data/profiles/registry.cfg`:

- `_prof_reg_<62digit_id>` = admin label (may be empty)
- `_prof_nick_<skey>` = id digits (label index; `skey` = sanitized label)

## Admin workflow

```text
python3 userinfo_rcon.py --mint        # mint 62-digit identity
set _prof_admin_id <identity>
set _prof_admin_nick <label>          # optional admin label
prof_admin_add                         # register (autosaves)
prof_audit                             # ok / wrong / guest / pending per slot
set _prof_admin_slot <slot>
set _prof_admin_id <identity>
prof_apply                             # push registered identity to slot
prof_enforce                           # fix registered stash collapses (bare 0/1)
set _prof_admin_id <identity>          # or set _prof_admin_nick <label>
prof_admin_del
```

## Per-slot state (for other addons)

- `_prof_id_<slot>` — identity digits latched from userinfo
- `_prof_team_<slot>` — team bit 0/1
- `_prof_label_<slot>` — admin label when `_prof_member_<slot>` is 1
- `_prof_member_<slot>` — 1 only when presenting a registered identity

Lookups: `prof_find_by_id` (live slots), `prof_find_by_name` (by admin
label in registry, not client name). Hook: `set _prof_userinfo_hook myfunc`.

## Team collapse (menu swap, sp_sv_client_red/blue)

When `team_red_blue` collapses to bare `0`/`1`, run `prof_enforce` to push
the stashed registered identity (never auto-stufftext on the userinfo hook —
that caused a feedback loop). `_prof_snap_busy_<slot>` coalesces snapshot
round-trips while one is in flight.

## Trust notes

Identity keys are bearer tokens in visible userinfo — good against casual
spoofing, not against sniffers. Mismatch notices are generic; assigned keys
only go to the holder's slot.

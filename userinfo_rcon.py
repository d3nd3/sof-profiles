"""External side of the profiles.func userinfo round-trip.

Flow: sof-export-fire dispatches ``userinfo`` trigger events (fired by
``prof_userinfo_changed`` in profiles.func). For each event this helper
sends UDP ``rcon dumpuser <slot>`` to the game server, parses the reply
into userinfo cvars, and writes them back as
``<data_dir>/userinfo/snapshot_<slot>.cfg`` lines of
``set "_prof_userinfo_<key>" "<value>"``. The .func side execs that file
(``sp_sc_exec_file``), which puts the values back into script cvars.

Wire protocol: speaks the sof-export-fire socket protocol (newline-delimited
JSON over TCP; see sof-export-fire/sof_export_fire.py). Requires a running
export-fire server (``--serve``).

Config via environment:

- EXPORT_FIRE_HOST (default 127.0.0.1), EXPORT_FIRE_PORT (default 8765)
- RCON_HOST (default 127.0.0.1): game server host
- RCON_PORT (default 0): game server UDP port; 0 = take it from the event
  (the ``user-<port>`` number, which is the game port)
- RCON_PASSWORD (required): server ``rcon_password``; never logged
- RCON_TIMEOUT (default 2.0): overall UDP wait, seconds
- RCON_QUIET (default 0.5): stop collecting after this long without packets
- EXTRA_USERINFO_KEYS (default ""): comma-separated extra known keys, for
  long custom userinfo cvars whose values glue to the key in the dump
- VERBOSE (default 0): set to 1 for per-event logging

Requires: Python 3.9+, stdlib only.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import select
import socket
import sys
import time
from dataclasses import dataclass
from typing import Dict, Optional

OOB = b"\xff\xff\xff\xff"

# Keys seen in real `rcon dumpuser` output (SoF userinfo block). Used to
# disambiguate fixed-width lines whose key fills the whole column, e.g.
# "allow_download_models1" (21-char key, value glued, no whitespace).
KNOWN_USERINFO_KEYS = frozenset({
    "predicting", "spectator_password", "password", "cl_violence",
    "spectator", "fov", "msg", "rate", "allow_download_models",
    "team_red_blue", "name", "teamname", "skin", "ip",
})

# Identity token budget (see Info_SetValueForKey limits in the engine):
# keys/values may be at most 63 usable chars (rejected above that) with no
# '"', ';', '\\' and ASCII-only -- digits satisfy all of that. The game
# reads team_red_blue with atoi and uses the smallest bit (0 = blue,
# 1 = red); parity survives atoi's 32-bit wraparound for arbitrarily long
# digit strings ((N mod 2^32) mod 2 == N mod 2 == last digit parity), so
# the last char stays the team bit and everything before it is identity.
# Worst case, not typical case: a 63-char value costs 13 (key) + 63 + 2
# (backslashes) = 78 bytes of the 512-byte MAX_INFO_STRING, leaving a
# guaranteed 434 bytes for the rest -- overflow is impossible because the
# engine rejects over-budget sets instead of truncating. With the default
# 14-key block (118 key chars + 28 backslashes = 146 fixed overhead) the
# other 13 values share 512 - 146 - 63 = 303 bytes (~23 avg); shortening
# the token would free only ~30 bytes there, so we keep the longest legal
# 62 identity digits (~206 bits) + team bit.
MAX_USERINFO_VALUE = 63
IDENTITY_DIGITS = MAX_USERINFO_VALUE - 1  # 62


def parse_dumpuser(text: str, extra_keys=None, on_ambiguous=None) -> Dict[str, str]:
    """Parse `rcon dumpuser` output into {cvar: value}.

    The dump prints ``userinfo`` / ``--------`` headers, then one cvar per
    line with the key padded to 20 columns (no separator, so keys of 20+
    chars glue to their value). Returns {} when the text is not a dump.

    Unseen keys (e.g. a player-side ``set newuserinfocvar val u``) parse
    fine whenever the line carries whitespace -- which the padding
    guarantees for every key shorter than 20 chars, the realistic case.
    The one ambiguous shape is a whitespace-free line longer than 20 chars
    with an *unknown* 20+ char key: it is indistinguishable from a bare
    long key with an empty value (the ``%-20s%s`` format glues exactly
    then), so the whole line is kept as the key. Cover such keys
    deterministically via ``extra_keys``; ambiguous lines are reported
    through ``on_ambiguous`` when given.
    """
    known = KNOWN_USERINFO_KEYS | set(extra_keys or ())
    prefixes = sorted(known, key=len, reverse=True)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not any(ln.strip() == "userinfo" for ln in lines):
        return {}
    info: Dict[str, str] = {}
    for raw in lines:
        stripped = raw.strip()
        if stripped in ("userinfo", "--------"):
            continue
        key: Optional[str] = None
        value = ""
        if len(raw) > 20 and raw[:20].strip() in known:
            key, value = raw[:20].strip(), raw[20:].strip()
        elif re.search(r"\s", stripped):
            key, value = re.split(r"\s+", stripped, maxsplit=1)
        else:
            for known_key in prefixes:
                if stripped.startswith(known_key) and len(stripped) > len(known_key):
                    key, value = known_key, stripped[len(known_key):].strip()
                    break
            else:
                key, value = stripped, ""
                if len(stripped) > 20 and on_ambiguous is not None:
                    on_ambiguous(stripped)
        info[key] = value
    return info


def sanitize_key(key: str) -> str:
    key = re.sub(r"[^A-Za-z0-9_]", "_", key)
    return key or "unknown"


def sanitize_value(value: str) -> str:
    return value.replace("\r", "").replace("\n", "").replace('"', "'")


def split_identity(value: str):
    """Split a team_red_blue value into (identity, team, valid).

    Valid identity form: all digits, >= 2 chars, last char 0/1 (the team
    bit the game reads via atoi). ``team`` mirrors the game (atoi low bit)
    for any all-digit value -- so legacy single-digit "0"/"1" still yield
    their team -- and is -1 otherwise. Trust model: trust-on-first-use;
    whoever controls the client userinfo owns the binding, and guessing
    a 62-digit token is infeasible.
    """
    if value and re.fullmatch(r"[0-9]+", value):
        team = int(value[-1]) & 1
        if len(value) >= 2 and value[-1] in ("0", "1"):
            return (value[:-1], team, True)
        return ("", team, False)
    return ("", -1, False)


def mint_identity(n: int = IDENTITY_DIGITS) -> str:
    """Mint n random decimal identity digits (secrets CSPRNG)."""
    return "".join(str(secrets.randbelow(10)) for _ in range(n))


def build_snapshot(slot: int, info: Dict[str, str], extra=None) -> str:
    """Render snapshot cfg lines the .func side execs back into cvars.

    ``info`` keys become _prof_userinfo_<key>; ``extra`` keys are used
    verbatim (already full cvar names like _prof_id).
    """
    lines = ['set "_prof_userinfo_slot" "%d"' % slot]
    for key in sorted(info):
        lines.append('set "_prof_userinfo_%s" "%s"'
                     % (sanitize_key(key), sanitize_value(info[key])))
    for name in sorted(extra or {}):
        lines.append('set "%s" "%s"' % (name, sanitize_value(str(extra[name]))))
    return "\n".join(lines) + "\n"


def rcon(host: str, port: int, password: str, command: str,
         timeout: float = 2.0, quiet: float = 0.5) -> str:
    """Send one UDP rcon command; collect `print` reply bodies as text."""
    packet = b"%srcon %s %s" % (OOB, password.encode("latin-1"),
                                command.encode("latin-1"))
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.sendto(packet, (host, port))
        deadline = time.time() + timeout
        chunks = []
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            sock.settimeout(min(quiet, remaining))
            try:
                data, _ = sock.recvfrom(65536)
            except socket.timeout:
                if chunks:
                    break  # burst over
                continue  # keep waiting for the first packet
            if not data.startswith(OOB):
                continue
            body = data[len(OOB):]
            if body.startswith(b"print\n"):
                body = body[len(b"print\n"):]
            chunks.append(body)
        return b"".join(chunks).decode("latin-1")
    finally:
        sock.close()


def log(msg: str) -> None:
    print("userinfo_rcon: %s" % msg, flush=True)


@dataclass
class Config:
    fire_host: str = "127.0.0.1"
    fire_port: int = 8765
    rcon_host: str = "127.0.0.1"
    rcon_port: int = 0  # 0 = game port from the event (user-<port>)
    rcon_password: str = ""
    rcon_timeout: float = 2.0
    rcon_quiet: float = 0.5
    extra_keys: tuple = ()
    verbose: bool = False

    @classmethod
    def from_env(cls) -> "Config":
        get = os.environ.get
        return cls(
            fire_host=get("EXPORT_FIRE_HOST", "127.0.0.1"),
            fire_port=int(get("EXPORT_FIRE_PORT", "8765")),
            rcon_host=get("RCON_HOST", "127.0.0.1"),
            rcon_port=int(get("RCON_PORT", "0")),
            rcon_password=get("RCON_PASSWORD", ""),
            rcon_timeout=float(get("RCON_TIMEOUT", "2.0")),
            rcon_quiet=float(get("RCON_QUIET", "0.5")),
            extra_keys=tuple(k for k in
                             (s.strip() for s in get("EXTRA_USERINFO_KEYS", "").split(",")) if k),
            verbose=get("VERBOSE", "0") not in ("", "0"),
        )


def handle_event(event: dict, cfg: Config) -> Optional[str]:
    """Process one `userinfo` event; return the snapshot path or None."""
    try:
        slot = int(str(event.get("slot", "")))
    except (TypeError, ValueError):
        log("ignoring event with bad slot: %r" % (event.get("slot"),))
        return None
    if not 0 <= slot <= 255:
        log("ignoring out-of-range slot: %d" % slot)
        return None
    game_port = cfg.rcon_port
    if not game_port:
        try:
            game_port = int(str(event.get("port", "")))
        except (TypeError, ValueError):
            game_port = 0
    if not game_port:
        log("no game port for slot %d" % slot)
        return None
    data_dir = event.get("data_dir", "")
    if not data_dir:
        log("no data_dir for slot %d" % slot)
        return None
    text = rcon(cfg.rcon_host, game_port, cfg.rcon_password,
                "dumpuser %d" % slot,
                timeout=cfg.rcon_timeout, quiet=cfg.rcon_quiet)
    on_ambiguous = None
    if cfg.verbose:
        def on_ambiguous(line: str, _slot: int = slot) -> None:
            log("slot %d: ambiguous userinfo line kept whole: %r" % (_slot, line))
    info = parse_dumpuser(text, extra_keys=cfg.extra_keys,
                          on_ambiguous=on_ambiguous)
    if not info:
        log("empty userinfo for slot %d (rcon ok=%s)" % (slot, bool(text)))
        return None
    identity, team, valid = split_identity(info.get("team_red_blue", ""))
    derived = {
        "_prof_id_valid": "1" if valid else "0",
        "_prof_id": identity,
        "_prof_team": str(team),
        # Fresh candidate for unregistered players; the .func offers it
        # once for `set team_red_blue <id><team> u`. Minted even for
        # garbage values (team digit resolved script-side).
        "_prof_id_new": "" if valid else mint_identity(),
    }
    path = os.path.join(data_dir, "userinfo", "snapshot_%d.cfg" % slot)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="latin-1") as fh:
        fh.write(build_snapshot(slot, info, extra=derived))
    os.replace(tmp_path, path)  # atomic: .func never execs a half file
    if cfg.verbose:
        log("slot %d -> %s (%d cvars, id valid=%s)"
            % (slot, path, len(info), valid))
    return path


def run_forever(cfg: Config) -> None:
    """Subscribe to `userinfo` triggers; block forever with reconnects."""
    backoff = 1.0
    state: Dict[str, bool] = {}
    while True:
        state.clear()
        try:
            _session(cfg, state)
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # noqa: BLE001
            log("export-fire connection lost (%s); retry in %.1fs" % (exc, backoff))
        backoff = 1.0 if state.get("established") else min(backoff * 2, 30.0)
        time.sleep(backoff)


def _session(cfg: Config, state: Dict[str, bool]) -> None:
    sock = socket.create_connection((cfg.fire_host, cfg.fire_port), timeout=5.0)
    try:
        sock.settimeout(None)
        buf = bytearray()

        def read_line(timeout: float) -> Optional[str]:
            while True:
                nl = buf.find(b"\n")
                if nl >= 0:
                    line = bytes(buf[: nl + 1])
                    del buf[: nl + 1]
                    return line.decode("utf-8")
                ready, _, _ = select.select([sock], [], [], timeout)
                if not ready:
                    return None
                try:
                    chunk = sock.recv(65536)
                except OSError as exc:
                    raise ConnectionError("recv failed: %s" % exc)
                if not chunk:
                    raise ConnectionError("server closed connection")
                buf.extend(chunk)

        hello = read_line(5.0)
        if hello is None:
            raise ConnectionError("timed out waiting for hello")
        json.loads(hello)
        state["established"] = True
        sock.sendall(b'{"subscribe": ["userinfo"]}\n')
        ping_id = 0
        while True:
            line = read_line(30.0)
            if line is None:
                ping_id += 1
                sock.sendall((json.dumps({"ping": ping_id}) + "\n").encode())
                reply = read_line(5.0)
                if reply is None:
                    raise ConnectionError("pong timed out")
                pong = json.loads(reply)
                if not isinstance(pong, dict) or pong.get("pong") != ping_id:
                    raise ConnectionError("pong mismatch: %r" % reply)
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if isinstance(msg, dict) and msg.get("event") is not None:
                try:
                    handle_event(msg["event"], cfg)
                except Exception:  # noqa: BLE001
                    log("event handling failed")
    finally:
        try:
            sock.close()
        except OSError:
            pass


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--mint":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else IDENTITY_DIGITS
        if n <= 0:
            print("userinfo_rcon: --mint needs a positive length", file=sys.stderr)
            raise SystemExit(2)
        print(mint_identity(n))
        return
    cfg = Config.from_env()
    if not cfg.rcon_password:
        print("userinfo_rcon: RCON_PASSWORD is required", file=sys.stderr)
        raise SystemExit(2)
    log("subscribing to userinfo on %s:%d; rcon %s:* (port per event)"
        % (cfg.fire_host, cfg.fire_port, cfg.rcon_host))
    run_forever(cfg)


if __name__ == "__main__":
    main()

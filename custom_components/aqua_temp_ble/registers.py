"""Single source of truth for the Aqua Temp register mapping and calibration.

Every label<->register and value<->meaning decision lives HERE and nowhere
else. The static APK analysis (FINDINGS.md §11.4 / §12) established this map
at ~80% confidence; the live probe tool (the "Probe registers" button and
the `aqua_temp_ble.probe_registers` service, see docs/CALIBRATION.md) closes
the remaining gap. After a live calibration session, correcting a label =
editing one entry below + restarting Home Assistant.

Spec 003 (specs/003-sensor-coverage/) extends this surface with the seven
parameter families observed in the vendor app's parameter view
(T/H/R/P/S/O/D — see FAMILIES below). The decode rules (sentinel set,
plausibility guard) and the write allowlist
(ensure_writable) are normative in the spec's contracts; the H/R/P installer
families are deliberately NOT exposed as entities and never writable.

This module must stay Home-Assistant-free (stdlib only) so the standalone
tools (tools/smoke_client.py) and the unit tests can import it.

Status legend used throughout this repo:
  [P] PROVEN — recovered from the APK and CRC-verified ([P apk]), or observed
      in the vendor app's parameter view ([P panel], owner's photographs,
      2026-10)
  [A] ASSUMED — reasonable default, cheap to correct after calibration
  [V] TO-VERIFY — cannot be known without one live session with the unit
"""

from __future__ import annotations

from dataclasses import dataclass

# --- base read block (proven frame 63030007002d3c54) ---
BASE_BLOCK_START = 7   # [P]
BASE_BLOCK_COUNT = 45  # [P] -> registers 7..51

# --- [P] verified base-block registers ---
REG_POWER = 8                 # 0=off, 1=on
REG_BOOST = 11                # future use, read-only here
REG_MODE = 13                 # cool/heat/auto, values below [A]
REG_SET_CURRENT_TEMP = 14     # current setpoint, x10
REG_SET_COOL_TARGET = 19      # x10
REG_SET_HEAT_TARGET = 20      # x10
REG_SET_AUTO_TARGET = 21      # x10
REG_COOL_MIN_TARGET = 22      # x10
REG_COOL_MAX_TARGET = 23      # x10
REG_HEAT_MIN_TARGET = 24      # x10
REG_HEAT_MAX_TARGET = 25      # x10
REG_INLET_WATER_TEMP = 26     # x10, signed
REG_OUTLET_WATER_TEMP = 27    # x10, signed
REG_AMBIENT_TEMP = 28         # x10, signed
REG_FAULT1 = 47
REG_FIRMWARE_FIRST = 48       # 48..51 [V] semantics
REG_FIRMWARE_LAST = 51

# --- [V] TO-VERIFY placeholders: set the register number after calibration
# (docs/CALIBRATION.md, spec 003 calibration-contract.md). None = entity
# not created yet, so no unverified data can ever be displayed under a name.
# Family codes refer to the vendor app's parameter view (FAMILIES below).
# Temperature placeholders only: the binary-sensor family (O/S switches)
# was pruned in the spec-021 cleanup — their base registers were never
# pinned and no pushed home exists, so no entity can ever be created.
REG_SUCTION_TEMP: int | None = None     # T01 Suction, x10, signed
REG_COIL_TEMP: int | None = None        # T04 Coil, x10, signed
REG_EXHAUST_TEMP: int | None = None     # T06 Exhaust, x10, signed

# --- [A] value mappings (verify via runbook §4.4 step M) ---
# Rationale for this order: the sequential register allocation follows
# Cool->Heat->Auto (regs 19/20/21), so the Mode enum likely shares that order.
MODE_VALUE_TO_NAME: dict[int, str] = {0: "cool", 1: "heat", 2: "auto"}

TEMP_SCALE = 10.0         # [P] value = int(degrees_C * 10)
FALLBACK_MIN_TARGET = 5.0   # [A] if bound registers are missing/sentinel
FALLBACK_MAX_TARGET = 40.0  # [A]

# --- sentinels & plausibility (spec 003, decode-contract.md §2) ---
# Two "sensor absent" conventions exist on the wire; both decode to None so
# the state shows "unknown" and the vendor's own display bug (3276.5 degC for
# an absent sensor) can never happen here:
#   -9990  [P apk]   the app's Status page defines -999.0 degC as
#                    "sensor absent" (FINDINGS.md §12);
#   32765  [P panel] 0x7FFD, two below the int16 maximum — observed for the
#                    absent T01 Suction sensor on the owner's unit (2026-10),
#                    which the vendor app renders as 3276.5 degC.
SENTINEL_RAWS: frozenset[int] = frozenset({-9990, 32765})
SENTINEL_RAW = -9990  # [P] legacy alias, the -9990 member of the set (FR-14)

# Plausibility window (FR-2): a temperature raw outside these bounds decodes
# to None -> state "unknown" + one warning per address. Real values always
# pass: the highest legitimate system temperature observed is the H04
# exhaust-high safety threshold at 105.0 degC [P panel] (raw 1050), and
# winter ambients stay far above -45.0 degC (raw -450). The window also
# catches unknown sentinel conventions (e.g. 0x7FFF = 32767) and mis-decoded
# garbage (e.g. byte-swapped 0xAA00 = 43520).
TEMP_PLAUSIBLE_MIN_RAW = -450   # -> -45.0 degC
TEMP_PLAUSIBLE_MAX_RAW = 1500   # -> +150.0 degC

# --- calibration switches (flip after live verification, then restart HA) ---
WRITE_ECHO_EXPECTED = True         # False if the device sends no FC10 echo (runbook step W)
WRITE_MODE_SETPOINT_TOO = False    # True if the app also writes reg 19/20/21 on setpoint change (step S)
REG_DATA_LITTLE_ENDIAN = False     # [A] register data byte order; flip if the probe
# shows byte-swapped values (e.g. 0xF300 instead of 0x00F3 for 24.3 degC).

# --- pushed plane (spec 013, data-model 013 §6) ---------------------------
# The unit never pushes spontaneously: it ANSWERS every valid request
# (FC03 read, block query, control write — law L2, spec 020) with a burst
# of four CRC-valid 45-register blocks (1001/1046/2001/2046 — [P, research
# 013 §2]) while NEVER answering our byte-perfect base reads
# (att-accepted-silent, [P]). Block 2046-2090
# carries the T-family live temperatures at 2046-2051: the T01 sentinel
# anchor at 2046 (this unit's only absent sensor [P panel]) and the
# live-proven 2047 (14.0 -> 14.5 degC between two pushes [P]). The
# constants below are the single source of truth for the pushed-plane
# mapping — a permuted calibration outcome is a ONE-LINE edit in
# PUSHED_TEMPS (quickstart 013 §2).
PUSH_TEMP_BLOCK_START = 2046          # [P] the T-family live-temperature block

# member -> pushed register; the T-window hypothesis (research 013 §2.4).
PUSHED_TEMPS: dict[str, int] = {
    "suction_temp": 2046,       # T01 — sentinel on this unit -> unknown
    "inlet_water_temp": 2047,   # T02 — live-proven register [P]
    "outlet_water_temp": 2048,  # T03
    "coil_temp": 2049,         # T04
    "ambient_temp": 2050,      # T05
    "exhaust_temp": 2051,      # T06
}

# The pushed-plane address window (FR-7): the blocks the unit pushes
# live in the two dataset series 1001-1180 and 2001-2090 (research 013
# §3); the entity-bearing pushes observed are 2001-2090 only [P].
PUSH_PLANE_FIRST = 2001
PUSH_PLANE_LAST = 2090

# --- pushed control plane (spec 015, data-model 015 §7) -------------------
# The control homes the owner's toggle-and-diff calibration pinned in the
# 2001 block (start=0x07D1, research 015 §8.1) under the pinning rule of
# contract 015-A §2.1: the register followed the app's control change AND
# its revert (2026-10-12). A read-diff proves what a register REFLECTS,
# never what writing it CONTROLS (contract 015-A §8): these homes are
# READ-ONLY sources, and `ensure_writable` stays byte-identical (the
# allowlist {8, 13, 14} + 19-21 — contract 015-B §1/§3.4).
PUSH_CONTROL_BLOCK_START = 2001  # [P] the control block of the pushed plane

# The control plane window (spec 015 FR-11d): the two pushed series — the
# machine-settings ladder 1001-1090 included, because a future setpoint
# home may land in the 1046 ladder (research 015 §2.2).
PUSH_CONTROL_FIRST = 1001
PUSH_CONTROL_LAST = 2090

# quantity -> pushed register; None = no home identified (FR-5).
PUSHED_CONTROL: dict[str, int | None] = {
    # [P owner diff-revert 2026-10-12]: 0 (off, 07:47) -> 1 (on, 08:31)
    # -> 0 (off, 08:37) across the app's power slide + revert — the
    # aller-retour proof (quickstart 015 §1 step P). The 2012 flag
    # candidate is EXCLUDED for power: static 1 across the whole diff.
    # [P arithmetic on the owner's raw frames, review 015]: the frame is
    # BIG-endian. The block's own start sits at index 9 of the payload
    # (07 d1 -> 2001), which is an exact self-reference and proves the
    # byte order. The two toggles therefore land at 2011 (power) and
    # 2019 (mode) — not 2025/2034 (a miscount corrected in review).
    "power": 2011,
    # [P owner diff-revert 2026-10-12]: 0 -> 4 -> 0 across the same
    # toggle (quickstart 015 §1); the enum's other members are unknown.
    "mode": 2019,
    # Spec 019 [P owner diff 2026-10-12 — research 019 §1]: register
    # 1046 IS the heat setpoint — the owner's panel change (+1.0 °C)
    # moved the first register of the pushed 1046-block 290 -> 300
    # (29.0 -> 30.0 °C, x10 scaled). The decode reads the capture of
    # the block that CONTAINS the home (coordinator.from_pushes,
    # FR-1) — NOT the 2001 window.
    "setpoint": 1046,
}

# Spec 018 (contract 018-A §3): the OBSERVED-VALUES registry for the
# pushed mode mirror — documentation + the unmapped-diagnostic's
# membership check. It is NO LONGER the display gate: the climate's
# hvac_mode follows power alone on this heat-only unit (H06 [P
# panel]). Observed values [P owner, 2026-10-12]:
#   4 — the app-flow on-state (the 015 diff-revert, 08:31);
#   0 — the off world AND the on-state after OUR power-only writes
#       (our 1011 write never touches the mode plane, contract 016
#       §4 — research 018 §3, M-1): expected, never an error.
# Other values unknown (research 018 §8 G-3); an unmapped NONZERO
# value logs ONE INFO per session per value. The base
# MODE_VALUE_TO_NAME (reg 13, the classic plane) must never be
# polluted from this map (unchanged).
PUSHED_MODE_VALUE_TO_NAME: dict[int, str] = {
    4: "heat",
}

# FR-10: the diagnostics candidates (address -> evidence tag). The
# push_stream diagnostic exposes these raws gate-independently so the
# owner can re-run the diff without a terminal (quickstart 015 §1);
# the probe logs annotate the same addresses via KNOWN_LABELS. The tags
# carry the honesty — [P] pinned, [V] open, [A-weak] assumed candidate.
PUSH_CONTROL_CANDIDATES: dict[int, str] = {
    2011: "power home [P owner diff-revert 2026-10-12]",
    2019: "mode home [P owner diff-revert 2026-10-12]",
    2012: "flag candidate [V] — static 1 across the 2026-10-12 diff",
    # Spec 022 FR-8: the already-nonzero wildcards join the candidate
    # list — diagnostics only (the Push stream attributes + the probe
    # labels), for the owner's E03 fault hunt (quickstart 022 §3). No
    # entity, no decode, no label claim.
    2017: "unknown [V] — candidate, spec 022 fault hunt",
    2018: "unknown [V] — candidate, spec 022 fault hunt",
    # Spec 019 T1: the tag updated to the proven wording — 1046 is no
    # longer a candidate (the panel diff proved it, research 019 §1).
    1046: "setpoint home (heat) [P owner diff 2026-10-12]",
    1049: "setpoint candidate [A-weak] — Heating set?",
    1051: "setpoint candidate [A-weak] — Heating set / Auto. Set?",
    1055: "setpoint candidate [A-weak] — Max. cool?",
    1068: "setpoint candidate [A-weak] — Auto. set?",
}


def pushed_block_start(address: int) -> int | None:
    """The start of the OBSERVED pushed block containing `address`.

    The four blocks the unit pushes [P — research 013 §2, six censused
    runs]: 1001/1046/2001/2046, 45 registers each (BASE_BLOCK_COUNT).
    None when the address lies outside every observed block (e.g. the
    never-pushed 1091/1136). Feeds the FR-10 diagnostics raws — a
    candidate's block lookup, never an entity source.
    """
    for start in (1001, 1046, PUSH_CONTROL_BLOCK_START, PUSH_TEMP_BLOCK_START):
        if start <= address <= start + BASE_BLOCK_COUNT - 1:
            return start
    return None


def is_sentinel(raw: int | None) -> bool:
    """True when `raw` is a "sensor absent" sentinel (False for None)."""
    return raw is not None and raw in SENTINEL_RAWS


def is_plausible_temp(raw: int | None) -> bool:
    """True when `raw` lies inside the temperature plausibility window."""
    return raw is not None and TEMP_PLAUSIBLE_MIN_RAW <= raw <= TEMP_PLAUSIBLE_MAX_RAW


def decode_temp(raw: int | None) -> float | None:
    """Raw register -> degrees Celsius; None when absent, sentinel or implausible.

    Priority (decode-contract.md §2): None -> None; sentinel -> None (sensor
    absent — a normal state, never rendered as a temperature); implausible
    -> None (the consumer logs one warning per address per session);
    otherwise round(raw / TEMP_SCALE, 1). Pure: no logging here (NFR-1).
    """
    if raw is None or is_sentinel(raw):
        return None
    if not is_plausible_temp(raw):
        return None
    return round(raw / TEMP_SCALE, 1)


def decode_flag(raw: int | None, active_when_nonzero: bool) -> bool | None:
    """Raw register -> ON/OFF flag; None when the register is absent.

    Polarity per decode-contract.md §4: nonzero means ON when
    `active_when_nonzero` is True; inverted (zero means ON) when False. The
    S-family may use the opposite convention of the O-family [V] — hence the
    per-RegDef polarity (research.md §9).
    """
    if raw is None:
        return None
    return (raw != 0) if active_when_nonzero else (raw == 0)


# --- write control plane (spec 016, research 016 §2/§5) ----------------
# The vendor app's WRITE plane, recovered from the app's own
# disassembly (research 016 §0/§2): the home-screen power slide
# writes register 1011 with 1 (on) / 0 (off) — the ONLY hardcoded
# power write in the whole binary (closure _Iwd: mov x2, #0x3f3,
# mov x3, #1, bytecount "02"); the mode write targets 1012 plus the
# pair register 1013, and the mute write 1076. The write plane and
# the read plane are DIFFERENT registers (power: write 1011, read the
# pushed mirror 2011; mode: write 1012, read 2019 — the offset is NOT
# constant, research 016 §3). DATA ONLY (FR-1/NFR-3): unreachable by
# any write path — the guard below admits 1011 only within its value
# allowlist, and 1012/1013/1076 never (each needs its own proof,
# contract 016 §4).
WRITE_REG_POWER = 1011        # [P — disassembly, research 016 §2]
WRITE_REG_MODE = 1012         # [P shape / A semantics — research 016 §5]
WRITE_REG_MODE_PAIR = 1013    # [P shape / A semantics — research 016 §5]
WRITE_REG_MUTE = 1076         # [P frame / V semantics — research 016 §5]

# The pinned power frames [P — research 016 §2.1, CRC-computed through
# the app's own builder pipeline]:
#   63 10 03F3 0001 02 0001 F131  (ON, 11 bytes)
#   63 10 03F3 0001 02 0000 30F1  (OFF, 11 bytes)
# One source of truth (NFR-2): modbus.build_write_control(1011, v) is
# asserted byte-equal to these strings by the modbus self-test and the
# spec-016 suite — a mismatch fails both.
WRITE_FRAME_POWER_ON = "631003f30001020001f131"
WRITE_FRAME_POWER_OFF = "631003f3000102000030f1"

# The proof oracle: the pushed read home the owner's toggle-and-diff
# pinned as the power STATE mirror (spec 015). NEVER written — a
# read-diff proves what a register REFLECTS, never what writing it
# CONTROLS (contract 016 §2); the probe only READS it.
READ_MIRROR_POWER = PUSHED_CONTROL["power"]  # = 2011 [P]

# --- setpoint write plane (spec 019, research 019 §2/§4/§5/§7) ----------
# The vendor app's setpoint write: ONE register (qty 1, bytecount "02",
# value = temp × 10 [P — research 019 §2's disassembly of the
# home-screen setpoint handler 0x6a1f54/0x6a1fe8]) to an address
# resolved at RUNTIME from a per-family, per-mode map recovered from
# the disassembly [P — research 019 §4.1, the binder 0x5dc3c4]:
#   CS.field_0x11b = Map{1: [1135,1136,1137], 2: [1051,1046,1068],
#                        3: [1073,1074,1075]}[family][mode]
# (mode 0/1/2 = cool/heat/auto [A — the mode handler's field order]).
# The owner's unit adjudicates to family 2's heat slot = 1046
# [A-strong — research 019 §5] — the SAME register the panel diff
# proved is the live setpoint [P owner — research 019 §1]: the write
# target and the read home COINCIDE (a PARAMETER register — the "66"
# page's plane, read AND written by the vendor's own app; research
# 019 §4.2/§5). The alternative 1013 hypothesis is disproven by the
# exhaustive immediate scan (zero movz #1013 in the binary —
# research 019 §3).
WRITE_REG_SETPOINT = 1046  # [A-strong — research 019 §5; the map is data too]

# The proof oracle: the pushed read home (1046). The write target and
# the mirror are the SAME register — the fresh 1046-block capture is
# the write's confirmation, NATIVELY (contract 019 §1: the mirror must
# postdate the write it verifies).
READ_MIRROR_SETPOINT = PUSHED_CONTROL["setpoint"]  # = 1046 [P owner]

# The value discipline [D — research 019 §7]: the setpoint is a
# REGULATION value — only raw 100..400 (10.0-40.0 °C) in steps of 5
# (the 0.5 °C granularity; every observed raw is a multiple of 5
# [P decode]) may EVER be written. PERMANENT: extending the range or
# changing the step needs a new contract (contract 019 §2.2).
SETPOINT_MIN_RAW = 100   # 10.0 °C
SETPOINT_MAX_RAW = 400   # 40.0 °C
SETPOINT_STEP_RAW = 5    # 0.5 °C

# The vendor's per-family, per-mode write-address map — DATA ONLY
# [P — research 019 §4.1], documented for the record. The family key's
# exact semantics is [V] (research 019 §10 U-4 — the logcat oracle or a
# targeted disassembly pass; NOT a control blocker). NO consumer may
# ever read this map for a write target (contract 019 §7.1: no
# mode-aware addressing at runtime — 1046 is pinned for this heat-only
# unit; a multi-mode or other-family unit needs its own spec, its own
# proof and its own contract; the unproven slots (1135-1137, 1051,
# 1068, 1073-1075) are NEVER writable — installer thresholds,
# contract 019 §7.2).
WRITE_SETPOINT_FAMILY_MAP: dict[int, tuple[int, int, int]] = {
    1: (1135, 1136, 1137),  # cool / heat / auto [P — the map's list order]
    2: (1051, 1046, 1068),
    3: (1073, 1074, 1075),
}

# --- write security (spec 003 §6 / FR-9) ---
# The ONLY registers the integration may ever write: power, mode and the
# current setpoint — all proven user-operational writes inside the base
# block. Regs 19/20/21 join only behind the default-off
# WRITE_MODE_SETPOINT_TOO mirror switch. The installer families (H/R/P) and
# the high blocks 1001-2090 are never writable; there is deliberately NO
# generic write surface (research.md §7) — an erroneous H04/R-limit write can
# damage the heat pump or prevent it from starting.
ALLOWED_WRITE_BASE: frozenset[int] = frozenset(
    {REG_POWER, REG_MODE, REG_SET_CURRENT_TEMP}
)

# --- write security, control plane (spec 016 FR-3, contract 016 §4) ---
# The CONTROL allowlist: the address AND the value allowlist — power
# 1011 accepts ONLY 0/1, FOREVER (contract 016 §4's value discipline:
# extending the values requires a new contract). Spec 022 removed
# the human confirmation gate; this PERMANENT value rail is the
# protection that stays. The mode (1012-1013), mute (1076) and every
# other write-plane register is NOT extended here: each needs its own
# §2 evidence (its own pinned frame, mirror and §3 proof — the power
# proof does not transfer).
ALLOWED_WRITE_CONTROL: dict[int, frozenset[int]] = {
    WRITE_REG_POWER: frozenset({0, 1}),
}

# --- write security, setpoint plane (spec 019 FR-4, contract 019 §4) ---
# The setpoint value discipline: the address AND the value allowlist —
# 1046 accepts ONLY raw 100..400 in steps of 5 (10.0-40.0 °C, the 0.5
# °C granularity), FOREVER (contract 019 §2.2: a bounded RANGE instead
# of the power plane's {0, 1} — a regulation value needs it; a
# 30.3 °C request is REFUSED, never rounded; a 41.0 °C request is
# REFUSED, never clamped). Spec 022 removed the human confirmation
# gate; this PERMANENT value rail is the protection that stays. The
# ladder's other members (1049/1050/1051/1054/1055/1068…) are NEVER
# writable here — installer thresholds (contract 019 §7.2).
ALLOWED_WRITE_SETPOINT: dict[int, frozenset[int]] = {
    WRITE_REG_SETPOINT: frozenset(
        range(SETPOINT_MIN_RAW, SETPOINT_MAX_RAW + 1, SETPOINT_STEP_RAW)
    ),
}

# Spec 022 (FR-3, Option B — "active by default"): the human
# confirmation gates (the global proven switches and their per-entry
# option twins) are DELETED. Every surface they gated
# is owner-proven (the power write probe's EXECUTED + REVERSIBLE
# verdict, the setpoint probe's verdict, the mirror diff-reverts —
# specs 016/017/019), so the gates re-asked an answered question.
# What protects the machine is AUTOMATIC and stays byte-identical: the
# address allowlists above and the value rails ({0, 1} for 1011;
# 100..400 step 5 for 1046) are PERMANENT (contracts 016 §4 / 019
# §2.2) — extending either needs a new contract. The probes remain as
# informational instruments (spec 022 keeps them unchanged).


def ensure_writable(address: int, value: int | None = None) -> None:
    """Guard the single write funnel: raise ValueError unless allowed.

    coordinator.async_write_register calls this as its first statement,
    so no code path — present or future — can write outside the
    allowlists. The base-block rules (spec 003 §6) are UNTOUCHED:
    {8, 13, 14} + 19-21 behind WRITE_MODE_SETPOINT_TOO, and value=None
    keeps the old semantics for those callers.

    Spec 022 (FR-3/FR-4): the integration is ACTIVE BY DEFAULT — the
    owner-confirmation gates are gone. What guards the write plane is
    PERMANENT (contracts 003 §6 / 016 §4 / 019 §2.2): the ADDRESS
    allowlists (1011 and 1046 ONLY — the mode pair 1012/1013, mute
    1076, the installer thresholds and the whole read plane are
    refused forever) and the VALUE rails — a register in
    ALLOWED_WRITE_CONTROL (today: power 1011) is writable ONLY with a
    value in its allowlist ({0, 1}; a control write must always name
    its value); a register in ALLOWED_WRITE_SETPOINT (today: 1046)
    ONLY with a value in raw 100..400 in steps of 5 (10.0-40.0 °C in
    0.5 steps — a regulation value is never rounded, never clamped).
    Every other register is refused with the spec-003 message.
    """
    allowed = ALLOWED_WRITE_BASE
    if WRITE_MODE_SETPOINT_TOO:
        allowed = allowed | {
            REG_SET_COOL_TARGET,
            REG_SET_HEAT_TARGET,
            REG_SET_AUTO_TARGET,
        }
    if address in allowed:
        return
    if address in ALLOWED_WRITE_CONTROL:
        # The permanent value rail: {0, 1} only, forever.
        values = ALLOWED_WRITE_CONTROL[address]
        if value is None or value not in values:
            raise ValueError(
                f"register {address} accepts only values {sorted(values)} "
                "(the value allowlist is part of the guard)"
            )
        return
    if address in ALLOWED_WRITE_SETPOINT:
        # The permanent value rail: the range AND the step — never
        # rounded, never clamped (contract 019 §2.2).
        values = ALLOWED_WRITE_SETPOINT[address]
        if value is None or value not in values:
            raise ValueError(
                f"register {address} accepts only values "
                f"{SETPOINT_MIN_RAW}..{SETPOINT_MAX_RAW} in steps of "
                f"{SETPOINT_STEP_RAW} (10.0-40.0 °C in 0.5 steps) — the "
                "setpoint value allowlist is permanent (contract 019 §2.2)"
            )
        return
    raise ValueError(f"register {address} is not writable (spec 003 §6)")


@dataclass(frozen=True)
class RegDef:
    """One entity definition, driven by the registries below."""

    key: str                     # entity key + unique_id suffix
    address: int | None          # None = unverified -> entity not added
    label: str                   # probe-log annotation
    scale: float = 1.0
    signed: bool = False
    device_class: str | None = None
    unit: str | None = None
    state_class: str | None = None
    icon: str | None = None
    diagnostic: bool = False
    # --- spec 003 field (appended; existing positional uses keep working) ---
    family: str | None = None                # "T01", "S03", ... doc/probe tag


SENSORS: tuple[RegDef, ...] = (
    RegDef("inlet_water_temp", REG_INLET_WATER_TEMP, "InletWaterTemp", TEMP_SCALE, True,
           "temperature", "°C", "measurement", family="T02"),
    RegDef("outlet_water_temp", REG_OUTLET_WATER_TEMP, "OutletWaterTemp", TEMP_SCALE, True,
           "temperature", "°C", "measurement", family="T03"),
    RegDef("ambient_temp", REG_AMBIENT_TEMP, "AT", TEMP_SCALE, True,
           "temperature", "°C", "measurement", family="T05"),
    # spec 003 T-family placeholders — [V] indices; entities appear only
    # after calibration pins the registers (calibration-contract.md §3).
    RegDef("suction_temp", REG_SUCTION_TEMP, "SuctionTemp", TEMP_SCALE, True,
           "temperature", "°C", "measurement", family="T01"),
    RegDef("coil_temp", REG_COIL_TEMP, "Coil", TEMP_SCALE, True,
           "temperature", "°C", "measurement", family="T04"),
    RegDef("exhaust_temp", REG_EXHAUST_TEMP, "Exhaust", TEMP_SCALE, True,
           "temperature", "°C", "measurement", family="T06"),
    # entity-contract.md §2 lists this as `diagnostic`; shipped behaviour is
    # kept as-is (non-regression, "existing, unchanged"). No category change.
    RegDef("fault_code", REG_FAULT1, "Fault1", icon="mdi:alert-circle"),
    RegDef("firmware", REG_FIRMWARE_FIRST, "Firmware", icon="mdi:information",
           diagnostic=True),  # special-case: joins regs 48..51
)

# The binary-sensor registry (O/S switches) was pruned in the spec-021
# cleanup: their base registers were never pinned and no pushed home
# exists, so no binary entity can ever be created (spec 020 §5).

# --- pushed-plane registries (spec 013 FR-5, data-model 013 §6) ----------
# PUSHED_MIRRORS: the base-pinned keys with their pushed registers — the
# same physical quantity on two planes (contract 013 §4.1: one entity
# key per quantity, never two entities). In push mode these entities
# exist as today and take their values from the mirrors (spec 022:
# active by default — the calibration gate is gone; the sentinel and
# plausibility rules still guard every decode).
PUSHED_MIRRORS: dict[str, int] = {
    "inlet_water_temp": PUSHED_TEMPS["inlet_water_temp"],
    "outlet_water_temp": PUSHED_TEMPS["outlet_water_temp"],
    "ambient_temp": PUSHED_TEMPS["ambient_temp"],
}

# PUSHED_SENSORS: the push-only keys (no base home [V base]) — created
# unconditionally in push mode (spec 022: active by default). Addresses
# come from PUSHED_TEMPS — the single source of truth (a permuted
# calibration outcome is a one-line edit there, data-model 013 §6).
PUSHED_SENSORS: tuple[RegDef, ...] = tuple(
    RegDef(
        d.key,
        PUSHED_TEMPS[d.key],
        d.label,
        d.scale,
        d.signed,
        d.device_class,
        d.unit,
        d.state_class,
        family=d.family,
    )
    for d in SENSORS
    if d.key in PUSHED_TEMPS and d.key not in PUSHED_MIRRORS
)

# Reverse map used by the probe logs ("reg N = ... # Label"). The [P]/[V] tag
# records the confidence level of each label; the family code documents the
# panel-observed group (spec 003 data-model.md).
KNOWN_LABELS: dict[int, str] = {
    REG_POWER: "Power [P]",
    REG_BOOST: "EnBoostMode [P]",
    REG_MODE: "Mode [P]",
    REG_SET_CURRENT_TEMP: "SetCurrentTemp [P]",
    REG_SET_COOL_TARGET: "SetCoolTargetTemp [P]",
    REG_SET_HEAT_TARGET: "SetHeatTargetTemp [P]",
    REG_SET_AUTO_TARGET: "SetAutoTargetTemp [P]",
    REG_COOL_MIN_TARGET: "SetCoolMinTargetTemp [P]",
    REG_COOL_MAX_TARGET: "SetCoolMaxTargetTemp [P]",
    REG_HEAT_MIN_TARGET: "SetHeatMinTargetTemp [P]",
    REG_HEAT_MAX_TARGET: "SetHeatMaxTargetTemp [P]",
    REG_INLET_WATER_TEMP: "InletWaterTemp [P] (T02)",
    REG_OUTLET_WATER_TEMP: "OutletWaterTemp [P] (T03)",
    REG_AMBIENT_TEMP: "AT [P] (T05)",
    REG_FAULT1: "Fault1 [P]",
    REG_FIRMWARE_FIRST: "Firmware [V]",  # marks the 48..51 group
}

# Placeholder labels ([V]) are only meaningful once their register is pinned
# down; the family tag documents the panel-observed group (entity-contract.md
# §2/§3). Temperature placeholders only — the binary placeholders were
# pruned with their registry (spec 021).
for _reg, _label in (
    (REG_SUCTION_TEMP, "SuctionTemp [V] (T01)"),
    (REG_COIL_TEMP, "Coil [V] (T04)"),
    (REG_EXHAUST_TEMP, "Exhaust [V] (T06)"),
):
    if _reg is not None:
        KNOWN_LABELS[_reg] = _label
del _reg, _label

# Spec 015 (FR-10/AC-5): the 2001-block control homes + neighbours and the
# 1046-ladder setpoint candidates, annotated for the probe logs (the
# block=2001 / block=1046 output) — the tags carry the evidence state,
# so the owner can re-run the diff from the probe alone.
KNOWN_LABELS.update({
    2010: "self-reference 0x07D1 [V]",
    2012: "flag [V] — static across the 2026-10-12 diff",
    2017: "unknown (13) [V]",
    2018: "unknown (168) [V]",
    2011: "Power (pushed control) [P owner diff 2026-10-12]",
    2019: "Mode (pushed control) [P owner diff 2026-10-12]",
    # Spec 019 (research 019 §1.1 — the corrected ladder labels): the
    # panel's page order is NOT the register order. 1046 is the PROVEN
    # heat home; 1051/1068 are the per-mode candidates adjudicated from
    # the family map [A-strong]; 1049 is a ladder member (min-bound or
    # diff family, [V]); 1050/1054 are the max/min BOUND candidates,
    # kept [A-weak] — never promoted until a bounds-diff at the panel
    # pins them (research 019 §10 U-5).
    1046: "setpoint home (heat) [P owner diff 2026-10-12]",
    1049: "setpoint-block ladder member (min-bound/diff family) [V 019]",
    1050: "max-bound candidate (40.0) [A-weak 019]",
    1051: "cool setpoint candidate [A-strong 019]",
    1054: "min-bound candidate (10.0) [A-weak 019]",
    1055: "setpoint candidate [A-weak] — Max. cool?",
    1068: "auto setpoint candidate [A-strong 019]",
})

# Spec 016 (FR-1): the WRITE-plane tags — the probe-log annotation
# convention for the command registers (research 016 §5's map). The
# tags carry the honesty: the power frame is [P] (disassembly), the
# mode/mute semantics are [A]/[V] and stay unwritable regardless
# (each needs its own proof, contract 016 §4).
KNOWN_LABELS.update({
    1011: "Power (WRITE plane) [P disassembly 016]",
    1012: "Mode (WRITE plane) [P shape / A semantics 016]",
    1013: "Mode pair (WRITE plane) [P shape / A semantics 016]",
    1076: "Mute (WRITE plane) [P frame / V semantics 016]",
})

# Documentation-only mirror of the panel observation (2026-10,
# specs/003-sensor-coverage/data-model.md). NO consumer — do not build logic
# on it (plan.md §5); it exists so the seven families and their HA exposure
# policy are readable next to the registries. H/R/P/D have NO entity of any
# platform and are never writable (spec 003 §6).
FAMILIES: dict[str, dict[str, str]] = {
    "T": {"meaning": "live temperatures (T01-T06)", "exposure": "temperature sensors"},
    "H": {"meaning": "machine settings (H01-H06, H70-H72)",
          "exposure": "NONE — never writable from HA (spec 003 §6)"},
    "R": {"meaning": "regulation thresholds, installer (R01-R11)",
          "exposure": "NONE — never writable from HA (spec 003 §6)"},
    "P": {"meaning": "filtration program (P01-P04)",
          "exposure": "NONE — never writable from HA (spec 003 §6)"},
    "S": {"meaning": "safety inputs / switches (S01-S06)",
          "exposure": "NONE — never pinned; no entity (binary surface pruned, spec 021)"},
    "O": {"meaning": "outputs (O01-O04)",
          "exposure": "NONE — never pinned; no entity (binary surface pruned, spec 021)"},
    "D": {"meaning": "digital states (D01-D06)",
          "exposure": "NONE — known but unmapped, no speculation"},
}


def in_base_block(address: int) -> bool:
    """True when `address` lies inside the polled base block (FR-7 gating)."""
    return BASE_BLOCK_START <= address <= BASE_BLOCK_START + BASE_BLOCK_COUNT - 1


def check_registry_integrity(
    sensors: tuple[RegDef, ...] = SENSORS,
    pushed_temps: dict[str, int] | None = None,
    pushed_control: dict[str, int | None] | None = None,
) -> None:
    """Validate the entity registries (FR-11 / entity-contract.md §6).

    A mis-calibration must fail loudly at import instead of silently
    shadowing one label with another's register. Raises ValueError on:
    - duplicate entity keys within the registry;
    - duplicate non-None addresses within the registry;
    - a pinned address outside the polled base block 7..51 (high-block
      polling is not implemented — spec 003 §8; leave such a register
      unpinned until phase B);
    - a pushed-plane violation (spec 013 FR-7, contract 013 §7.1): a
      pushed key that is not a shipped TEMPERATURE entity key (H/R/P
      are never entities — spec 003 §6), a pushed address outside
      2001-2090, or a duplicate address within the pushed plane. The
      pushed plane maps shipped sensor keys only — validated against
      the SHIPPED SENSORS, so a custom base-plane tuple passed for the
      spec-003 rules never breaks the pushed validation. Cross-plane
      collisions are impossible by the two window checks; mirror
      pairs (base+push for one key) are the documented legal case
      (data-model 013 §6).
    - a pushed-CONTROL violation (spec 015 FR-11d, contract 015-A §6):
      a key outside {power, mode, setpoint}, a non-None address outside
      the control plane 1001-2090 (the machine-settings ladder included
      — a setpoint home may land in the 1046 ladder), a duplicate
      address within the control plane, or a collision with a pushed
      temperature register (cross-plane duplicates). None homes are
      legal — an unidentified quantity must stay unrepresented.
    The shipped registries must pass — asserted in tests/test_registers.py,
    tests/test_registers_push_013.py and tests/test_control_015.py.
    """
    seen_keys: set[str] = set()
    seen_addresses: dict[int, str] = {}
    for regdef in sensors:
        if regdef.key in seen_keys:
            raise ValueError(
                f"duplicate entity key {regdef.key!r} in the registries (FR-11)"
            )
        seen_keys.add(regdef.key)
        if regdef.address is None:
            continue
        if not in_base_block(regdef.address):
            raise ValueError(
                f"SENSORS entry {regdef.key!r} pins register "
                f"{regdef.address}, outside the polled base block "
                f"{BASE_BLOCK_START}..{BASE_BLOCK_START + BASE_BLOCK_COUNT - 1}; "
                "high-block polling is not implemented (spec 003 §8) — "
                "leave the register unpinned (docs/CALIBRATION.md §8.4)"
            )
        if regdef.address in seen_addresses:
            raise ValueError(
                f"register {regdef.address} pinned for both "
                f"{seen_addresses[regdef.address]!r} and {regdef.key!r} (FR-11)"
            )
        seen_addresses[regdef.address] = regdef.key

    # --- pushed plane (spec 013 FR-7, contract 013 §7.1) -------------------
    pushed = dict(pushed_temps) if pushed_temps is not None else dict(PUSHED_TEMPS)
    seen_pushed: dict[int, str] = {}
    for key, address in pushed.items():
        shipped = next((d for d in SENSORS if d.key == key), None)
        if shipped is None or shipped.device_class != "temperature":
            raise ValueError(
                f"pushed key {key!r} is not a shipped temperature entity — "
                "the pushed plane maps shipped sensor keys only (spec 013 "
                "FR-7; H/R/P are never entities, spec 003 §6)"
            )
        if not (PUSH_PLANE_FIRST <= address <= PUSH_PLANE_LAST):
            raise ValueError(
                f"pushed register {address} for {key!r} is outside the pushed "
                f"plane {PUSH_PLANE_FIRST}-{PUSH_PLANE_LAST} (spec 013 FR-7)"
            )
        if address in seen_pushed:
            raise ValueError(
                f"pushed register {address} pinned for both "
                f"{seen_pushed[address]!r} and {key!r} (spec 013 FR-7)"
            )
        seen_pushed[address] = key

    # --- pushed control plane (spec 015 FR-11d, contract 015-A §6) --------
    control = (
        dict(pushed_control) if pushed_control is not None else dict(PUSHED_CONTROL)
    )
    unknown_keys = sorted(set(control) - {"power", "mode", "setpoint"})
    if unknown_keys:
        raise ValueError(
            f"unknown pushed-control key(s) {unknown_keys} — the control "
            "keys are power/mode/setpoint only (spec 015 FR-11d)"
        )
    seen_control: dict[int, str] = {}
    for key, address in control.items():
        if address is None:
            continue  # no home identified — legal (the quantity stays unrepresented)
        if not (PUSH_CONTROL_FIRST <= address <= PUSH_CONTROL_LAST):
            raise ValueError(
                f"pushed-control register {address} for {key!r} is outside "
                f"the control plane {PUSH_CONTROL_FIRST}-{PUSH_CONTROL_LAST} "
                "(spec 015 FR-11d)"
            )
        if address in seen_pushed:
            raise ValueError(
                f"pushed-control register {address} for {key!r} collides "
                f"with the pushed temperature {seen_pushed[address]!r} — no "
                "cross-plane duplicates (spec 015 FR-11d)"
            )
        if address in seen_control:
            raise ValueError(
                f"pushed-control register {address} pinned for both "
                f"{seen_control[address]!r} and {key!r} (spec 015 FR-11d)"
            )
        seen_control[address] = key


check_registry_integrity()
# Tasks 006 — Ordered implementation blocks for the Coder

Work top-down; each block ends with its gates. Repo rule: durable artifacts
(code comments, commit messages) in **English**. Do not touch anything
outside NFR-1's file list (`const.py`, `aqua_client.py`, `config_flow.py`,
`tests/`). The normative design is `plan.md` +
`contracts/ble-session-sequence.md`; proofs are in `research.md` (§ refs
below). **No protocol changes beyond FR-1/FR-2/FR-3/FR-4 — there is no
pairing frame to send (research §8) and none may be invented (NFR-2).**

---

## Block A — Client parity: settle delay + write mode (P0)

**A1. `const.py`: `SETTLE_AFTER_SUBSCRIBE = 2.0`** *(FR-1)* — new constant
after `SILENCE_TIMEOUT`, comment citing research 006 §2 step 4 / §7
(vendor 2 s Duration in the subscribe→read path).

**A2. `aqua_client.py`: settle after subscribe** *(FR-1)* — in `connect()`,
after the notify/ff01 setup try-block, before the final `_LOGGER.info`:
`await asyncio.sleep(const.SETTLE_AFTER_SUBSCRIBE)`. Extend the nearby
budget comment (plan §2.3).

**A3. `aqua_client.py`: write-without-response preference** *(FR-2)* —
rewrite `_resolve_write_characteristic()`'s preference logic per plan §2.2
(without-response first, with-response fallback, hard error if neither);
DEBUG log prints props + decision.

**A4. Tests** —
- New `tests/test_connect_parity.py` (AC-1): patched `asyncio.sleep`,
  assert: exactly one sleep of `const.SETTLE_AFTER_SUBSCRIBE`, ordered
  after `start_notify`, before the first read (AC-1); property matrix →
  write mode (AC-2): `["write-without-response","write"]` → False,
  `["write"]` → True, `["notify"]` → raises `AquaTempConnectionError`.
- `tests/test_reassembly.py`: zero out the settle in fixtures
  (monkeypatch `const.SETTLE_AFTER_SUBSCRIBE = 0`) so existing fakes and
  wall-clock are unaffected.

**Gate A:** `pytest tests/ -q` green; `python3 -m py_compile` on
`const.py` + `aqua_client.py`; no other file touched.

---

## Block B — Connection-test retry budget (P0)

**B1. `config_flow.py`: `TEST_TIMEOUT = 70.0`** *(FR-3)* — replace value
and comment with the plan §3.1 arithmetic (2 × 20 s brc connects + backoff
+ 2 s settle + 8 s read + margin; vendor retries connects — research 006
§2.2). Supersedes spec 005 FR-5's `40.0` (routing key unchanged).

**B2. `config_flow.py`: `max_attempts=2` in `_async_test`** *(FR-3)* —
update the inline comment (the old "a second 20 s connect attempt never
could [fit]" no longer holds inside 70 s). Update the `TimeoutError`
branch's WARNING sentence to the two-attempt reality (plan §3.2); keep the
UI key `cannot_connect` and all spec-005 logging branches intact.

**B3. Progress step copy** *(FR-3)* — add "(this can take about a minute)"
to the test step's description where it is defined; touch the existing
description/translation entries only (no new keys, no rewording of the
error texts — spec 006 FR-5 keeps them).

**B4. Tests** — `tests/test_config_flow_walkthrough.py`: update any
literal `40.0`; add assertion that `_async_test` passes
`max_attempts=2` (fake client records kwargs). All existing walkthrough
cases stay green (AC-3).

**Gate B:** `pytest tests/ -q` green; `python3 -m py_compile` on
`config_flow.py`; `git diff` shows only the three touched .py files (+
tests).

---

## Block C — Discovery filter parity (P1)

**C1. `const.py`: `NAME_PREFIXES` += `"wt-"`** *(FR-4)* — comment cites
research 006 §3 (vendor operating filter `BlueNRG-` + `WT-`).

**C2. Tests** — extend the listing/resolution tier test with a `WT-AB12`
entry, assert it is listed (AC-4); keep `bluenrg-`/`hiflying-`/`prov_`
cases green.

**Gate C:** `pytest tests/ -q` green; `python3 -m py_compile` clean.

---

## Block D — Verification (P0, no code)

**D1.** Full suite + compile (AC-5) and `git diff --stat` confined to
NFR-1's list.

**D2.** Read-back against the contract: the shipped connect sequence must
match `contracts/ble-session-sequence.md` §1-§3 (connect ≤20 s×2 →
discover → subscribe ff02 → settle 2 s → FC03 base read → reassembly; no
pre-connection frame — none exists, research §8).

**D3.** Confirm `quickstart.md` exists in this kit (AC-6) — it ships with
the spec; the Coder does not author it.

**Out of scope (do NOT implement):** any time-sync sending (register
1267-1270, research §5 — layout still [V], NFR-2); any pairing/bonding
call; any MTU or connection-priority request; any change to the Modbus
layer, registers, entities, or translations beyond B3's one sentence.

---

## Completion status — Coder run 2026-10-11 (session agent:coder:subagent:a9d23b55)

| Task | Status | Notes |
|---|---|---|
| A1 `const.py` SETTLE_AFTER_SUBSCRIBE = 2.0 | ✅ done | after SILENCE_TIMEOUT; comment cites research 006 §2 step 4 / §7 |
| A2 `aqua_client.py` settle after subscribe | ✅ done | `await asyncio.sleep(const.SETTLE_AFTER_SUBSCRIBE)` inside the teardown try, after `_resolve_write_characteristic`, before the final `_LOGGER.info`; budget comments extended (plan §2.3) |
| A3 write-without-response preference | ✅ done | property-gated: without-response → `False`, `write` → `True`, neither → `AquaTempConnectionError`; DEBUG log prints props + decision |
| A4 tests | ✅ done | new `tests/test_connect_parity.py` (4 tests, red-first); `test_reassembly.py` gained connect-capable fakes + settle-zeroed regression test |
| B1 `TEST_TIMEOUT = 70.0` | ✅ done | comment carries the 2x20 s + backoff + 2 s settle + 8 s read arithmetic; cannot_connect routing key unchanged |
| B2 `max_attempts=2` in `_async_test` | ✅ done | inline comment updated; TimeoutError WARNING names the two-attempt reality; all spec-005 log branches intact |
| B3 progress step copy | ⏭️ skipped — blocked | the test step description lives in `strings.json` + `translations/{en,fr}.json` (`config.step.test.description`), all outside this run's authorized file list (NFR-1 + mission scope). Follow-up for the orchestrator: append "This can take about a minute." to that one description in the 3 files (no new keys) |
| B4 walkthrough tests | ✅ done | FakeClient records connect kwargs; new `test_connection_test_budget_and_attempts` asserts TEST_TIMEOUT==70.0 and `max_attempts=2` (red-first); all 15 prior cases green |
| C1 `NAME_PREFIXES` += `"wt-"` | ✅ done | comment cites research 006 §3 (operating filter BlueNRG- + WT-) |
| C2 discovery tier tests | ✅ done | `WT-AB12` listed by the list builder (test_config_flow_list) and resolved via tier 3 (test_resolve_device), red-first; bluenrg-/hiflying-/prov_ cases still green |
| D1 full suite + compile + diff scope | ✅ done | 73/73 pytest; all python3 runners green; py_compile clean; `git diff --stat` confined to const/aqua_client/config_flow + tests |
| D2 contract read-back | ✅ done | shipped sequence: connect (≤20 s×2) → discover → subscribe ff02 → settle 2 s → FC03 base read → reassembly; no pre-connection frame (research §8) |
| D3 quickstart.md present | ✅ done | shipped in this kit; six-step procedure with [P]/[V] statuses verified |

Extra (solo decisions, reported to the orchestrator):
- `tests/test_config_flow_walkthrough.py` gained a guarded module-level
  `pytestmark = pytest.mark.asyncio`: plain pytest (strict mode, no pytest
  config in the repo) refused all bare `async def` cases — a pre-existing
  condition (15 failures on the db1b387 baseline). With the mark,
  `pytest tests/ -q` is 73/73 green; the standalone `python3` runners are
  unaffected. A `tests/conftest.py` shim was tried first and removed
  (collection hooks run after pytest-asyncio evaluates markers).
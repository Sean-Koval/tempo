# Rationale — 2026 Bike Build (12wk FTP Target)

## 1. Goal framing

Sean's goal is a **performance-target bike build**: FTP 250 W → 280 W (+12%) in 12 weeks (start Mon 2026-05-04, FTP test Fri 2026-07-24). The training cliff this clears is the active **left-tibia BSI grade 2** (diagnosed 2026-04-25), which makes running unavailable until ~2026-06-06 and forces a cycling-led plan regardless. This block is therefore both:

- The targeted FTP build, and
- The **bike base for the 2027 half-IM A-race** declared in `race-calendar.yaml`.

Two birds, one plan. When BSI clearance lands (~W23/2026-06-06), walk-jog return-to-run folds in without disturbing the bike priority; the swim re-enters mid-June for technique work. Strength program (`athlete/strength-program.md`) runs throughout at 4 touches/week.

## 2. Framework chosen

Composer picked `ftp_target_16wk` from `phases.yaml` (the metric-specific template for `metric: ftp_w`): **base_aerobic_bike → ftp_progression_block → vo2_polarisation_block → deload_test**. This matches the literature consensus on FTP development: aerobic capacity first (Friel "abilities" sequence), then sweet-spot/threshold accumulation to "raise the floor", then a brief polarised VO2 block to lift the ceiling, then a freshen + test. Sources from `search_knowledge`:

- `friel-periodization.md` — 3:1 build:recovery cadence (R-15), abilities developed in sequence (aerobic endurance → force → muscular endurance → anaerobic). Credibility: expert_practitioner.
- `ironman-build-structure.md` — "aerobic capacity first, intensity later" + "≤2 field tests per macrocycle". Credibility: expert_practitioner.

## 3. Adaptations — **template compression flagged**

The standard template is **16 weeks** (4+6+4+2). Sean's runway is **12 weeks** — a 25% compression, **above the skill's 20% silent-floor**. Mitigations applied:

- **Base shortened by 0** (4 → 4 wk) — preserved fully; BSI-recovery athletes need the aerobic foundation more, not less.
- **FTP progression shortened −33%** (6 → 4 wk) — risk: less sweet-spot accumulation may cap how much of the +30 W is truly threshold-floor improvement vs. test-day sharpness.
- **VO2 polarisation shortened −25%** (4 → 3 wk) — risk: VO2 adaptation typically wants ≥4 wk of stimulus; 3 wk is the lower bound.
- **Deload + test shortened −50%** (2 → 1 wk) — acceptable; the goal is freshness + measurement, both achievable in 1 wk if the prior 11 wk are clean.

**Recommendation:** if Sean wants to land the +12% with margin, **extending the target date to 2026-08-21** (16 wk) restores the template at full length. The current 12-wk plan is achievable but treats every recovery week as load-bearing — one missed week can compound. This is the single most important judgement call in the plan.

Other notable adaptations:

- **Composer `sport_focus` honored** (bike 0.80–0.85, strength 0.15–0.20). Run + swim land in deload week for return-to-multisport flavor only — no real run/swim TSS until the BSI-recheck milestone (W23).
- **Long-ride scaling** matches Sean's S5 shape from `preferences.md` (90 min → 4 hr). The plan caps it at 3.5 hr in the build phase to leave room for race-pace inserts; full 4 hr arrives in late W28/W29 for IM-relevant durability.
- **Cycling sessions** map to Sean's S1-S6 names where possible. S2 (`vo2_intervals_bike`) and S4 (`cadence_neuromuscular_bike`) were added to the bike session library this week specifically to support this plan.

## 4. Assumptions

- **CTL trajectory:** start 28, target peak 52 by W29 (+2/wk, conservative). If CTL ramp falls > 5/wk for two consecutive weeks, R-15 will fire and plan-training-week will cut.
- **Weekly hour budget:** 12 hr cap from `preferences.md`. Plan averages ~9 hrs in base, ~10–11 in ftp_progression, ~10 in vo2_polarisation, ~6 in deload. Strength is ~2.5 hrs of that.
- **BSI-driven workflow:** zero run TSS for W19-W22. Walk-jog can start W23 *only if* the 2026-06-06 medical recheck clears it. If recheck is negative or delayed, the plan is unaffected (it never assumed run).
- **Strength gating:** Day-A single-leg calf raise on the left side stays BW until BSI is cleared. Other lifts progress per `strength-program.md`.
- **TSS targets per phase are upper envelopes** — actual per-week numbers get resolved by `plan-training-week`, which reads adherence + wellness + load.
- **Field tests:** exactly 2 — a baseline 20-min FTP check at the end of W19 (informal, just to confirm the 250 W starting point is current), and the goal-verification FTP test on 2026-07-24. No mid-block testing — it would siphon adaptation.

## 5. Open questions

1. **Should the target date move to 2026-08-21 (16 wk)?** Strongly recommended — restores the template at full length. The +12% goal is achievable in 12 wk, but with no margin for missed weeks.
2. **Is the FTP starting value (250 W) current?** Provenance says `intervals_import` from 2026-05-01, but it's not clear when the underlying FTP test happened. A baseline check at end of W19 will confirm.
3. **Is the BSI medical recheck on 2026-06-06 already booked?** Plan assumes it is — milestone W23 references it. If it slips, run re-entry slips with it.
4. **Strength-program TSS allocation?** The plan budgets ~2.5 hr/wk of strength, but `strength-program.md` doesn't yet quantify TSS-equivalent. The composer puts strength in `sport_focus` but TSS-equivalent for lifts is hand-waved. R-20 (sub-program capacity rule, from `goal-research`) will apply once strength becomes a real plan-fragment.
5. **Heat acclimation?** July in Sean's location may push outdoor sessions into heat. No heat-specific block is in this plan; if relevant, add a 7-10 day heat-acc block in mid W27-W28.

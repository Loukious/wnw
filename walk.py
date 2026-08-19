#!/usr/bin/env python3
"""
Walk&Win walk simulator.

Replays the exact API flow discovered from reverse-engineering
the app's Hermes v96 bytecode (see ../decompiled.js).

Flow
----
0. POST /user/login                              — authenticate & fetch fresh JWT token
1. POST /api/location/save   {latitude, longitude}   — every 3 steps
2. GET  /api/activity/get                              — every 10 steps
3. POST /api/location/change-test {nbValid, …}        — every 10 steps
4. WeCards & Quizzes:
   - POST /api/wecards/nearby                         — find collectibles
   - GET  /api/wecards/{id}/quiz                      — fetch quiz & find correct choice
   - POST /api/quiz/{id}/answer                       — submit correct answer key (+Points)
   - POST /api/wecards/{id}/claim                     — claim card (+Points)

Anti-cheat constraints respected
---------------------------------
• Cadence   : 70 – 130  steps/min   (we target 80-120, realistic walking)
• Speed     : 2.5 – 10  km/h        (we target 3.5-5.5)
• Burst     : max 3 steps in 800 ms  (we wait ≥ 500 ms between steps)
• Batch size: steps rounded to 10    (Math.floor(n/10)*10)
• Stride    : ≈ 0.72 m/step         (server expects ≈ 0.7055)
"""

import os
import sys
import math
import time
import random
import argparse
from datetime import datetime
from pathlib import Path

# Fix Windows console encoding
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv

from api import WalkApi
from gps import PathWalker


# ── Constants ──────────────────────────────────────────────────────────
LOCATION_SEND_INTERVAL = 3     # send GPS every N accepted steps
FLUSH_INTERVAL = 10            # flush to server every N accepted steps
MIN_CADENCE_SPM = 70           # app's lower bound
MAX_CADENCE_SPM = 130          # app's upper bound
MIN_STEP_GAP_MS = 800 / 3     # burst detector: 3 steps in 800ms → min ~267ms each
STRIDE_M = 0.72               # metres per step (observed: server uses ~0.7055)


def _log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def _format_distance(m: float) -> str:
    return f"{m / 1000:.2f} km" if m >= 1000 else f"{m:.0f} m"


def process_wecards_and_quizzes(
    api: WalkApi,
    lat: float,
    lng: float,
    claimed_cards: set[int],
    fast: bool = False,
    max_distance_m: float = 250.0,
) -> tuple[int, int, tuple[float, float] | None]:
    """
    Checks for nearby WeCards within interactive radius (<= max_distance_m),
    solves any associated quiz with human-like response delay, and claims the card.

    Returns (quiz_points_earned, card_points_earned, closest_unclaimed_target).
    """
    quiz_pts_total = 0
    card_pts_total = 0
    closest_unclaimed_dist = float("inf")
    closest_unclaimed_target = None
    try:
        res = api.get_nearby_wecards(lat, lng)
        cards = res.get("success", [])
        for card in cards:
            card_id = card.get("id")
            if not card_id or card_id in claimed_cards:
                continue

            dist = float(card.get("distance", 9999))
            if dist > max_distance_m:
                # Card is outside interactive radius for current GPS position
                if dist < closest_unclaimed_dist:
                    closest_unclaimed_dist = dist
                    card_lat = float(card.get("latitude", 0))
                    card_lng = float(card.get("longitude", 0))
                    if card_lat != 0 and card_lng != 0:
                        closest_unclaimed_target = (card_lat, card_lng)
                continue

            is_claimed = card.get("claimed", False)
            card_pts = card.get("points", 0)

            # 1. Process attached quiz if present & unanswered
            quiz_info = card.get("quiz")
            if quiz_info and not quiz_info.get("answered", True):
                quiz_id = quiz_info.get("id")
                try:
                    q_res = api.get_wecard_quiz(card_id)
                    questions = q_res.get("success", [])
                    if questions:
                        q_data = questions[0]
                        choices = q_data.get("choices", [])
                        correct_choice = next(
                            (c for c in choices if c.get("is_correct")), None
                        )
                        if correct_choice:
                            answer_key = correct_choice.get("key")
                            q_label = q_data.get("question", "Quiz")

                            # Simulate human thinking/reading time (4–9 seconds)
                            resp_time = random.randint(4, 9)
                            if not fast:
                                time.sleep(resp_time)

                            ans_res = api.send_quiz_answer(
                                quiz_id, answer_key, response_time=resp_time
                            )
                            if ans_res.get("correct"):
                                pts = ans_res.get("pointsEarned", 30)
                                quiz_pts_total += pts
                                _log(
                                    f"    🎯 Quiz solved! [{q_label[:40]}…] → +{pts} Quiz Points ({resp_time}s think time)"
                                )
                except Exception as e:
                    _log(f"    ⚠ Quiz solve failed for card #{card_id}: {e}")

            # 2. Claim the WeCard
            if not is_claimed:
                try:
                    if not fast:
                        time.sleep(random.uniform(1.5, 3.0))

                    claim_res = api.claim_wecard(card_id)
                    claimed_cards.add(card_id)
                    added_pts = (
                        claim_res.get("success", {}).get("pointsAdded", card_pts)
                    )
                    card_pts_total += added_pts
                    _log(
                        f"    🃏 WeCard #{card_id} claimed (dist: {dist:.0f}m)! → +{added_pts} Card Points"
                    )
                except Exception as e:
                    _log(f"    ⚠ WeCard claim failed for #{card_id}: {e}")
            else:
                claimed_cards.add(card_id)

    except Exception as e:
        _log(f"  ⚠ Failed to query nearby WeCards: {e}")

    return quiz_pts_total, card_pts_total, closest_unclaimed_target


def run_walk(
    phone_number: str,
    pin_code: str,
    target_steps: int | None = None,
    start_lat: float = 36.736003,
    start_lng: float = 10.214625,
    speed_kmh: float | None = None,
    fast: bool = False,
    check_wecards: bool = True,
    jitter_m: float = 100.0,
    version_app: str = "5.6",
):
    """
    Simulate a walk of *target_steps* steps (or auto-calculated to hit daily goal).

    Parameters
    ----------
    phone_number : str
        Account phone number.
    pin_code : str
        Account PIN code.
    target_steps : int | None
        Total steps to walk. If None, dynamically calculated to hit daily goal.
    start_lat, start_lng : float
        GPS starting point.
    speed_kmh : float | None
        Walking speed in km/h. None = random 3.5–5.5.
    fast : bool
        If True, don't sleep between steps (API calls only, no real-time wait).
    check_wecards : bool
        If True, search for and claim nearby WeCards & solve their Quizzes.
    jitter_m : float
        Random start position offset radius in metres.
    version_app : str
        App version for login payload (default: 5.6).
    """

    # ── Authenticate ───────────────────────────────────────────────
    _log(f"Authenticating via /user/login for phone {phone_number}…")
    try:
        api = WalkApi.login(
            phone_number=phone_number, pin_code=pin_code, version_app=version_app
        )
        profile = api.get_profile()
        user = profile.get("data", {})
        _log(
            f"  ✓ Login successful! User: {user.get('username')}  Status: {user.get('state')}"
        )
    except Exception as e:
        _log(f"  ✗ Authentication failed: {e}")
        sys.exit(1)

    # ── Fetch server params ────────────────────────────────────────
    params_resp = api.get_params()
    srv = params_resp.get("success", [{}])[0]
    max_speed = srv.get("maxSpeed", 10)
    stable_speed = srv.get("stableSpeed", 2.5)
    _log(
        f"  Server params: maxSpeed={max_speed} km/h, stableSpeed={stable_speed} km/h"
    )

    # ── Initialize path walker ─────────────────────────────────────
    walker = PathWalker(
        start_lat=start_lat,
        start_lng=start_lng,
        stride_m=STRIDE_M,
        speed_kmh=speed_kmh,
        jitter_m=jitter_m,
    )

    # Clamp speed to server's window
    if walker.speed_kmh < stable_speed:
        walker.speed_kmh = stable_speed + 0.5
    if walker.speed_kmh > max_speed:
        walker.speed_kmh = max_speed - 0.5

    _log(
        f"  Walking speed: {walker.speed_kmh:.1f} km/h  "
        f"(cadence ≈ {walker.cadence_spm:.0f} spm)"
    )

    # ── Create the initial activity ────────────────────────────────
    _log("")
    _log("Step 0: Creating activity with initial location save…")
    resp = api.send_location(walker.lat, walker.lng)
    _log(f"  ✓ {resp}")

    activity = api.get_activity()
    server_steps = activity.get("valid_steps", 0)
    server_duration = float(activity.get("duration", 0))
    server_distance = float(activity.get("valid_distance", 0))
    date_server = activity.get("date_server", "")

    # Perform initial sync to query server's dailyGoal
    ds_date = (
        date_server.split("T")[0]
        if "T" in date_server
        else datetime.now().strftime("%Y-%m-%d")
    )
    daily_goal = 14000
    try:
        init_sync = api.change_activity(
            nb_valid=server_steps,
            duration=round(server_duration, 3),
            valid_distance=round(server_distance, 2),
            activity_date=ds_date,
        )
        if init_sync.get("status") == 200:
            daily_goal = int(init_sync.get("success", {}).get("dailyGoal", 14000))
    except Exception:
        pass

    _log(
        f"  Activity id={activity.get('id')}  "
        f"steps={server_steps}  dist={server_distance}  "
        f"dailyGoal={daily_goal}  date_server={date_server}"
    )

    # Dynamic step calculation to barely hit dailyGoal if target_steps is not specified
    if target_steps is None:
        remaining = daily_goal - server_steps
        if remaining <= 0:
            target_steps = random.randint(30, 150)
            _log(
                f"  ✓ Daily goal of {daily_goal} steps already met! Walking small maintenance ({target_steps} steps)…"
            )
        else:
            current_hour = datetime.now().hour
            # Morning run (before 14:00 UTC) and < 40% completed
            if current_hour < 14 and server_steps < daily_goal * 0.4:
                ratio = random.uniform(0.45, 0.55)
                target_steps = int(daily_goal * ratio)
                _log(
                    f"  Morning run: targeting ~{int(ratio*100)}% of daily goal ({target_steps} steps)…"
                )
            else:
                buffer_steps = random.randint(10, 80)
                target_steps = remaining + buffer_steps
                _log(
                    f"  Targeting daily goal ({daily_goal} steps): {remaining} remaining + {buffer_steps} buffer = {target_steps} steps…"
                )

    # Round target to nearest 10
    target_steps = (target_steps // 10) * 10
    if target_steps < 10:
        target_steps = 10

    est_time_min = (target_steps * walker.step_interval_s) / 60
    est_dist = target_steps * STRIDE_M
    _log(
        f"  Target: {target_steps} steps ≈ {_format_distance(est_dist)} "
        f"≈ {est_time_min:.0f} min"
    )

    claimed_cards: set[int] = set()
    total_quiz_points = 0
    total_card_points = 0

    if check_wecards:
        _log("  Checking initial location for WeCards & Quizzes…")
        q_pts, c_pts, next_target = process_wecards_and_quizzes(
            api, walker.lat, walker.lng, claimed_cards, fast=fast
        )
        total_quiz_points += q_pts
        total_card_points += c_pts
        if next_target:
            _log(f"  Tracking nearby WeCard at {next_target}...")
            walker.set_target(next_target[0], next_target[1])

    # ── Walk loop ──────────────────────────────────────────────────
    _log("")
    _log(
        f"Starting walk: {target_steps} steps from "
        f"({walker.lat:.6f}, {walker.lng:.6f})"
    )
    _log("─" * 60)

    accepted_steps = 0
    steps_since_location = 0
    steps_since_flush = 0
    walk_start = time.time()
    last_step_time = time.time()
    batch_distance = 0.0
    batch_start_time = time.time()

    while accepted_steps < target_steps:
        # ── Simulate step timing ───────────────────────────────────
        cadence = random.uniform(80, 120)
        step_gap = 60.0 / cadence

        if not fast:
            elapsed = time.time() - last_step_time
            sleep_time = max(0, step_gap - elapsed)
            time.sleep(sleep_time)

        last_step_time = time.time()

        # ── Advance GPS position ───────────────────────────────────
        lat, lng = walker.step()
        accepted_steps += 1
        steps_since_location += 1
        steps_since_flush += 1
        batch_distance += walker.stride_m * random.uniform(0.95, 1.05)

        # ── Send GPS location every 3 steps ────────────────────────
        if steps_since_location >= LOCATION_SEND_INTERVAL:
            try:
                api.send_location(lat, lng)
                steps_since_location = 0
            except Exception as e:
                _log(f"  ⚠ location/save failed: {e}")

        # ── Flush batch every 10 steps ─────────────────────────────
        if steps_since_flush >= FLUSH_INTERVAL:
            can_send = (steps_since_flush // 10) * 10
            elapsed_min = (time.time() - batch_start_time) / 60.0

            new_steps = server_steps + can_send
            new_duration = server_duration + elapsed_min
            new_distance = server_distance + batch_distance

            # Get activity date from server
            try:
                act = api.get_activity()
                ds = act.get("date_server", "")
                if "T" in ds:
                    activity_date = ds.split("T")[0]
                else:
                    activity_date = datetime.now().strftime("%Y-%m-%d")

                resp = api.change_activity(
                    nb_valid=new_steps,
                    duration=round(new_duration, 3),
                    valid_distance=round(new_distance, 2),
                    activity_date=activity_date,
                )

                success = resp.get("success", {})
                server_steps = success.get("validSteps", new_steps)
                server_distance = float(success.get("validDistance", new_distance))
                server_duration = float(success.get("duration", new_duration))
                points = success.get("dailyAchievedNumberOfPoints", 0)
                goal = success.get("dailyGoal", "?")

                pct = (
                    (server_steps / int(goal) * 100) if goal and goal != "?" else 0
                )
                _log(
                    f"  ✓ Steps: {server_steps}/{goal} ({pct:.1f}%)  "
                    f"Dist: {_format_distance(server_distance)}  "
                    f"Dur: {server_duration:.1f} min  "
                    f"Pts: {points}  "
                    f"[+{can_send} batch]"
                )

                if check_wecards:
                    q_pts, c_pts, next_target = process_wecards_and_quizzes(
                        api, lat, lng, claimed_cards, fast=fast
                    )
                    total_quiz_points += q_pts
                    total_card_points += c_pts
                    if next_target:
                        walker.set_target(next_target[0], next_target[1])
                    else:
                        walker.clear_target()

            except Exception as e:
                _log(f"  ✗ change-test failed: {e}")

            # Reset batch counters
            steps_since_flush = 0
            batch_distance = 0.0
            batch_start_time = time.time()

        # ── Progress line (every 50 steps) ─────────────────────────
        elif accepted_steps % 50 == 0:
            elapsed_total = time.time() - walk_start
            remaining = target_steps - accepted_steps
            eta_s = remaining * walker.step_interval_s if not fast else 0
            _log(
                f"  … {accepted_steps}/{target_steps}  "
                f"pos=({lat:.6f}, {lng:.6f})  "
                f"dist={_format_distance(walker.total_distance_m)}  "
            )

    # ── Final summary ──────────────────────────────────────────────
    wall_time = time.time() - walk_start
    _log("─" * 60)
    _log("Walk complete!")
    _log(f"  Steps walked   : {accepted_steps}")
    _log(f"  Server steps   : {server_steps}")
    _log(f"  Distance       : {_format_distance(server_distance)}")
    _log(f"  Duration       : {server_duration:.1f} min")
    _log(f"  Wall time      : {wall_time / 60:.1f} min")
    _log(f"  End position   : ({walker.lat:.6f}, {walker.lng:.6f})")
    _log(f"  WeCards claimed: {len(claimed_cards)} cards (+{total_card_points} pts)")
    _log(f"  Quizzes solved : +{total_quiz_points} pts")
    _log(f"  Total Session Pts: {total_card_points + total_quiz_points} pts")


def main():
    parser = argparse.ArgumentParser(
        description="Walk&Win walk simulator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-s",
        "--steps",
        type=int,
        default=None,
        help="Number of steps to walk (default: auto-calculated to hit dailyGoal)",
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=None,
        help="Walking speed in km/h (default: random 3.5–5.5)",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Skip real-time delays (API calls only, no sleeping)",
    )
    parser.add_argument(
        "--no-wecards",
        action="store_true",
        help="Disable automatic WeCard scanning & Quiz solving",
    )
    parser.add_argument(
        "--lat",
        type=float,
        default=36.736003,
        help="Starting latitude (default: 36.736003)",
    )
    parser.add_argument(
        "--lng",
        type=float,
        default=10.214625,
        help="Starting longitude (default: 10.214625)",
    )
    parser.add_argument(
        "--jitter",
        type=float,
        default=100.0,
        help="Random start position offset up to +/- N metres (default: 100.0)",
    )
    parser.add_argument(
        "--phone",
        type=str,
        default=None,
        help="Account phone number (default: from PHONE_NUMBER env)",
    )
    parser.add_argument(
        "--pin",
        type=str,
        default=None,
        help="Account PIN code (default: from PIN_CODE env)",
    )
    args = parser.parse_args()

    # Load .env
    env_path = Path(__file__).parent / ".env"
    load_dotenv(env_path)
    phone_number = (
        args.phone
        or os.environ.get("PHONE_NUMBER")
        or os.environ.get("PHONE")
        or os.environ.get("WALK_WIN_PHONE")
    )
    pin_code = (
        args.pin
        or os.environ.get("PIN_CODE")
        or os.environ.get("PIN")
        or os.environ.get("WALK_WIN_PIN")
    )
    version_app = os.environ.get("VERSION_APP", "5.6")

    if not phone_number or not pin_code:
        print("Error: PHONE_NUMBER and PIN_CODE must be set in .env or passed via CLI")
        print(f"Create {env_path} with:\nPHONE_NUMBER=99999999\nPIN_CODE=9999")
        sys.exit(1)

    steps = args.steps  # None if omitted -> triggers auto dailyGoal calculation

    _log(f"Walk&Win Simulator")
    _log(f"  Account Phone     : {phone_number}")
    _log(f"  Starting center   : ({args.lat}, {args.lng}) ±{args.jitter}m")
    _log(f"  Target steps      : {steps if steps is not None else 'auto (hit dailyGoal)'}")
    _log(f"  Mode              : {'fast (no delays)' if args.fast else 'real-time'}")
    _log(f"  WeCards & Quizzes : {'disabled' if args.no_wecards else 'enabled'}")
    _log("")

    run_walk(
        phone_number=phone_number,
        pin_code=pin_code,
        target_steps=steps,
        start_lat=args.lat,
        start_lng=args.lng,
        speed_kmh=args.speed,
        fast=args.fast,
        check_wecards=not args.no_wecards,
        jitter_m=args.jitter,
        version_app=version_app,
    )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Walk&Win Leaderboard & User Profile Info script.

Fetches and displays:
1. Account Profile Info (/api/user/get)
2. Global Top 10 Leaderboard & Scores (/api/user/order/1/10)
3. User Rank Position (/api/user/order_user)
4. Today's Activity Stats (/api/activity/get)
5. Server Configuration Params (/api/params)
"""

import os
import sys
import argparse
from datetime import datetime
from pathlib import Path

# Fix Windows console encoding
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv

from api import WalkApi


def _log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def _format_distance(m: float) -> str:
    return f"{m / 1000:.2f} km" if m >= 1000 else f"{m:.0f} m"


def show_leaderboard_info(
    phone_number: str, pin_code: str, version_app: str = "5.6", top_count: int = 10
):
    _log("Authenticating with Walk&Win server…")
    try:
        api = WalkApi.login(
            phone_number=phone_number, pin_code=pin_code, version_app=version_app
        )
    except Exception as e:
        _log(f"✗ Authentication failed: {e}")
        sys.exit(1)

    print("\n" + "=" * 65)
    print(" 🏆 WALK&WIN GLOBAL LEADERBOARD & USER PROFILE")
    print("=" * 65)

    # 1. User Profile
    user_id = None
    try:
        prof_res = api.get_profile()
        user_data = prof_res.get("data", {})
        user_id = user_data.get("id")
        print("\n👤 YOUR PROFILE:")
        print(f"  • Username / Phone : {user_data.get('username')}")
        print(f"  • User ID          : {user_data.get('id')}")
        print(f"  • Account State    : {user_data.get('state')}")
        print(f"  • Subscription ID  : {user_data.get('subscriptionId')}")
        print(
            f"  • Gender / Height  : {user_data.get('gender')} / {user_data.get('height')}m"
        )
    except Exception as e:
        print(f"  ⚠ Failed to fetch profile: {e}")

    # 2. Leaderboard Rank Position & Top Players
    try:
        lb_res = api.get_leaderboard(offset=1, limit=top_count)
        lb_success = lb_res.get("success", {})
        lb_outer = lb_success.get("data", {})
        top_players = lb_outer.get("data", [])

        user_rank = lb_outer.get("order")
        user_total_pts = lb_outer.get("points")
        total_players = lb_outer.get("totalData", "N/A")

        print(f"\n🥇 TOP {top_count} LEADERBOARD (Total Players: {total_players}):")
        print(f"  {'Rank':<6} {'Phone / User':<16} {'Score (Points)':<15}")
        print("  " + "─" * 42)

        for player in top_players:
            r = player.get("order", "?")
            phone = player.get("phoneNumber", "N/A")
            pts = player.get("numberOfPoints", 0)

            # Highlight current user if match
            is_me = phone == str(phone_number)
            me_marker = "  ◄ YOU" if is_me else ""

            medal = (
                "🥇"
                if r == 1
                else ("🥈" if r == 2 else ("🥉" if r == 3 else f"#{r:<3}"))
            )
            print(f"  {medal:<6} {phone:<16} {pts:>8,} pts{me_marker}")

        print("  " + "─" * 42)

        # Print user's exact position
        rank_display = f"#{user_rank}" if user_rank else "N/A"
        pts_display = f"{user_total_pts:,} pts" if user_total_pts is not None else "N/A"
        print(f"  📍 YOUR RANK: {rank_display} out of {total_players} ({pts_display})\n")

    except Exception as e:
        print(f"  ⚠ Failed to fetch leaderboard: {e}")

    # 3. Today's Activity Stats
    try:
        act_data = api.get_activity()
        steps = act_data.get("valid_steps", 0)
        dist = float(act_data.get("valid_distance", 0))
        dur = float(act_data.get("duration", 0))
        pts = act_data.get("numberOfPoints", "N/A")
        print("👟 TODAY'S ACTIVITY STATS:")
        print(f"  • Activity ID      : {act_data.get('id')}")
        print(f"  • Valid Steps Today: {steps:,}")
        print(
            f"  • Valid Distance   : {_format_distance(dist * 1000 if dist < 100 else dist)}"
        )
        print(f"  • Duration         : {dur:.1f} min")
        print(f"  • Lifetime Points  : {pts}")
        print(f"  • Server Date      : {act_data.get('date_server')}")
    except Exception as e:
        print(f"  ⚠ Failed to fetch activity stats: {e}")

    # 4. Server Params
    try:
        params_res = api.get_params()
        srv = params_res.get("success", [{}])[0]
        print("\n⚙️ SERVER CONFIGURATION:")
        print(f"  • Max Speed        : {srv.get('maxSpeed')} km/h")
        print(f"  • Stable Speed     : {srv.get('stableSpeed')} km/h")
        print(f"  • Phone Shake Speed: {srv.get('phoneShakingSpeed')}")
    except Exception as e:
        print(f"  ⚠ Failed to fetch server params: {e}")

    print("\n" + "=" * 65 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Walk&Win Leaderboard & Profile Info",
        formatter_class=argparse.RawDescriptionHelpFormatter,
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
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        help="Number of top players to display (default: 10)",
    )
    args = parser.parse_args()

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
        sys.exit(1)

    show_leaderboard_info(phone_number, pin_code, version_app, top_count=args.top)


if __name__ == "__main__":
    main()

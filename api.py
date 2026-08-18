"""
Walk&Win API client — matches exact endpoints and headers from the app.
Reverse-engineered from decompiled Hermes v96 bytecode.
"""

import requests
from datetime import datetime


BASE_URL = "https://api-symfony.walkandwin.tn"

_HEADERS_TEMPLATE = {
    "accept": "application/json, text/plain, */*",
    "User-Agent": "okhttp/4.9.2",
    "Content-Type": "application/json",
}


class WalkApi:
    def __init__(self, token: str):
        self.token = token
        self.session = requests.Session()
        self.session.headers.update(_HEADERS_TEMPLATE)
        self.session.headers["Authorization"] = f"Bearer {token}"

    # ── POST /user/login ───────────────────────────────────────────
    # Authenticates with phone number and PIN code, returning a fresh token.
    @classmethod
    def login(
        cls,
        phone_number: str,
        pin_code: str,
        version_app: str = "5.6",
    ) -> "WalkApi":
        url = f"{BASE_URL}/user/login"
        headers = {
            "accept": "application/json, text/plain, */*",
            "User-Agent": "okhttp/4.9.2",
            "Content-Type": "application/json",
            "Pragma": "no-cache",
            "Cache-Control": "no-cache",
        }
        body = {
            "phoneNumber": str(phone_number),
            "pinCode": str(pin_code),
            "versionApp": str(version_app),
        }
        r = requests.post(url, json=body, headers=headers, timeout=30)
        r.raise_for_status()
        data = r.json()

        if data.get("status") == 200 and "success" in data:
            token = data["success"].get("token")
            if token:
                return cls(token)

        raise ValueError(f"Login failed: {data}")

    # ── POST /api/location/save ─────────────────────────────────────
    # Called every 3 accepted steps by the background tracker.
    # Creates the server-side activity if none exists.
    def send_location(self, lat: float, lng: float) -> dict:
        r = self.session.post(
            f"{BASE_URL}/api/location/save",
            json={"latitude": lat, "longitude": lng},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    # ── GET /api/activity/get ───────────────────────────────────────
    # Returns current activity state: valid_steps, duration,
    # valid_distance, date_server, numberOfPoints.
    def get_activity(self) -> dict:
        r = self.session.get(
            f"{BASE_URL}/api/activity/get",
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    # ── POST /api/location/change-test ──────────────────────────────
    # Updates the walk stats. activityDate must be YYYY-MM-DD
    # (derived from date_server via toLocaleDateString('fr-CA')).
    def change_activity(
        self,
        nb_valid: int,
        duration: float,
        valid_distance: float,
        activity_date: str,
    ) -> dict:
        r = self.session.post(
            f"{BASE_URL}/api/location/change-test",
            json={
                "nbValid": nb_valid,
                "duration": duration,
                "validDistance": valid_distance,
                "activityDate": activity_date,
            },
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    # ── GET /api/params ─────────────────────────────────────────────
    # Server-side config: maxSpeed, stableSpeed, phoneShakingSpeed.
    def get_params(self) -> dict:
        r = self.session.get(f"{BASE_URL}/api/params", timeout=30)
        r.raise_for_status()
        return r.json()

    # ── GET /api/user/get ───────────────────────────────────────────
    def get_profile(self) -> dict:
        r = self.session.get(f"{BASE_URL}/api/user/get", timeout=30)
        r.raise_for_status()
        return r.json()

    # ── POST /api/wecards/nearby ────────────────────────────────────
    # Returns nearby WeCards collectibles relative to (lat, lng).
    def get_nearby_wecards(self, lat: float, lng: float) -> dict:
        r = self.session.post(
            f"{BASE_URL}/api/wecards/nearby",
            json={"latitude": lat, "longitude": lng},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    # ── GET /api/wecards/{card_id}/quiz ─────────────────────────────
    # Fetches quiz questions for a specific WeCard.
    def get_wecard_quiz(self, wecard_id: int) -> dict:
        r = self.session.get(
            f"{BASE_URL}/api/wecards/{wecard_id}/quiz",
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    # ── POST /api/quiz/{quiz_id}/answer ─────────────────────────────
    # Submits answer key for a quiz.
    def send_quiz_answer(
        self, quiz_id: int, answer_key: str, response_time: int = 5
    ) -> dict:
        r = self.session.post(
            f"{BASE_URL}/api/quiz/{quiz_id}/answer",
            json={"answer": answer_key, "responseTime": response_time},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    # ── POST /api/wecards/{card_id}/claim ───────────────────────────
    # Claims a WeCard to add its points to the user profile.
    def claim_wecard(self, wecard_id: int) -> dict:
        r = self.session.post(
            f"{BASE_URL}/api/wecards/{wecard_id}/claim",
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

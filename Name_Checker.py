#!/usr/bin/env python3
"""
Roblox / Minecraft / Discord username availability checker.

Checks random 3- and 4-character usernames (letters + digits).
Found names appear in green above a live counter at the bottom of the
console, and are saved to Names.txt.

Setup:
    pip install requests

Run:
    python name_checker.py
"""

import base64
import json
import os
import random
import string
import sys
import time

import requests

# Enable ANSI colors on Windows terminals
if os.name == "nt":
    os.system("")

# ----------------------------------------------------------------------
# Colors
# ----------------------------------------------------------------------
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
DIM = "\033[2m"
RESET = "\033[0m"

# ----------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------
ROBLOX_VALIDATE_URL = "https://auth.roblox.com/v1/usernames/validate"
DISCORD_USERNAME_URL = "https://discord.com/api/v9/unique-username/username-attempt-unauthed"
MOJANG_PROFILE_URL = "https://api.mojang.com/users/profiles/minecraft/{}"

# Character pool for generated names: letters + digits.
CHARSET = string.ascii_lowercase + string.digits

# Where available names get saved
OUTFILE = "Names.txt"

# Seconds between checks (be nice, or you'll get 429'd)
DELAY = {"roblox": 0.7, "minecraft": 1.2, "discord": 1.0}

session = requests.Session()
session.headers["User-Agent"] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_csrf_token = None


# ----------------------------------------------------------------------
# Roblox
# ----------------------------------------------------------------------
def roblox_check(name, retries=3):
    """Return (True, msg) available, (False, msg) taken, (None, msg) error."""
    global _csrf_token

    payload = {
        "birthday": "2000-01-01T00:00:00.000Z",
        "context": "Signup",
        "username": name,
    }

    for attempt in range(retries):
        headers = {}
        if _csrf_token:
            headers["X-CSRF-TOKEN"] = _csrf_token

        try:
            r = session.post(ROBLOX_VALIDATE_URL, json=payload,
                             headers=headers, timeout=10)
        except requests.RequestException:
            time.sleep(1 + attempt)
            continue

        if r.status_code == 403:              # get CSRF token, then retry
            token = r.headers.get("x-csrf-token")
            if token:
                _csrf_token = token
                continue
            time.sleep(1 + attempt)
            continue

        if r.status_code == 429:
            time.sleep(5 * (attempt + 1))
            continue

        if r.status_code == 200:
            data = r.json()
            code = data.get("code")
            message = data.get("message", "")
            if code == 0:
                return True, message          # valid / not taken
            if code == 1:
                return False, message         # already taken
            return False, message             # moderated / invalid

        time.sleep(1 + attempt)

    return None, "request failed"


# ----------------------------------------------------------------------
# Minecraft
# ----------------------------------------------------------------------
def minecraft_check(name, retries=3):
    for attempt in range(retries):
        try:
            r = session.get(MOJANG_PROFILE_URL.format(name), timeout=10)
        except requests.RequestException:
            time.sleep(1 + attempt)
            continue

        if r.status_code == 429:
            time.sleep(5 * (attempt + 1))
            continue

        if r.status_code == 200:          # profile exists -> taken
            return False, "taken"
        if r.status_code in (204, 404):   # no profile -> free
            return True, "available"

        time.sleep(1 + attempt)

    return None, "request failed"


# ----------------------------------------------------------------------
# Discord
# ----------------------------------------------------------------------
def _discord_super_properties():
    props = {
        "os": "Windows",
        "browser": "Chrome",
        "device": "",
        "system_locale": "en-US",
        "browser_user_agent": session.headers["User-Agent"],
        "browser_version": "124.0.0.0",
        "os_version": "10",
        "referrer": "",
        "referring_domain": "",
        "referrer_current": "",
        "referring_domain_current": "",
        "release_channel": "stable",
        "client_build_number": 300000,
        "client_event_source": None,
    }
    raw = json.dumps(props, separators=(",", ":")).encode()
    return base64.b64encode(raw).decode()


def discord_check(name, retries=3):
    """
    Check a Discord username via the public (unauthenticated) endpoint.

    NOTE: Discord frequently demands a captcha on this endpoint, in which
    case the server returns HTTP 400 with a `captcha_key` field. When that
    happens we report 'captcha required' — there is no way around it
    without solving the captcha.
    """
    headers = {
        "Content-Type": "application/json",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "X-Super-Properties": _discord_super_properties(),
        "X-Discord-Locale": "en-US",
        "X-Debug-Options": "bugReporterEnabled",
        "Origin": "https://discord.com",
        "Referer": "https://discord.com/register",
    }

    last_error = "request failed"

    for attempt in range(retries):
        try:
            r = session.post(
                DISCORD_USERNAME_URL,
                json={"username": name},
                headers=headers,
                timeout=10,
            )
        except requests.RequestException as e:
            last_error = type(e).__name__
            time.sleep(1 + attempt)
            continue

        if r.status_code == 429:
            # Honor Retry-After if Discord provides it.
            try:
                wait = float(r.headers.get("Retry-After", 5 * (attempt + 1)))
            except ValueError:
                wait = 5 * (attempt + 1)
            time.sleep(wait)
            continue

        # Try to parse JSON body regardless of status code.
        try:
            data = r.json()
        except ValueError:
            data = {}

        if r.status_code == 200:
            if data.get("taken") is True:
                return False, "taken"
            if data.get("taken") is False:
                return True, "available"
            # Unexpected but successful response — surface it.
            return None, str(data)[:40]

        # Discord signals "needs captcha" via captcha_key in the body.
        if "captcha_key" in data:
            last_error = "captcha required"
            # No point hammering if every request wants a captcha.
            return None, last_error

        if r.status_code == 400:
            msg = data.get("message") or "bad request"
            return None, str(msg)[:40]

        last_error = f"http {r.status_code}"
        time.sleep(1 + attempt)

    return None, last_error


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def random_name(length):
    return "".join(random.choice(CHARSET) for _ in range(length))


def save_name(platform, name):
    """Append a found name to Names.txt as 'platform: name'."""
    with open(OUTFILE, "a") as f:
        f.write(f"{platform}: {name}\n")


CHECKERS = {
    "roblox": roblox_check,
    "minecraft": minecraft_check,
    "discord": discord_check,
}


def run_checker(platform):
    check = CHECKERS[platform]
    delay = DELAY[platform]

    print(f"\n=== Checking {platform.upper()} 3- and 4-character names ===")
    print(f"Available names are saved to {OUTFILE}")
    print("Press Ctrl+C to stop.\n")

    seen = set()
    found = []
    checked = 0
    length = 3

    try:
        while True:
            name = random_name(length)
            if name in seen:
                continue
            seen.add(name)

            available, message = check(name)
            checked += 1

            # Erase the counter line so we can redraw underneath found names
            sys.stdout.write("\r\033[K")

            if available is True:
                # Print the found name (it stays above the counter)
                print(f"{GREEN}[+] {name}  ({length} chars) -> AVAILABLE{RESET}")
                found.append(name)
                save_name(platform, name)
                status = f"{DIM}...{RESET}"
            elif available is False:
                status = f"{RED}[-]{RESET} {name} {DIM}{message[:20]:<20}{RESET}"
            else:
                status = f"{YELLOW}[?]{RESET} {name} {DIM}{message[:20]:<20}{RESET}"

            # Redraw the counter at the bottom of the console
            counter = f"checked: {checked}  found: {len(found)}"
            sys.stdout.write(f"{status}  {counter}")
            sys.stdout.flush()

            length = 4 if length == 3 else 3
            time.sleep(delay)

    except KeyboardInterrupt:
        sys.stdout.write("\r\033[K")
        print("\nStopped.")
        print(f"Checked {checked} names, found {len(found)} available.")
        if found:
            print(f"{GREEN}Available: {', '.join(found)}{RESET}")


# ----------------------------------------------------------------------
# Menu
# ----------------------------------------------------------------------
def main():
    while True:
        print("\n================================")
        print("           NAME CHECKER")
        print("================================")
        print(" 1) Roblox")
        print(" 2) Minecraft")
        print(" 3) Discord")
        print(" 0) Quit")
        print("================================")

        choice = input("Choose: ").strip()

        if choice == "1":
            run_checker("roblox")
        elif choice == "2":
            run_checker("minecraft")
        elif choice == "3":
            run_checker("discord")
        elif choice == "0":
            print("Bye.")
            sys.exit(0)
        else:
            print("Invalid choice.")


if __name__ == "__main__":
    main()
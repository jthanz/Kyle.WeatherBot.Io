#!/usr/bin/env python3
"""
Once a day: find the hottest place on Earth (from a curated candidate list),
write a short message in the voice of a famous person from that state/country,
tag a specific server member, and post it to a Discord channel.

If the hottest place is the same as the previous run, a different famous
person is used (tracked in state.json).

Environment variables:
  DISCORD_WEBHOOK_URL  - webhook URL for the target channel
  DISCORD_USER_ID      - numeric ID of the member to tag (Copy User ID in Discord)
  ANTHROPIC_API_KEY    - your Anthropic API key
  STATE_FILE           - optional, defaults to state.json
"""

import os
import sys
import json
import requests
import anthropic

from hot_locations import HOT_LOCATIONS

MODEL = "claude-sonnet-5"          # good at voice/parody; swap if needed
STATE_FILE = os.environ.get("STATE_FILE", "state.json")


# --- Temperature data (Open-Meteo, free, no key, returns F directly) --------
def daily_max_f(lat: float, lon: float):
    r = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat,
            "longitude": lon,
            "daily": "temperature_2m_max",
            "temperature_unit": "fahrenheit",
            "timezone": "auto",
            "forecast_days": 1,
        },
        timeout=30,
    )
    r.raise_for_status()
    highs = r.json().get("daily", {}).get("temperature_2m_max", [])
    return highs[0] if highs else None


def hottest_location():
    best = None
    for loc in HOT_LOCATIONS:
        try:
            temp = daily_max_f(loc["lat"], loc["lon"])
        except Exception as e:
            print(f"skip {loc['place']}: {e}", file=sys.stderr)
            continue
        if temp is None:
            continue
        if best is None or temp > best["temp_f"]:
            best = {**loc, "temp_f": temp}
    return best


# --- Persistent state for the "different person if repeated" rule -----------
def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"last_place": None, "used_people": []}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# --- Persona message via Claude --------------------------------------------
def compose(loc, excluded):
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    system = (
        "You write one short, fun daily weather message for a Discord server. "
        "Voice it as an affectionate, obviously comedic parody of the SPEAKING "
        "STYLE (cadence, catchphrases) of a famous person from the given place. "
        "Keep it clearly fictional and light: PG-rated, no political content, no "
        "controversial or defamatory claims, and nothing framed as a real quote "
        "the person actually said. Include the placeholder {{MENTION}} exactly "
        "once, addressing the tagged user. State the location and the temperature "
        "in Fahrenheit. Under 90 words. "
        'Respond with ONLY a JSON object: {"person": "...", "message": "..."}'
    )
    user = (
        f"Place: {loc['place']}\n"
        f"Choose the famous person from: {loc['origin']}\n"
        f"Temperature (daily high): {round(loc['temp_f'])} F\n"
        f"Do NOT use any of these, already used recently: "
        f"{', '.join(excluded) if excluded else 'none'}\n"
        "Pick a recognizable, different famous person from that place."
    )
    resp = client.messages.create(
        model=MODEL, max_tokens=600, system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    # tolerate ```json fences
    if text.startswith("```"):
        text = text.strip("`")
        text = text[4:].strip() if text.lower().startswith("json") else text
    return json.loads(text)


def main():
    webhook = os.environ["DISCORD_WEBHOOK_URL"]
    user_id = str(os.environ["DISCORD_USER_ID"])

    loc = hottest_location()
    if not loc:
        print("No temperature data available.", file=sys.stderr)
        return 1

    state = load_state()
    same_place = state.get("last_place") == loc["place"]
    excluded = state.get("used_people", []) if same_place else []

    result = compose(loc, excluded)
    person = result["person"]
    message = result["message"].replace("{{MENTION}}", f"<@{user_id}>")
    if f"<@{user_id}>" not in message:
        message = f"<@{user_id}> {message}"

    resp = requests.post(
        webhook,
        json={"content": message[:2000],
              "allowed_mentions": {"users": [user_id]}},
        timeout=30,
    )
    resp.raise_for_status()

    if same_place:
        state["used_people"].append(person)
    else:
        state["last_place"] = loc["place"]
        state["used_people"] = [person]
    save_state(state)

    print(f"Posted as {person} for {loc['place']} ({round(loc['temp_f'])} F)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
Once a day: find the hottest place on Earth (from a curated candidate list),
write a short message in the voice of a famous person from that state/country,
address it to Kyle, and post it to a Discord channel.

If the hottest place is the same as the previous run, a different famous
person is used (tracked in state.json).

Environment variables:
  DISCORD_WEBHOOK_URL  - webhook URL for the target channel
  ANTHROPIC_API_KEY    - your Anthropic API key
  WEATHER_API_KEY      - your free WeatherAPI.com key
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
RECIPIENT = "Kyle"                 # the name the message is addressed to


# --- Temperature data (WeatherAPI.com: free key, cloud-friendly, returns F) --
def daily_max_f(lat: float, lon: float):
    r = requests.get(
        "https://api.weatherapi.com/v1/forecast.json",
        params={
            "key": os.environ["WEATHER_API_KEY"],
            "q": f"{lat},{lon}",
            "days": 1,
        },
        timeout=30,
    )
    r.raise_for_status()
    days = r.json().get("forecast", {}).get("forecastday", [])
    return days[0]["day"]["maxtemp_f"] if days else None


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
        "Voice it Arnold Schwarzenegger trying to give a motivational speach about how kyle would never survive the heat in this given region"
        "Add - Arnold to the end of each message so people are sure it's him"
        "the person actually said. Include the placeholder {{MENTION}} exactly "
        "once, as the name of the person you're speaking to. State the location "
        "and the temperature in Fahrenheit. Under 90 words. "
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

    loc = hottest_location()
    if not loc:
        print("No temperature data available.", file=sys.stderr)
        return 1

    state = load_state()
    same_place = state.get("last_place") == loc["place"]
    excluded = state.get("used_people", []) if same_place else []

    result = compose(loc, excluded)
    person = result["person"]
    message = result["message"].replace("{{MENTION}}", RECIPIENT)
    if RECIPIENT not in message:
        message = f"{RECIPIENT}, {message}"

    resp = requests.post(
        webhook,
        json={"content": message[:2000]},
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

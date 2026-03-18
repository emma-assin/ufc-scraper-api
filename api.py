from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
import json
import os
import re

BASE_URL = "https://www.ufc.com"
EVENTS_URL = f"{BASE_URL}/events"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    )
}
FALLBACK_IMG = "https://i.imgur.com/0X4vFQy.png"
FIGHTER_CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fighter_profile_cache.json")

# In-memory fighter profile cache; populated from disk and updated on new fetches
_fighter_cache: Dict[str, Any] = {}


def _load_fighter_cache() -> None:
    global _fighter_cache
    if os.path.exists(FIGHTER_CACHE_PATH):
        try:
            with open(FIGHTER_CACHE_PATH, "r") as f:
                _fighter_cache = json.load(f)
        except Exception:
            _fighter_cache = {}


def _save_fighter_cache() -> None:
    try:
        with open(FIGHTER_CACHE_PATH, "w") as f:
            json.dump(_fighter_cache, f, indent=2)
    except Exception:
        pass


_load_fighter_cache()

app = FastAPI(title="UFC Events API (Clean)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _get_soup(url: str) -> BeautifulSoup:
    res = requests.get(url, headers=HEADERS, timeout=15)
    if res.status_code != 200:
        raise HTTPException(status_code=500, detail=f"Failed to fetch {url}")
    return BeautifulSoup(res.text, "html.parser")


def _parse_events() -> List[Dict[str, Any]]:
    soup = _get_soup(EVENTS_URL)
    events: List[Dict[str, Any]] = []

    # UFC uses cards for both upcoming and past events
    for card in soup.select(".c-card-event--result"):
        title_el = card.select_one("h3 a")
        if not title_el or not title_el.get("href"):
            continue

        title = title_el.get_text(strip=True)
        url = BASE_URL + title_el["href"]

        date_el = card.select_one(".c-card-event--result__date")
        loc_el = card.select_one(".c-card-event--result__location")

        date_text = date_el.get("data-main-card", "") if date_el else ""
        loc_text = loc_el.get_text(strip=True) if loc_el else ""

        # data-main-card-timestamp is a Unix timestamp (UTC) provided directly by UFC.com
        timestamp: Optional[int] = None
        parsed_date: Optional[datetime] = None
        if date_el and date_el.get("data-main-card-timestamp"):
            try:
                timestamp = int(date_el["data-main-card-timestamp"])
                parsed_date = datetime.fromtimestamp(timestamp, tz=timezone.utc)
            except (ValueError, OSError):
                pass

        if parsed_date is None:
            continue

        events.append(
            {
                "EVENT": title,
                "URL": url,
                "DATE_TEXT": date_text,
                "LOCATION": loc_text,
                "DATE": parsed_date,
                "TIMESTAMP": timestamp,
            }
        )

    return events


def _slug_to_name(slug: str) -> str:
    if not slug or slug == "unknown":
        return "Unknown"
    return " ".join([p.capitalize() for p in slug.replace("_", "-").split("-") if p])


def _fetch_fighter_profile(slug: str) -> Dict[str, Any]:
    """Return fighter profile data (record, country, height, weight, reach, leg_reach).

    Results are cached in fighter_profile_cache.json.  Returns an empty dict on
    failure so callers always get a safe default.
    """
    if not slug or slug == "unknown":
        return {}

    slug = slug.lower()

    # Return from cache when the cached entry already has the new-format fields
    cached = _fighter_cache.get(slug, {})
    if "record" in cached:
        return {
            "record": cached.get("record", ""),
            "country": cached.get("country", ""),
            "height": cached.get("height", ""),
            "weight": cached.get("weight", ""),
            "reach": cached.get("reach", ""),
            "leg_reach": cached.get("leg_reach", ""),
        }

    # Fetch from UFC.com athlete page
    url = f"{BASE_URL}/athlete/{slug}"
    try:
        res = requests.get(url, headers=HEADERS, timeout=15)
        if res.status_code != 200:
            return {}

        soup = BeautifulSoup(res.text, "html.parser")

        # Record (e.g. "27-9-0 (W-L-D)")
        record = ""
        div_body = soup.select_one(".hero-profile__division-body")
        if div_body:
            m = re.match(r"(\d+-\d+-\d+)", div_body.get_text(strip=True))
            if m:
                record = m.group(1)

        # Bio fields
        bio: Dict[str, str] = {}
        for field in soup.select(".c-bio__field"):
            label_el = field.select_one(".c-bio__label")
            val_el = field.select_one(".c-bio__text")
            if label_el and val_el:
                bio[label_el.get_text(strip=True)] = val_el.get_text(strip=True)

        # Extract country as the last comma-separated part of Place of Birth
        birth_place = bio.get("Place of Birth", "")
        country = birth_place.split(",")[-1].strip() if birth_place else ""

        profile: Dict[str, str] = {
            "record": record,
            "country": country,
            "height": bio.get("Height", ""),
            "weight": bio.get("Weight", ""),
            "reach": bio.get("Reach", ""),
            "leg_reach": bio.get("Leg reach", ""),
        }

        # Persist to cache using the new flat format
        _fighter_cache[slug] = {
            "fetched_at": datetime.now().isoformat(),
            **profile,
        }
        _save_fighter_cache()

        return profile

    except Exception:
        return {}


def _fetch_event_results(fmid: str) -> Dict[int, Dict[str, Any]]:
    """
    Fetch fight results from the UFC CloudFront stats API.
    Returns a dict keyed by FightId (int) with winner_corner, method, round, time.
    """
    if not fmid:
        return {}
    url = f"https://d29dxerjsp82wz.cloudfront.net/api/v3/event/live/{fmid}.json"
    try:
        res = requests.get(url, headers=HEADERS, timeout=15)
        if res.status_code != 200:
            return {}
        data = res.json()
        results: Dict[int, Dict[str, Any]] = {}
        for fight in data.get("LiveEventDetail", {}).get("FightCard", []):
            fight_id = fight.get("FightId")
            if not fight_id:
                continue
            result = fight.get("Result") or {}
            winner_corner = None
            winner_name = None
            for fighter in fight.get("Fighters", []):
                outcome = (fighter.get("Outcome") or {}).get("Outcome", "")
                if outcome == "Win":
                    winner_corner = fighter.get("Corner")  # "Red" or "Blue"
                    winner_name = (
                        f"{fighter['Name']['FirstName']} {fighter['Name']['LastName']}"
                    )
            results[fight_id] = {
                "winner_corner": winner_corner,
                "winner_name": winner_name,
                "method": result.get("Method") or "",
                "round": str(result["EndingRound"]) if result.get("EndingRound") else "",
                "time": result.get("EndingTime") or "",
            }
        return results
    except Exception:
        return {}


def _parse_fighter_corner(corner) -> tuple:
    if not corner:
        return "Unknown", FALLBACK_IMG, ""

    link = corner.select_one("a")
    img = corner.select_one("img")

    slug = ""
    name = None
    if link and link.has_attr("href"):
        slug = link["href"].rstrip("/").split("/")[-1]
        name = _slug_to_name(slug)

    if not name or name == "Unknown":
        if img and img.get("alt"):
            name = img.get("alt").strip()

    img_url = img.get("src") if img and img.get("src") else None
    if not img_url:
        img_url = FALLBACK_IMG

    return name or "Unknown", img_url, slug


def _get_event_details(event_url: str) -> Dict[str, Any]:
    soup = _get_soup(event_url)

    # Extract event_fmid from drupalSettings JSON embedded in the page
    fmid = None
    for script in soup.find_all("script", type="application/json"):
        text = script.string or ""
        if "eventLiveStats" in text:
            try:
                settings = json.loads(text)
                fmid = settings.get("eventLiveStats", {}).get("event_fmid")
            except Exception:
                pass
            if fmid:
                break

    # Fetch fight results (winner, method, round, time) from the CloudFront stats API
    api_results = _fetch_event_results(fmid) if fmid else {}

    # Event hero image
    hero_img = None
    hero = soup.select_one(".c-hero__image img") or soup.select_one(".c-hero--full img")
    if hero and hero.get("src"):
        hero_img = hero["src"]

    fight_rows = soup.select(".c-listing-fight__content")
    fights: List[Dict[str, Any]] = []

    main_red_name = main_blue_name = None
    main_red_img = main_blue_img = None

    for i, row in enumerate(fight_rows):
        red_corner = row.select_one(".c-listing-fight__corner--red")
        blue_corner = row.select_one(".c-listing-fight__corner--blue")

        red_name, red_img, red_slug = _parse_fighter_corner(red_corner)
        blue_name, blue_img, blue_slug = _parse_fighter_corner(blue_corner)

        # Look up fight result from the stats API via the data-time-fid attribute
        time_div = row.select_one("[data-time-fid]")
        fight_id = int(time_div["data-time-fid"]) if time_div else None
        api_res = api_results.get(fight_id, {}) if fight_id else {}

        # Determine winner from API result
        winner = None
        winner_corner = api_res.get("winner_corner")  # "Red" or "Blue"
        if winner_corner == "Red":
            winner = red_name
        elif winner_corner == "Blue":
            winner = blue_name

        # Method / Round / Time from API result
        method = api_res.get("method", "")
        round_num = api_res.get("round", "")
        time = api_res.get("time", "")

        if i == 0:
            main_red_name, main_red_img = red_name, red_img
            main_blue_name, main_blue_img = blue_name, blue_img

        weight = row.select_one(".c-listing-fight__class-text")
        weight_text = weight.get_text(strip=True) if weight else ""

        if red_name != "Unknown" and blue_name != "Unknown":
            fight_title = f"{red_name} vs {blue_name}"
        elif red_name != "Unknown":
            fight_title = red_name
        elif blue_name != "Unknown":
            fight_title = blue_name
        else:
            fight_title = "Unknown Fight"

        red_profile = _fetch_fighter_profile(red_slug)
        blue_profile = _fetch_fighter_profile(blue_slug)

        fights.append(
            {
                "FIGHT": fight_title,
                "WEIGHT_CLASS": weight_text,
                "RED_NAME": red_name,
                "BLUE_NAME": blue_name,
                "RED_IMG": red_img,
                "BLUE_IMG": blue_img,
                "WINNER": winner,
                "METHOD": method,
                "ROUND": round_num,
                "TIME": time,
                "RED_PROFILE": red_profile,
                "BLUE_PROFILE": blue_profile,
            }
        )

    main_event = {
        "A": main_red_name or "Unknown",
        "B": main_blue_name or "Unknown",
        "A_IMG": main_red_img or FALLBACK_IMG,
        "B_IMG": main_blue_img or FALLBACK_IMG,
    }

    return {
        "IMAGE": hero_img or FALLBACK_IMG,
        "MAIN_EVENT": main_event,
        "FIGHTS": fights,
    }


def _pick_next_event(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    today = datetime.now().date()
    # Treat all events scheduled for today as upcoming until they are actually completed.
    upcoming = [e for e in events if e["DATE"].date() >= today]
    if not upcoming:
        raise HTTPException(status_code=404, detail="No upcoming events found")
    upcoming.sort(key=lambda e: e["DATE"])
    return upcoming[0]


def _pick_last_event(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    today = datetime.now().date()
    past = [e for e in events if e["DATE"].date() < today]
    if not past:
        raise HTTPException(status_code=404, detail="No past events found")
    past.sort(key=lambda e: e["DATE"], reverse=True)
    return past[0]


@app.get("/next")
def get_next_event():
    events = _parse_events()
    next_event = _pick_next_event(events)
    details = _get_event_details(next_event["URL"])

    return {
        "EVENT": next_event["EVENT"],
        "DATE": next_event["DATE_TEXT"],
        "TIMESTAMP": next_event["TIMESTAMP"],
        "LOCATION": next_event["LOCATION"],
        "URL": next_event["URL"],
        "IMAGE": details["IMAGE"],
        "MAIN_EVENT": details["MAIN_EVENT"],
        "FIGHTS": details["FIGHTS"],
    }


@app.get("/last")
def get_last_event():
    events = _parse_events()
    last_event = _pick_last_event(events)
    details = _get_event_details(last_event["URL"])

    return {
        "EVENT": last_event["EVENT"],
        "DATE": last_event["DATE_TEXT"],
        "TIMESTAMP": last_event["TIMESTAMP"],
        "LOCATION": last_event["LOCATION"],
        "URL": last_event["URL"],
        "IMAGE": details["IMAGE"],
        "MAIN_EVENT": details["MAIN_EVENT"],
        "FIGHTS": details["FIGHTS"],
    }
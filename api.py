from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
from pathlib import Path
import requests
from bs4 import BeautifulSoup
import re
import json
from datetime import datetime, timedelta
import yaml
import sys
sys.path.append(str(Path(__file__).resolve().parent))
import scrape_ufc_stats_library as LIB

app = FastAPI(title="UFC Stats API")

# Allow your Flutter app (web or mobile) to call this
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten later if you want
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent
PROFILE_CACHE_PATH = BASE_DIR / "fighter_profile_cache.json"
PROFILE_CACHE_TTL = timedelta(hours=24)

def load_csv(name: str) -> pd.DataFrame:
    path = BASE_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"{name} not found. Run the scraper first.")
    return pd.read_csv(path)


def _load_profile_cache() -> dict:
    if not PROFILE_CACHE_PATH.exists():
        return {}
    try:
        with PROFILE_CACHE_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_profile_cache(cache: dict) -> None:
    try:
        with PROFILE_CACHE_PATH.open("w", encoding="utf-8") as f:
            json.dump(cache, f)
    except Exception:
        pass

@app.get("/upcoming")
def get_upcoming_events():
    url = "https://www.ufc.com/events"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/123.0.0.0 Safari/537.36"
        )
    }

    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        raise HTTPException(status_code=500, detail="Failed to fetch UFC.com events")

    soup = BeautifulSoup(response.text, "html.parser")

    # UFC.com upcoming events live inside this container
    event_cards = soup.select(".c-card-event--result")  # works for upcoming & future cards

    events = []
    for card in event_cards:
        title_el = card.select_one(".c-card-event--result__headline")
        date_el = card.select_one(".c-card-event--result__date")
        location_el = card.select_one(".c-card-event--result__location")
        link_el = card.select_one("a")
@app.get("/last")
def get_last_event():
    # 1. Fetch past events
    try:
        df = load_csv("ufc_event_details.csv")
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=str(e))

    if df.empty:
        raise HTTPException(status_code=404, detail="No past events found")

    # 2. Find the most recent past event
    df["DATE"] = pd.to_datetime(df["DATE"], errors="coerce")
    df = df.dropna(subset=["DATE"])
    df = df.sort_values("DATE", ascending=False)
    last_event = df.iloc[0]

    event_url = last_event["URL"]
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/123.0.0.0 Safari/537.36"
        )
    }

    # 3. Scrape event page
    response = requests.get(event_url, headers=headers)
    if response.status_code != 200:
        raise HTTPException(status_code=500, detail="Failed to fetch event details")

    soup = BeautifulSoup(response.text, "html.parser")
    fight_rows = soup.select(".c-listing-fight__content")

    def _slug_to_name(slug: str) -> str:
        return " ".join([p.capitalize() for p in slug.replace("_", "-").split("-") if p])

    def _parse_fighter_corner(corner):
        if not corner:
            return None, None
        link = corner.select_one("a")
        img = corner.select_one("img")
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
        return name or "Unknown", img_url

    fights = []
    main_red_name = main_blue_name = None
    main_red_img = main_blue_img = None

    for i, row in enumerate(fight_rows):
        red_corner = row.select_one(".c-listing-fight__corner--red")
        blue_corner = row.select_one(".c-listing-fight__corner--blue")
        red_name, red_img = _parse_fighter_corner(red_corner)
        blue_name, blue_img = _parse_fighter_corner(blue_corner)
        if i == 0:
            main_red_name, main_red_img = red_name, red_img
            main_blue_name, main_blue_img = blue_name, blue_img
        weight = row.select_one(".c-listing-fight__class-text")
        fight_title = ""
        if red_name and blue_name and red_name != "Unknown" and blue_name != "Unknown":
            fight_title = f"{red_name} vs {blue_name}"
        elif red_name and red_name != "Unknown":
            fight_title = red_name
        elif blue_name and blue_name != "Unknown":
            fight_title = blue_name
        else:
            fight_title = "Unknown Fight"
        red_profile = {}
        blue_profile = {}
        if red_name and red_name != "Unknown":
            try:
                profile = get_fighter_profile(red_name)
                if profile:
                    red_profile = profile
            except Exception as e:
                print("Error fetching profile for", red_name, ":", e)
        if blue_name and blue_name != "Unknown":
            try:
                profile = get_fighter_profile(blue_name)
                if profile:
                    blue_profile = profile
            except Exception as e:
                print("Error fetching profile for", blue_name, ":", e)
        fights.append({
            "FIGHT": fight_title,
            "WEIGHT_CLASS": weight.get_text(strip=True) if weight else "",
            "RED_IMG": red_img,
            "BLUE_IMG": blue_img,
            "RED_PROFILE": red_profile,
            "BLUE_PROFILE": blue_profile,
        })

    fighter_a = main_red_name or "Unknown"
    fighter_b = main_blue_name or "Unknown"
    fighter_a_profile = {}
    fighter_b_profile = {}
    try:
        if fighter_a != "Unknown":
            profile = get_fighter_profile(fighter_a)
            if profile:
                fighter_a_profile = profile
    except Exception as e:
        print("Error fetching profile for", fighter_a, ":", e)
    try:
        if fighter_b != "Unknown":
            profile = get_fighter_profile(fighter_b)
            if profile:
                fighter_b_profile = profile
    except Exception as e:
        print("Error fetching profile for", fighter_b, ":", e)

    def _fallback_image(primary, profile):
        if primary and primary != FALLBACK_IMG:
            return primary
        if isinstance(profile, dict):
            return profile.get("image")
        return FALLBACK_IMG

    fighter_a_img = _fallback_image(main_red_img, fighter_a_profile)
    fighter_b_img = _fallback_image(main_blue_img, fighter_b_profile)

    return {
        "EVENT": last_event["EVENT"],
        "DATE": last_event["DATE"].strftime("%Y-%m-%d") if hasattr(last_event["DATE"], "strftime") else str(last_event["DATE"]),
        "LOCATION": last_event["LOCATION"],
        "URL": last_event["URL"],
        "IMAGE": last_event["IMAGE"],
        "MAIN_EVENT_FIGHTERS": {
            "A": fighter_a,
            "B": fighter_b,
            "A_IMG": fighter_a_img,
            "B_IMG": fighter_b_img,
            "A_PROFILE": fighter_a_profile,
            "B_PROFILE": fighter_b_profile,
        },
        "FIGHTS": fights
    }

FALLBACK_IMG = "https://i.imgur.com/0X4vFQy.png"  # high‑contrast fallback

@app.get("/next")
def get_next_event():
    # 1. Fetch upcoming events
    upcoming = get_upcoming_events()
    if not upcoming:
        raise HTTPException(status_code=404, detail="No upcoming events found")

    next_event = upcoming[0]
    event_url = next_event["URL"]

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/123.0.0.0 Safari/537.36"
        )
    }

    # 2. Scrape event page
    response = requests.get(event_url, headers=headers)
    if response.status_code != 200:
        raise HTTPException(status_code=500, detail="Failed to fetch event details")

    soup = BeautifulSoup(response.text, "html.parser")
    fight_rows = soup.select(".c-listing-fight__content")

    def _slug_to_name(slug: str) -> str:
        # Convert URL slugs like "max-holloway" to "Max Holloway"
        return " ".join([p.capitalize() for p in slug.replace("_", "-").split("-") if p])

    def _parse_fighter_corner(corner):
        if not corner:
            return None, None

        link = corner.select_one("a")
        img = corner.select_one("img")

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
        return name or "Unknown", img_url

    fights = []
    main_red_name = main_blue_name = None
    main_red_img = main_blue_img = None

    for i, row in enumerate(fight_rows):
        red_corner = row.select_one(".c-listing-fight__corner--red")
        blue_corner = row.select_one(".c-listing-fight__corner--blue")

        red_name, red_img = _parse_fighter_corner(red_corner)
        blue_name, blue_img = _parse_fighter_corner(blue_corner)

        if i == 0:
            main_red_name, main_red_img = red_name, red_img
            main_blue_name, main_blue_img = blue_name, blue_img

        weight = row.select_one(".c-listing-fight__class-text")
        fight_title = ""
        if red_name and blue_name and red_name != "Unknown" and blue_name != "Unknown":
            fight_title = f"{red_name} vs {blue_name}"
        elif red_name and red_name != "Unknown":
            fight_title = red_name
        elif blue_name and blue_name != "Unknown":
            fight_title = blue_name
        else:
            fight_title = "Unknown Fight"

        # Fetch fighter profiles (cached) to provide decision-making data
        red_profile = {}
        blue_profile = {}

        if red_name and red_name != "Unknown":
            try:
                profile = get_fighter_profile(red_name)
                if profile:
                    red_profile = profile
            except Exception as e:
                print("Error fetching profile for", red_name, ":", e)

        if blue_name and blue_name != "Unknown":
            try:
                profile = get_fighter_profile(blue_name)
                if profile:
                    blue_profile = profile
            except Exception as e:
                print("Error fetching profile for", blue_name, ":", e)

        fights.append({
            "FIGHT": fight_title,
            "WEIGHT_CLASS": weight.get_text(strip=True) if weight else "",
            "RED_IMG": red_img,
            "BLUE_IMG": blue_img,
            "RED_PROFILE": red_profile,
            "BLUE_PROFILE": blue_profile,
        })

    # 3. Extract main event fighters (bulletproof)
    fighter_a = main_red_name or "Unknown"
    fighter_b = main_blue_name or "Unknown"

    # 4. Fetch fighter profiles for additional data
    fighter_a_profile = {}
    fighter_b_profile = {}

    try:
        if fighter_a != "Unknown":
            profile = get_fighter_profile(fighter_a)
            if profile:
                fighter_a_profile = profile
    except Exception as e:
        print("Error fetching profile for", fighter_a, ":", e)

    try:
        if fighter_b != "Unknown":
            profile = get_fighter_profile(fighter_b)
            if profile:
                fighter_b_profile = profile
    except Exception as e:
        print("Error fetching profile for", fighter_b, ":", e)

    def _fallback_image(primary, profile):
        if primary and primary != FALLBACK_IMG:
            return primary
        if isinstance(profile, dict):
            return profile.get("image")
        return FALLBACK_IMG

    fighter_a_img = _fallback_image(main_red_img, fighter_a_profile)
    fighter_b_img = _fallback_image(main_blue_img, fighter_b_profile)

    # 5. Return combined event + fighters + images + fight card + stats
    return {
        "EVENT": next_event["EVENT"],
        "DATE": next_event["DATE"],
        "LOCATION": next_event["LOCATION"],
        "URL": next_event["URL"],
        "IMAGE": next_event["IMAGE"],
        "MAIN_EVENT_FIGHTERS": {
            "A": fighter_a,
            "B": fighter_b,
            "A_IMG": fighter_a_img,
            "B_IMG": fighter_b_img,
            "A_PROFILE": fighter_a_profile,
            "B_PROFILE": fighter_b_profile,
        },
        "FIGHTS": fights
    }

@app.get("/past")
def get_past_events():
    try:
        df = load_csv("ufc_event_details.csv")
        return df.to_dict(orient="records")
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=str(e))

def _fighter_slug_candidates(name: str):
    base = name.lower().replace(" ", "-")
    return [
        base,
        f"{base}-0",
        f"{base}-1",
        f"{base}-2",
    ]


def get_fighter_profile(name: str):
    """Fetch athlete profile data (image + bio/stats) from UFC.com.

    This function uses a simple file-backed cache to avoid hitting the UFC site too
    often. Cached profiles are stored in `fighter_profile_cache.json` for up to
    `PROFILE_CACHE_TTL`.
    """

    cache = _load_profile_cache()
    slug_key = name.lower().replace(" ", "-")
    now = datetime.utcnow()

    if slug_key in cache:
        entry = cache[slug_key]
        try:
            fetched_at = datetime.fromisoformat(entry.get("fetched_at"))
            if now - fetched_at < PROFILE_CACHE_TTL:
                return entry.get("profile")
        except Exception:
            pass

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/123.0.0.0 Safari/537.36"
        )
    }

    profile = None
    for slug in _fighter_slug_candidates(name):
        url = f"https://www.ufc.com/athlete/{slug}"
        try:
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code != 200:
                continue

            soup = BeautifulSoup(response.text, "html.parser")

            # Image
            img = soup.select_one(".hero-profile__image img")
            image_url = img.get("src") if img and img.get("src") else None

            # Bio fields (height, weight, reach, etc.)
            bio = {}
            for field in soup.select(".c-bio__field"):
                label = field.select_one(".c-bio__label")
                value = field.select_one(".c-bio__value")
                if label and value:
                    bio[label.get_text(strip=True)] = value.get_text(strip=True)
            record_el = None
            for field in soup.select(".c-bio__field"):
                if field.get_text(strip=True).lower().startswith("record"):
                    record_el = field
                    break

            # 3) If still not found, search for any text node containing "Record" and nearby numeric pattern.
            if not record_el:
                record_el = soup.find(string=re.compile(r"\bRecord\b", re.I))

            if record_el:
                text = record_el.get_text(strip=True) if hasattr(record_el, "get_text") else str(record_el)
                m = re.search(r"(\d+-\d+(?:-\d+)?)", text)
                if m:
                    record = m.group(1)
                else:
                    next_num = record_el.find_next(string=re.compile(r"\d+-\d+(?:-\d+)?"))
                    if next_num:
                        record = next_num.strip()

            profile = {
                "slug": slug,
                "image": image_url,
                "bio": bio,
                "record": record,
            }
            break

        except Exception as e:
            print("Error fetching fighter profile", slug, ":", e)
            continue

    cache[slug_key] = {
        "fetched_at": now.isoformat(),
        "profile": profile,
    }
    _save_profile_cache(cache)

    return profile


@app.get("/events")
def get_all_events():
    past = get_past_events()
    upcoming = get_upcoming_events()
    return upcoming + past

@app.get("/fighters")
def get_fighters():
    try:
        df = load_csv("ufc_fighter_details.csv")
        return df.to_dict(orient="records")
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/fights")
def get_fights():
    try:
        df = load_csv("ufc_fight_details.csv")
        return df.to_dict(orient="records")
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/stats")
def get_stats():
    try:
        df = load_csv("ufc_fight_statistics.csv")
        return df.to_dict(orient="records")
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/previous")
def get_previous_event():
    # Load config to get completed_events_all_url
    config_path = BASE_DIR / "scrape_ufc_stats_config.yaml"
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)
    completed_events_url = config["completed_events_all_url"]

    # Scrape all completed events
    soup = LIB.get_soup(completed_events_url)
    df = LIB.parse_event_details(soup)
    if df.empty:
        raise HTTPException(status_code=404, detail="No past events found")
    last_event = df.iloc[0].to_dict()  # Assumes most recent is first
    event_url = last_event.get("URL")
    if not event_url:
        raise HTTPException(status_code=500, detail="No event URL found for last event")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/123.0.0.0 Safari/537.36"
        )
    }

    # Scrape event page for fight card
    response = requests.get(event_url, headers=headers)
    if response.status_code != 200:
        raise HTTPException(status_code=500, detail="Failed to fetch event details")
    soup = BeautifulSoup(response.text, "html.parser")
    fight_rows = soup.select(".c-listing-fight__content")

    def _slug_to_name(slug: str) -> str:
        if not slug or slug == "unknown":
            return "Unknown"
        return " ".join([p.capitalize() for p in slug.replace("_", "-").split("-") if p])

    def _parse_fighter_corner(corner):
        if not corner:
            return None, None
        link = corner.select_one("a")
        img = corner.select_one("img")
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
        return name or "Unknown", img_url

    fights = []
    main_red_name = main_blue_name = None
    main_red_img = main_blue_img = None

    for i, row in enumerate(fight_rows):
        red_corner = row.select_one(".c-listing-fight__corner--red")
        blue_corner = row.select_one(".c-listing-fight__corner--blue")
        red_name, red_img = _parse_fighter_corner(red_corner)
        blue_name, blue_img = _parse_fighter_corner(blue_corner)
        if i == 0:
            main_red_name, main_red_img = red_name, red_img
            main_blue_name, main_blue_img = blue_name, blue_img
        weight = row.select_one(".c-listing-fight__class-text")
        fight_title = ""
        if red_name and blue_name and red_name != "Unknown" and blue_name != "Unknown":
            fight_title = f"{red_name} vs {blue_name}"
        elif red_name and red_name != "Unknown":
            fight_title = red_name
        elif blue_name and blue_name != "Unknown":
            fight_title = blue_name
        else:
            fight_title = "Unknown Fight"

        # Fetch fighter profiles (cached) to provide decision-making data
        red_profile = {}
        blue_profile = {}

        if red_name and red_name != "Unknown":
            try:
                profile = get_fighter_profile(red_name)
                if profile:
                    red_profile = profile
            except Exception as e:
                print("Error fetching profile for", red_name, ":", e)

        if blue_name and blue_name != "Unknown":
            try:
                profile = get_fighter_profile(blue_name)
                if profile:
                    blue_profile = profile
            except Exception as e:
                print("Error fetching profile for", blue_name, ":", e)

        fights.append({
            "FIGHT": fight_title,
            "WEIGHT_CLASS": weight.get_text(strip=True) if weight else "",
            "RED_IMG": red_img,
            "BLUE_IMG": blue_img,
            "RED_PROFILE": red_profile,
            "BLUE_PROFILE": blue_profile,
        })

    # 3. Extract main event fighters (bulletproof)
    fighter_a = main_red_name or "Unknown"
    fighter_b = main_blue_name or "Unknown"

    # 4. Fetch fighter profiles for additional data
    fighter_a_profile = {}
    fighter_b_profile = {}

    try:
        if fighter_a != "Unknown":
            profile = get_fighter_profile(fighter_a)
            if profile:
                fighter_a_profile = profile
    except Exception as e:
        print("Error fetching profile for", fighter_a, ":", e)

    try:
        if fighter_b != "Unknown":
            profile = get_fighter_profile(fighter_b)
            if profile:
                fighter_b_profile = profile
    except Exception as e:
        print("Error fetching profile for", fighter_b, ":", e)

    def _fallback_image(primary, profile):
        if primary and primary != FALLBACK_IMG:
            return primary
        if isinstance(profile, dict):
            return profile.get("image")
        return FALLBACK_IMG

    fighter_a_img = _fallback_image(main_red_img, fighter_a_profile)
    fighter_b_img = _fallback_image(main_blue_img, fighter_b_profile)

    # 5. Return combined event + fighters + images + fight card + stats
    return {
        "EVENT": last_event["EVENT"],
        "DATE": last_event["DATE"],
        "LOCATION": last_event["LOCATION"],
        "URL": last_event["URL"],
        "IMAGE": last_event.get("IMAGE", FALLBACK_IMG),
        "MAIN_EVENT_FIGHTERS": {
            "A": fighter_a,
            "B": fighter_b,
            "A_IMG": fighter_a_img,
            "B_IMG": fighter_b_img,
            "A_PROFILE": fighter_a_profile,
            "B_PROFILE": fighter_b_profile,
        },
        "FIGHTS": fights
    }
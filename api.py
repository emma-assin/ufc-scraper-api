from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
from pathlib import Path
import requests
from bs4 import BeautifulSoup
import re

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

def load_csv(name: str) -> pd.DataFrame:
    path = BASE_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"{name} not found. Run the scraper first.")
    return pd.read_csv(path)

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
        img_el = card.select_one("img")

        if not title_el:
            continue

        events.append({
            "EVENT": title_el.get_text(strip=True),
            "DATE": date_el.get_text(strip=True) if date_el else "",
            "LOCATION": location_el.get_text(strip=True) if location_el else "",
            "URL": "https://www.ufc.com" + link_el["href"] if link_el else "",
            "IMAGE": img_el["src"] if img_el else "",
        })

    return events

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

        if not name and img and img.get("alt"):
            name = img.get("alt").strip()

        img_url = img.get("src") if img and img.get("src") else None
        return name, img_url

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
        if red_name and blue_name:
            fight_title = f"{red_name} vs {blue_name}"
        elif red_name:
            fight_title = red_name
        elif blue_name:
            fight_title = blue_name

        fights.append({
            "FIGHT": fight_title,
            "WEIGHT_CLASS": weight.get_text(strip=True) if weight else "",
            "RED_IMG": red_img,
            "BLUE_IMG": blue_img,
        })

    # 3. Extract main event fighters (bulletproof)
    fighter_a = main_red_name or "Unknown"
    fighter_b = main_blue_name or "Unknown"
    fighter_a_img = main_red_img
    fighter_b_img = main_blue_img

    # 4. Fetch fighter images safely with fallback
    if not fighter_a_img and fighter_a != "Unknown":
        try:
            fighter_a_img = get_fighter_image(fighter_a)
        except Exception:
            fighter_a_img = None

    if not fighter_b_img and fighter_b != "Unknown":
        try:
            fighter_b_img = get_fighter_image(fighter_b)
        except Exception:
            fighter_b_img = None

    if not fighter_a_img:
        fighter_a_img = FALLBACK_IMG
    if not fighter_b_img:
        fighter_b_img = FALLBACK_IMG

    # 5. Return combined event + fighters + images + fight card
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
            "B_IMG": fighter_b_img
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

def get_fighter_image(name):
    base = name.lower().replace(" ", "-")

    # Try multiple possible slugs
    candidates = [
        base,
        f"{base}-0",
        f"{base}-1",
        f"{base}-2",
    ]

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/123.0.0.0 Safari/537.36"
        )
    }

    for slug in candidates:
        url = f"https://www.ufc.com/athlete/{slug}"
        try:
            print("Trying slug:", slug)
            response = requests.get(url, headers=headers, timeout=10)

            if response.status_code != 200:
                continue

            soup = BeautifulSoup(response.text, "html.parser")
            img = soup.select_one(".hero-profile__image img")

            if img and img.get("src"):
                print("Found image:", img["src"])
                return img["src"]

        except Exception as e:
            print("Error fetching slug", slug, ":", e)
            continue

    print("No image found for:", name)
    return None


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
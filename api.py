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

    fights = []
    for row in fight_rows:
        fighters = row.select(".c-listing-fight__headline")
        weight = row.select_one(".c-listing-fight__class")

        fight_title = fighters[0].get_text(strip=True) if fighters else ""

        fights.append({
            "FIGHT": fight_title,
            "WEIGHT_CLASS": weight.get_text(strip=True) if weight else ""
        })

    # 3. Extract main event fighters (bulletproof)
    main_event = fights[0]["FIGHT"]

    # Normalize all possible separators
    clean = re.sub(r'(vs\.?|v\.?)', 'vs', main_event, flags=re.IGNORECASE)
    clean = clean.replace("\u00A0", " ")  # remove non‑breaking spaces

    parts = [p.strip() for p in clean.split("vs") if p.strip()]

    if len(parts) != 2:
        print("ERROR: Could not split main event:", main_event)
        fighter_a = fighter_b = "Unknown"
    else:
        fighter_a, fighter_b = parts

    # 4. Fetch fighter images safely with fallback
    try:
        fighter_a_img = get_fighter_image(fighter_a)
        if not fighter_a_img:
            fighter_a_img = FALLBACK_IMG
    except Exception as e:
        print("Error fetching image for", fighter_a, ":", e)
        fighter_a_img = FALLBACK_IMG

    try:
        fighter_b_img = get_fighter_image(fighter_b)
        if not fighter_b_img:
            fighter_b_img = FALLBACK_IMG
    except Exception as e:
        print("Error fetching image for", fighter_b, ":", e)
        fighter_b_img = FALLBACK_IMG

    print("MAIN EVENT:", fighter_a, "vs", fighter_b)
    print("A IMG:", fighter_a_img)
    print("B IMG:", fighter_b_img)

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
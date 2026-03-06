from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
from pathlib import Path
import requests
from bs4 import BeautifulSoup

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

@app.get("/next")
def get_next_event():
    # 1. Fetch upcoming events (from your cached UFC.com scraper)
    upcoming = get_upcoming_events()
    if not upcoming:
        raise HTTPException(status_code=404, detail="No upcoming events found")

    next_event = upcoming[0]  # first event is the next one
    event_url = next_event["URL"]

    # 2. Scrape the event page for fight card
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/123.0.0.0 Safari/537.36"
        )
    }

    response = requests.get(event_url, headers=headers)
    if response.status_code != 200:
        raise HTTPException(status_code=500, detail="Failed to fetch event details")

    soup = BeautifulSoup(response.text, "html.parser")

    # UFC.com fight card selector
    fight_rows = soup.select(".c-listing-fight__content")

    fights = []
    for row in fight_rows:
        fighters = row.select(".c-listing-fight__headline")
        weight = row.select_one(".c-listing-fight__class")

        if len(fighters) >= 1:
            fight_title = fighters[0].get_text(strip=True)
        else:
            fight_title = ""

        fights.append({
            "FIGHT": fight_title,
            "WEIGHT_CLASS": weight.get_text(strip=True) if weight else ""
        })

    # 3. Return combined event + fights
    return {
        "EVENT": next_event["EVENT"],
        "DATE": next_event["DATE"],
        "LOCATION": next_event["LOCATION"],
        "URL": next_event["URL"],
        "IMAGE": next_event["IMAGE"],
        "FIGHTS": fights
    }


@app.get("/past")
def get_past_events():
    try:
        df = load_csv("ufc_event_details.csv")
        return df.to_dict(orient="records")
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=str(e))

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
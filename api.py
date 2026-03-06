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
    url = "http://ufcstats.com/statistics/events/upcoming"
    response = requests.get(url)

    if response.status_code != 200:
        raise HTTPException(status_code=500, detail="Failed to fetch upcoming events")

    soup = BeautifulSoup(response.text, "html.parser")

    # More flexible table selector
    table = soup.find("table", class_="b-statistics__table-events")
    if not table:
        table = soup.find("table")  # fallback

    if not table:
        raise HTTPException(status_code=500, detail="Upcoming events table not found")

    rows = table.find_all("tr")[1:]  # skip header

    events = []
    for row in rows:
        cols = row.find_all("td")
        if len(cols) < 3:
            continue

        event_name = cols[0].text.strip()
        event_url = cols[0].find("a")["href"]
        date = cols[1].text.strip()
        location = cols[2].text.strip()

        events.append({
            "EVENT": event_name,
            "URL": event_url,
            "DATE": date,
            "LOCATION": location
        })

    return events


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
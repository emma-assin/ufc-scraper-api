from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from typing import List, Dict, Any, Optional

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


def _parse_event_date(text: str) -> Optional[datetime]:
    if not text:
        return None
    # UFC.com dates are like "Sat, Mar 7 / 10:00 PM EST"
    # We only care about the date part before the slash
    try:
        date_part = text.split("/")[0].strip()
        # Remove weekday if present
        if "," in date_part:
            _, rest = date_part.split(",", 1)
            date_part = rest.strip()
        return datetime.strptime(date_part, "%b %d")
    except Exception:
        return None


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

        date_text = date_el.get_text(strip=True) if date_el else ""
        loc_text = loc_el.get_text(strip=True) if loc_el else ""

        parsed_date = _parse_event_date(date_text)
        # Attach year heuristically: UFC.com omits year sometimes; assume current or previous
        if parsed_date:
            now = datetime.now()
            parsed_date = parsed_date.replace(year=now.year)
            # If this date is in the future but visually looks like past, or vice versa,
            # you can adjust, but for now we keep it simple.

        events.append(
            {
                "EVENT": title,
                "URL": url,
                "DATE_TEXT": date_text,
                "LOCATION": loc_text,
                "DATE": parsed_date,
            }
        )

    # Filter out events with no date
    events = [e for e in events if e["DATE"] is not None]
    return events


def _slug_to_name(slug: str) -> str:
    if not slug or slug == "unknown":
        return "Unknown"
    return " ".join([p.capitalize() for p in slug.replace("_", "-").split("-") if p])


def _parse_fighter_corner(corner) -> (str, str):
    if not corner:
        return "Unknown", FALLBACK_IMG

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


def _get_event_details(event_url: str) -> Dict[str, Any]:
    soup = _get_soup(event_url)

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

        red_name, red_img = _parse_fighter_corner(red_corner)
        blue_name, blue_img = _parse_fighter_corner(blue_corner)

        # Determine winner
        # Determine winner using UFC.com's new markup
        winner = None

        outcome_red = row.select_one(".c-listing-fight__corner--red .c-listing-fight__outcome--win")
        outcome_blue = row.select_one(".c-listing-fight__corner--blue .c-listing-fight__outcome--win")

        if outcome_red:
            winner = red_name
        elif outcome_blue:
            winner = blue_name

        # Method / Round / Time
        method = round_num = time = ""
        result_box = row.select_one(".c-listing-fight__result-text")
        if result_box:
            spans = [s.get_text(strip=True) for s in result_box.select("span")]
            for s in spans:
                if "Round" in s:
                    round_num = s.replace("Round:", "").strip()
                elif "Time" in s:
                    time = s.replace("Time:", "").strip()
                else:
                    method = s  # KO/TKO, SUB, DEC, etc.

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
    now = datetime.now()
    upcoming = [e for e in events if e["DATE"] >= now]
    if not upcoming:
        raise HTTPException(status_code=404, detail="No upcoming events found")
    upcoming.sort(key=lambda e: e["DATE"])
    return upcoming[0]


def _pick_last_event(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    now = datetime.now()
    past = [e for e in events if e["DATE"] < now]
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
        "LOCATION": last_event["LOCATION"],
        "URL": last_event["URL"],
        "IMAGE": details["IMAGE"],
        "MAIN_EVENT": details["MAIN_EVENT"],
        "FIGHTS": details["FIGHTS"],
    }
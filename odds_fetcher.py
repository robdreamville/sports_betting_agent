# odds_fetcher.py
import os
import json
import requests
import sqlite3
from datetime import datetime

from dotenv import load_dotenv
from database_setup import SoccerDatabase

# Load environment variables
load_dotenv()
API_KEY = os.getenv("ODDS_API_KEY")
BASE_URL = "https://api.the-odds-api.com/v4/sports/{sport}/odds"

# Define the leagues we are interested in
LEAGUES = {
    "soccer_epl": "EPL",
    "soccer_spain_la_liga": "La Liga"
}

def fetch_odds_for_league(league_key: str) -> list:
    """Fetches odds from the API for a given league."""
    url = BASE_URL.format(sport=league_key)
    params = {
        "apiKey": API_KEY,
        "regions": "eu",
        "markets": "h2h",
        "oddsFormat": "american",
        "bookmakers": "unibet,pinnacle"
    }
    
    print(f"Fetching odds for {league_key}...")
    try:
        response = requests.get(url, params=params, timeout=20)
        response.raise_for_status()  # Raises an error for bad responses (4xx or 5xx)
        print(f"Successfully fetched odds for {league_key}.")
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching odds for {league_key}: {e}")
        return []

def process_and_insert_data(all_api_data: list):
    """
    Processes raw API data and inserts it into the matches and odds_history tables.
    """
    db = SoccerDatabase()
    conn = sqlite3.connect(db.db_path)
    
    matches_created = 0
    odds_inserted = 0

    for match_api_data in all_api_data:
        league_name = LEAGUES.get(match_api_data['sport_key'])
        if not league_name:
            continue

        # Get matchday info using the method from our database class
        start_date, end_date, matchday_number = db.detect_matchday_window(match_api_data['commence_time'])
        
        # Ensure the matchday record exists to get its ID
        conn.execute("INSERT OR IGNORE INTO matchdays (matchday_number, league, start_date, end_date) VALUES (?, ?, ?, ?)",
                     (matchday_number, league_name, start_date, end_date))
        matchday_id_row = conn.execute("SELECT id FROM matchdays WHERE matchday_number = ? AND league = ?", 
                                   (matchday_number, league_name)).fetchone()
        if not matchday_id_row:
            continue
        matchday_id = matchday_id_row[0]

        # Insert the core match details if they don't exist
        try:
            cursor = conn.execute("""
                INSERT OR IGNORE INTO matches 
                (matchday_id, match_id, sport_key, home_team, away_team, commence_time)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                matchday_id, match_api_data['id'], match_api_data['sport_key'],
                match_api_data['home_team'], match_api_data['away_team'], match_api_data['commence_time']
            ))
            if cursor.rowcount > 0:
                matches_created += 1
        except sqlite3.IntegrityError:
            pass # Match already exists, which is fine

        # Always insert the latest odds into the history table
        bookmaker = next((b for b in match_api_data.get("bookmakers", []) if any(m['key'] == 'h2h' for m in b.get("markets", []))), None)
        if not bookmaker:
            continue
        
        h2h_market = next((m for m in bookmaker.get("markets", []) if m['key'] == 'h2h'), None)
        if not h2h_market:
            continue

        odds_dict = {}
        for outcome in h2h_market["outcomes"]:
            if outcome["name"] == match_api_data["home_team"]: odds_dict["home"] = outcome["price"]
            elif outcome["name"] == match_api_data["away_team"]: odds_dict["away"] = outcome["price"]
            else: odds_dict["draw"] = outcome["price"]
        
        try:
            cursor = conn.execute("""
                INSERT INTO odds_history
                (match_id, bookmaker, odds_home, odds_away, odds_draw, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                match_api_data['id'], bookmaker['title'], odds_dict.get('home'),
                odds_dict.get('away'), odds_dict.get('draw'), datetime.utcnow().isoformat()
            ))
            if cursor.rowcount > 0:
                odds_inserted += 1
        except Exception as e:
            print(f"Error inserting odds history for {match_api_data['id']}: {e}")

    conn.commit()
    conn.close()
    print(f"Database update complete. New matches created: {matches_created}. New odds records inserted: {odds_inserted}.")

def run_pipeline():
    """Main function to run the fetch-and-load pipeline."""
    print("--- Starting Odds Fetching Pipeline ---")
    
    all_league_data = []
    for league_key in LEAGUES.keys():
        api_data = fetch_odds_for_league(league_key)
        if api_data:
            all_league_data.extend(api_data)
    
    if all_league_data:
        process_and_insert_data(all_league_data)
    else:
        print("No data fetched from API. Exiting.")
    
    print("--- Odds Fetching Pipeline Finished ---")

if __name__ == "__main__":
    run_pipeline()

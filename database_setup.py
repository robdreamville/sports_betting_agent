# database_setup.py
import sqlite3
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Tuple
import hashlib
import pytz  # Add this import at the top of your file

class SoccerDatabase:
    def __init__(self, db_path: str = "soccer_analysis.db"):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        """Initialize database with all required tables"""
        conn = sqlite3.connect(self.db_path)
        
        # Create tables
        conn.executescript("""
            -- Matchdays table: Groups matches by week
            CREATE TABLE IF NOT EXISTS matchdays (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                matchday_number INTEGER NOT NULL,
                league TEXT NOT NULL, -- 'EPL' or 'La Liga'
                start_date TEXT NOT NULL, -- ISO format
                end_date TEXT NOT NULL,
                total_matches INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(matchday_number, league)
            );
            
            -- Matches table: Core match data (odds removed)
            CREATE TABLE IF NOT EXISTS matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                matchday_id INTEGER,
                match_id TEXT UNIQUE NOT NULL, -- From odds API
                sport_key TEXT NOT NULL,
                home_team TEXT NOT NULL,
                away_team TEXT NOT NULL,
                commence_time TEXT NOT NULL, -- ISO format
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (matchday_id) REFERENCES matchdays (id)
            );

            -- New table to track odds history
            CREATE TABLE IF NOT EXISTS odds_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id TEXT NOT NULL,
                bookmaker TEXT,
                odds_home REAL,
                odds_away REAL,
                odds_draw REAL,
                fetched_at TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (match_id) REFERENCES matches (match_id)
            );
            
            -- Match analysis: AI-generated predictions and insights
            CREATE TABLE IF NOT EXISTS match_analysis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id TEXT NOT NULL,
                prediction TEXT NOT NULL, -- 'home_win', 'away_win', 'draw'
                prediction_text TEXT, -- "Tottenham Win"
                edge_reason TEXT NOT NULL, -- Main betting edge found
                key_factors TEXT, -- JSON array of key insights
                confidence TEXT, -- 'High', 'Medium', 'Low'
                raw_search_data TEXT, -- Full search results for reference
                telegram_sent BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (match_id) REFERENCES matches (match_id)
            );
            
            -- Search cache: Avoid redundant API calls
            CREATE TABLE IF NOT EXISTS search_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_hash TEXT UNIQUE NOT NULL,
                query_text TEXT NOT NULL,
                teams TEXT, -- JSON array of team names involved
                search_results TEXT NOT NULL, -- Cached search response
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL
            );
            
            -- Analysis runs: Track when analysis was performed
            CREATE TABLE IF NOT EXISTS analysis_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                matchday_ids TEXT, -- JSON array of processed matchday IDs
                total_matches INTEGER,
                successful_analyses INTEGER,
                failed_analyses INTEGER,
                telegram_messages_sent INTEGER,
                api_calls_made INTEGER,
                cache_hits INTEGER,
                run_duration_seconds REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        # Create indexes for better performance
        conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_matches_commence_time ON matches(commence_time);
            CREATE INDEX IF NOT EXISTS idx_matches_matchday ON matches(matchday_id);
            CREATE INDEX IF NOT EXISTS idx_search_cache_expires ON search_cache(expires_at);
            CREATE INDEX IF NOT EXISTS idx_analysis_match ON match_analysis(match_id);
            CREATE INDEX IF NOT EXISTS idx_odds_history_match ON odds_history(match_id);
        """)
        
        conn.commit()
        conn.close()
        print("Database schema updated successfully for odds history.")
    
    def detect_matchday_window(self, commence_time: str) -> Tuple[str, str, int]:
        """
        Given a match start time, determine the matchday window (Fri-Mon)
        Returns (start_date, end_date, matchday_number) in ISO format
        """
        match_dt = datetime.fromisoformat(commence_time.replace('Z', '+00:00'))
        
        # Define season start (first Friday on or after Aug 15, 2025), UTC timezone
        season_start = datetime(2025, 8, 15, tzinfo=pytz.UTC)
        if season_start.weekday() != 4:  # If not Friday, find next Friday
            days_to_friday = (4 - season_start.weekday()) % 7
            season_start = season_start + timedelta(days=days_to_friday)
        
        # Calculate weeks since season start
        days_since_start = (match_dt - season_start).days
        weeks_since_start = days_since_start // 7
        
        # If match is before season start, warn and assign to Matchday 1
        if days_since_start < 0:
            print(f"   ⚠️  Warning: commence_time {commence_time} is before season start ({season_start.isoformat()}), assigning to Matchday 1")
            weeks_since_start = 0
        
        matchday_number = weeks_since_start + 1
        
        # Calculate the Friday of the match's week
        matchday_friday = season_start + timedelta(weeks=weeks_since_start)
        start_date = matchday_friday.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = (matchday_friday + timedelta(days=4)).replace(hour=23, minute=59, second=59, microsecond=999999)
        
        # Debug: Log the commence_time and calculated window
        print(f"   🕒 Processing commence_time {commence_time} -> Window {start_date.isoformat()} to {end_date.isoformat()} (Matchday {matchday_number})")
        
        return start_date.isoformat(), end_date.isoformat(), matchday_number
    
    def migrate_json_data(self, epl_file: str = "odds_epl.json", laliga_file: str = "odds_laliga.json"):
        """Migrate data from JSON files to database, grouping matches by matchday window"""
        conn = sqlite3.connect(self.db_path)
        
        # Process each league
        leagues_data = [
            ("EPL", epl_file, "soccer_epl"),
            ("La Liga", laliga_file, "soccer_spain_la_liga")
        ]
        
        # Collect all matches across leagues
        all_matches = []
        for league_name, filename, sport_key in leagues_data:
            if not Path(filename).exists():
                print(f"⚠️  {filename} not found, skipping {league_name}")
                continue
            
            print(f"📥 Processing {league_name} data from {filename}")
            with open(filename, 'r') as f:
                matches_data = json.load(f)
            
            if not matches_data:
                print(f"⚠️  No data in {filename}")
                continue
            
            for match_data in matches_data:
                match_data['league'] = league_name
                match_data['sport_key'] = sport_key
                all_matches.append(match_data)
        
        if not all_matches:
            print("⚠️  No matches found in any JSON files")
            conn.close()
            return
        
        # Group matches by matchday window
        matchday_groups = {}
        
        for match_data in all_matches:
            commence_time = match_data['commence_time']
            start_date, end_date, matchday_num = self.detect_matchday_window(commence_time)
            
            # Create a unique key for the matchday window
            window_key = (start_date, end_date)
            
            if window_key not in matchday_groups:
                matchday_groups[window_key] = {
                    'matchday_number': matchday_num,
                    'start_date': start_date,
                    'end_date': end_date,
                    'matches': [],
                    'leagues': set()
                }
            
            matchday_groups[window_key]['matches'].append(match_data)
            matchday_groups[window_key]['leagues'].add(match_data['league'])
        
        # Insert matchdays and matches into the database
        for window_key, group in matchday_groups.items():
            start_date, end_date = window_key
            matches = group['matches']
            leagues = group['leagues']
            
            # Insert one matchday per league in this window
            for league in leagues:
                # Filter matches for this league
                league_matches = [m for m in matches if m['league'] == league]
                
                # Insert matchday
                cursor = conn.execute("""
                    INSERT OR REPLACE INTO matchdays 
                    (matchday_number, league, start_date, end_date, total_matches)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    group['matchday_number'],
                    league,
                    start_date,
                    end_date,
                    len(league_matches)
                ))
                
                # Get matchday ID
                matchday_id = conn.execute("""
                    SELECT id FROM matchdays 
                    WHERE matchday_number = ? AND league = ?
                """, (group['matchday_number'], league)).fetchone()[0]
                
                # Insert matches
                matches_inserted = 0
                for match_data in league_matches:
                    try:
                        home_team = match_data['teams'][0]
                        away_team = match_data['teams'][1]
                        odds = match_data.get('odds', {})
                        
                        cursor = conn.execute("""
                            INSERT OR REPLACE INTO matches
                            (matchday_id, match_id, sport_key, home_team, away_team, 
                            commence_time, bookmaker, odds_home, odds_away, odds_draw, fetched_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            matchday_id,
                            match_data['match_id'],
                            match_data['sport_key'],
                            home_team,
                            away_team,
                            match_data['commence_time'],
                            match_data.get('bookmaker', 'Unknown'),
                            odds.get('home'),
                            odds.get('away'), 
                            odds.get('draw'),
                            match_data.get('fetched_at')
                        ))
                        
                        matches_inserted += 1
                        
                    except Exception as e:
                        print(f"❌ Error inserting match {match_data.get('match_id')}: {str(e)}")
                
                print(f"   ✅ {league} Matchday {group['matchday_number']}: {matches_inserted} matches inserted ({start_date[:10]} - {end_date[:10]})")
        
        conn.commit()
        conn.close()
        
        # Print summary
        self.print_database_summary()
    
    def print_database_summary(self):
        """Print a summary of what's in the database"""
        conn = sqlite3.connect(self.db_path)
        
        print("\n" + "="*50)
        print("📊 DATABASE SUMMARY")
        print("="*50)
        
        # Matchdays summary
        matchdays = conn.execute("""
            SELECT league, COUNT(*) as matchday_count, 
                   MIN(start_date) as first_matchday,
                   MAX(end_date) as last_matchday
            FROM matchdays 
            GROUP BY league
        """).fetchall()
        
        for league, count, first, last in matchdays:
            print(f"\n🏆 {league}:")
            print(f"   📅 Matchdays: {count}")
            print(f"   📆 Period: {first[:10]} to {last[:10]}")
            
            # Matches per matchday
            matches_per_md = conn.execute("""
                SELECT matchday_number, COUNT(*) as match_count
                FROM matchdays md
                JOIN matches m ON md.id = m.matchday_id
                WHERE md.league = ?
                GROUP BY matchday_number
                ORDER BY matchday_number
            """, (league,)).fetchall()
            
            print(f"   🎯 Matches per matchday: {', '.join([f'MD{md}: {count}' for md, count in matches_per_md])}")
        
        # Overall stats
        total_matches = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
        total_matchdays = conn.execute("SELECT COUNT(*) FROM matchdays").fetchone()[0]
        
        print(f"\n📈 TOTALS:")
        print(f"   🎮 Total matches: {total_matches}")
        print(f"   📋 Total matchdays: {total_matchdays}")
        
        conn.close()
        print("\n" + "="*50)
    
    def get_next_matchday(self, league: str = None) -> List:
        """
        Get the next upcoming matchday(s) that haven't been fully analyzed.
        If league is None, get across all leagues combined.
        Returns matchday info with UNANALYZED matches grouped by matchday_id.
        """
        conn = sqlite3.connect(self.db_path)
        now = datetime.now().isoformat()

        # First, find upcoming matchdays that are not fully analyzed
        if league:
            query = """
                SELECT md.id, md.matchday_number, md.league, md.start_date, md.end_date,
                    COUNT(m.id) as total_matches,
                    COUNT(ma.id) as analyzed_matches
                FROM matchdays md
                LEFT JOIN matches m ON md.id = m.matchday_id
                LEFT JOIN match_analysis ma ON m.match_id = ma.match_id
                WHERE md.league = ? AND md.end_date > ?
                GROUP BY md.id
                HAVING analyzed_matches < total_matches OR analyzed_matches IS NULL
                ORDER BY md.start_date ASC
                LIMIT 2
            """
            params = (league, now)
            matchdays = conn.execute(query, params).fetchall()
        else:
            query = """
                SELECT md.id, md.matchday_number, md.league, md.start_date, md.end_date,
                    COUNT(m.id) as total_matches,
                    COUNT(ma.id) as analyzed_matches
                FROM matchdays md
                LEFT JOIN matches m ON md.id = m.matchday_id
                LEFT JOIN match_analysis ma ON m.match_id = ma.match_id
                WHERE md.end_date > ?
                GROUP BY md.id
                HAVING analyzed_matches < total_matches OR analyzed_matches IS NULL
                ORDER BY md.start_date ASC
                LIMIT 5
            """
            params = (now,)
            matchdays = conn.execute(query, params).fetchall()

        if not matchdays:
            conn.close()
            return []

        # Collect matchday ids
        matchday_ids = [row[0] for row in matchdays]
        if not matchday_ids:
            conn.close()
            return []

        in_clause = f"({','.join(['?'] * len(matchday_ids))})"

        # Fetch only UNANALYZED matches for these matchdays
        matches_query = f"""
                 SELECT m.match_id, m.home_team, m.away_team, m.commence_time, m.odds_home, m.odds_away, m.odds_draw, m.matchday_id
                 FROM matches m
                 LEFT JOIN match_analysis ma ON m.match_id = ma.match_id
                 WHERE m.matchday_id IN {in_clause} AND m.commence_time > ? AND ma.id IS NULL
                ORDER BY m.commence_time ASC
            """

        query_params = matchday_ids + [now]
        matches = conn.execute(matches_query, query_params).fetchall()
        conn.close()

        # Group matches by matchday id
        matches_by_matchday = {}
        for row in matches:
            md_id = row[7]
            if md_id not in matches_by_matchday:
                matches_by_matchday[md_id] = []
            matches_by_matchday[md_id].append({
                'match_id': row[0],
                'home_team': row[1],
                'away_team': row[2],
                'commence_time': row[3],
                'odds': {
                    'home': row[4],
                    'away': row[5],
                    'draw': row[6]
                }
            })

        # Build return structure with matchday info + grouped matches
        result = []
        for md in matchdays:
            md_id = md[0]
            unanalyzed_matches = matches_by_matchday.get(md_id, [])
            if not unanalyzed_matches:
                continue

            result.append({
                'matchday_id': md_id,
                'matchday_number': md[1],
                'league': md[2],
                'start_date': md[3],
                'end_date': md[4],
                'total_matches': md[5],
                'analyzed_matches': md[6] or 0,
                'remaining_matches': len(unanalyzed_matches),
                'matches': unanalyzed_matches
            })

        return result


    def get_odds_history_for_match(self, match_id: str) -> List[Dict]:
        """
        Gets all historical odds for a single match, ordered from newest to oldest.
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        odds_history = []
        try:
            cursor = conn.execute("""
                SELECT bookmaker, odds_home, odds_away, odds_draw, fetched_at
                FROM odds_history
                WHERE match_id = ?
                ORDER BY fetched_at DESC
            """, (match_id,))
            results = cursor.fetchall()
            odds_history = [dict(row) for row in results]
        except sqlite3.Error as e:
            print(f"Database error in get_odds_history_for_match: {e}")
        finally:
            conn.close()
        return odds_history

    def get_unsent_analyses(self) -> List[Dict]:
        """
        Gets all match analyses that have been completed but not yet sent to Telegram.
        Returns a list of dictionaries, each representing a match ready for notification.
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        
        analyses_to_send = []
        
        try:
            cursor = conn.execute("""
                SELECT
                    ma.id as analysis_id,
                    ma.prediction_text,
                    ma.edge_reason,
                    ma.key_factors,
                    m.home_team,
                    m.away_team,
                    m.commence_time,
                    m.match_id
                FROM match_analysis ma
                JOIN matches m ON ma.match_id = m.match_id
                WHERE ma.telegram_sent = 0 OR ma.telegram_sent = FALSE
            """)
            
            results = cursor.fetchall()
            
            for row in results:
                row_dict = dict(row)
                # Reconstruct the objects into the shape the agent expects
                analyses_to_send.append({
                    "current_match_info": {
                        "home_team": row_dict["home_team"],
                        "away_team": row_dict["away_team"],
                        "commence_time": row_dict["commence_time"],
                        "match_id": row_dict["match_id"]
                    },
                    "analysis": {
                        "prediction_text": row_dict["prediction_text"],
                        "edge_reason": row_dict["edge_reason"],
                        # The key_factors are stored as a JSON string, so we load it
                        "key_factors": json.loads(row_dict["key_factors"])
                    }
                })
                
        except sqlite3.Error as e:
            print(f"Database error in get_unsent_analyses: {e}")
        finally:
            conn.close()
            
        return analyses_to_send


def main():
    """Run the migration process"""
    print("🚀 Starting database migration process...")
    
    # Initialize database
    db = SoccerDatabase()
    
    # Migrate JSON data
    print("\n📂 Migrating JSON data to SQLite...")
    db.migrate_json_data()
    
    # Test the next matchday detection
    print("\n🔍 Testing next matchday detection...")
    all_matchdays = db.get_next_matchday()

    if all_matchdays:
        print(f"📅 Found {len(all_matchdays)} upcoming matchday(s) to analyze:")
        for next_matchday in all_matchdays:
            print("\n" + "-"*25)
            print(f"   🏆 {next_matchday['league']} Matchday {next_matchday['matchday_number']}")
            print(f"   📆 {next_matchday['start_date'][:10]} to {next_matchday['end_date'][:10]}")
            print(f"   🎯 {next_matchday['remaining_matches']}/{next_matchday['total_matches']} matches need analysis")

            if next_matchday['matches']:
                print(f"\n   🎮 Upcoming matches:")
                for match in next_matchday['matches'][:5]:  # Show first 5
                    print(f"      • {match['home_team']} vs {match['away_team']} ({match['commence_time'][:16]})")

                if len(next_matchday['matches']) > 5:
                    print(f"      ... and {len(next_matchday['matches']) - 5} more matches")
            else:
                print("   ✅ No upcoming matches for this matchday.")

    else:
        print("✅ No upcoming matchdays found or all are already analyzed!")


if __name__ == "__main__":
    main()
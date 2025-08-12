# reset_flags.py
import sqlite3
import os

def reset_telegram_flags():
    """
    Connects to the database and resets all `telegram_sent` flags
    in the `match_analysis` table to False (0).
    """
    db_path = "soccer_analysis.db"
    if not os.path.exists(db_path):
        print(f"Error: Database file not found at {db_path}")
        return

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        # Set all telegram_sent flags to 0 (False)
        cursor.execute("UPDATE match_analysis SET telegram_sent = 0")
        
        rows_updated = cursor.rowcount
        conn.commit()
        
        print(f"Successfully reset 'telegram_sent' flag for {rows_updated} records.")
        
    except sqlite3.Error as e:
        print(f"An error occurred: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    print("This script will reset all 'telegram_sent' flags in your database.")
    reset_telegram_flags()

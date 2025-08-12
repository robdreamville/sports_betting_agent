# news_agent.py
import os
import json
import sqlite3
from typing import List, TypedDict, Dict, Optional
from datetime import datetime

from dotenv import load_dotenv
from langgraph.graph import StateGraph, END
import google.generativeai as genai

# Import database and tools
from database_setup import SoccerDatabase
from tools import (
    send_telegram_message,
    batch_team_search,
    update_odds_database,
)

# Load environment variables
load_dotenv()

# --- Agent State ---
class AgentState(TypedDict):
    """Represents the state of our analysis agent."""
    notifications_to_send: Optional[List[Dict]]
    matches_to_process: Optional[List[Dict]]
    current_match_info: Optional[Dict]
    current_analysis: Optional[Dict]
    search_results: Optional[str]
    processed_matches: List[Dict]

# --- Agent Helper Functions ---

def _send_formatted_telegram_message(match_info: Dict, analysis: Dict):
    """Helper function to format and send a single telegram message."""
    home_team = match_info['home_team']
    away_team = match_info['away_team']

    try:
        dt_object = datetime.fromisoformat(match_info['commence_time'].replace('Z', ''))
        formatted_time = dt_object.strftime('%b %d, %H:%M')
    except (ValueError, KeyError):
        formatted_time = match_info['commence_time']

    message = (
        f"*{home_team} vs {away_team}* ({formatted_time})\n\n"
        f"*PICK:* {analysis['prediction_text']}\n"
        f"*EDGE:* {analysis['edge_reason']} ⚡\n"
        f"*KEY:* {', '.join(analysis['key_factors'])} "
    )

    print(f"--- SENDING TELEGRAM ---\n{message}")
    result = send_telegram_message.invoke({"message": message})
    print(f"Telegram tool result: {result}")

    if result.strip().startswith("✅"):
        db_path = "soccer_analysis.db"
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("UPDATE match_analysis SET telegram_sent = ? WHERE match_id = ?", (True, match_info['match_id']))
            conn.commit()
            print(f"Marked match {match_info['match_id']} as sent.")
        finally:
            conn.close()
    else:
        print(f"Telegram send failed for match {match_info['match_id']}, will retry on next run.")

# --- Agent Nodes ---

def fetch_latest_odds(state: AgentState) -> AgentState:
    """Node that runs the odds fetching pipeline tool."""
    print("--- PIPELINE START: FETCHING LATEST ODDS ---")
    result = update_odds_database.invoke({})
    print(result)
    return state

def get_work_to_do(state: AgentState) -> AgentState:
    """Gets both unsent notifications and new matches to analyze."""
    print("\n--- STEP 2: GETTING ALL WORK FROM DB ---")
    db = SoccerDatabase()
    
    unsent = db.get_unsent_analyses()
    if unsent:
        print(f"Found {len(unsent)} analysis reports with unsent notifications.")
    
    new_matches = []
    upcoming_matchdays = db.get_next_matchday()
    if upcoming_matchdays:
        for matchday in upcoming_matchdays:
            new_matches.extend(matchday.get('matches', []))
    
    if new_matches:
        print(f"Found {len(new_matches)} new matches to analyze.")

    return {
        **state,
        "notifications_to_send": unsent,
        "matches_to_process": new_matches,
        "processed_matches": []
    }

def process_unsent_notification(state: AgentState) -> AgentState:
    """Pops one unsent notification from the list and sends it."""
    notification_data = state["notifications_to_send"].pop(0)
    _send_formatted_telegram_message(
        match_info=notification_data["current_match_info"],
        analysis=notification_data["analysis"]
    )
    return state

def research_match(state: AgentState) -> AgentState:
    """Researches the next match from the to-do list."""
    print("\n--- RESEARCHING NEW MATCH ---")
    match = state["matches_to_process"].pop(0)
    home_team, away_team = match['home_team'], match['away_team']
    print(f"Researching: {home_team} vs {away_team}")
    
    search_results = batch_team_search.invoke({
        "home_team": home_team, "away_team": away_team, "match_date": match['commence_time'][:10]
    })
    return {**state, "current_match_info": match, "search_results": search_results}

def generate_analysis(state: AgentState) -> AgentState:
    """Generates analysis by dynamically building a prompt from config.json."""
    print("--- GENERATING ANALYSIS (FROM CONFIG) ---")

    # --- Load Config ---
    try:
        with open("config.json", "r") as f:
            config = json.load(f)
        analysis_persona = config.get("analysis_persona", "You are a sports betting analyst.")
        priorities = config.get("search_priorities", {})
        instructions = config.get("instructions", "Generate a JSON object.")
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"Warning: Could not load or parse config.json. Using default prompt. Error: {e}")
        analysis_persona = "You are a sports betting analyst."
        priorities = {}
        instructions = "Generate a JSON object."

    # --- Get Data from State ---
    match = state["current_match_info"]
    db = SoccerDatabase()
    odds_history = db.get_odds_history_for_match(match['match_id'])

    # --- Build Prompt Sections ---
    odds_prompt_section = ""
    if not odds_history:
        odds_prompt_section = "No odds data available for this match."
    else:
        latest_odds = odds_history[0]
        odds_prompt_section = f"""
**Current Betting Odds:**
- Bookmaker: {latest_odds['bookmaker']}
- Home Win ({match['home_team']}): {latest_odds['odds_home']}
- Draw: {latest_odds['odds_draw']}
- Away Win ({match['away_team']}): {latest_odds['odds_away']} """
        if len(odds_history) > 1:
            previous_odds = odds_history[1]
            odds_prompt_section += f"""

**Previous Betting Odds (for movement analysis):**
- Home Win: {previous_odds['odds_home']}
- Draw: {previous_odds['odds_draw']}
- Away Win: {previous_odds['odds_away']} """

    priority_prompt_section = ""
    if priorities:
        sorted_priorities = sorted(priorities.items(), key=lambda item: item[1])
        priority_list_str = ", ".join([p[0].replace('_', ' ') for p in sorted_priorities])
        priority_prompt_section = f"Your analysis should be weighted according to these priorities, in order: {priority_list_str}."

    # --- Assemble Final Prompt ---
    prompt = f"""{analysis_persona}

**Match:** {match['home_team']} vs {match['away_team']}
{odds_prompt_section}

**Context - Raw Search Data:**
---
{state["search_results"]}
---

**Instructions:**
{priority_prompt_section}
{instructions}

Generate a JSON object with the following structure:
- \"prediction\": A string, one of \"home_win\", \"away_win\", or \"draw\".
- \"prediction_text\": A short, human-readable prediction.
- \"confidence\": A string, one of \"High\", \"Medium\", or \"Low\".
- \"edge_reason\": A single, compelling sentence explaining your edge.
- \"key_factors\": A JSON array of 2-4 short strings."""

    # --- Call AI Model ---
    api_key = os.getenv("GEMINI_API_KEY")
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-2.5-flash')
    response = model.generate_content(prompt, generation_config=genai.types.GenerationConfig(response_mime_type="application/json"))
    analysis = json.loads(response.text)
    print(f"Analysis result: {analysis}")
    return {{**state, "current_analysis": analysis}}

def store_analysis(state: AgentState) -> AgentState:
    """Stores the new analysis in the database."""
    print("--- STORING ANALYSIS ---")
    db = sqlite3.connect("soccer_analysis.db")
    try:
        match = state["current_match_info"]
        analysis = state["current_analysis"]
        db.execute(
            "INSERT OR REPLACE INTO match_analysis (match_id, prediction, prediction_text, edge_reason, key_factors, confidence, raw_search_data, telegram_sent) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (match['match_id'], analysis['prediction'], analysis['prediction_text'], analysis['edge_reason'], json.dumps(analysis['key_factors']), analysis['confidence'], state['search_results'], False)
        )
        db.commit()
        print(f"Analysis for match {match['match_id']} saved.")
    finally:
        db.close()
    return state

def send_new_analysis_notification(state: AgentState) -> AgentState:
    """Sends a notification for a newly analyzed match."""
    _send_formatted_telegram_message(
        match_info=state["current_match_info"],
        analysis=state["current_analysis"]
    )
    processed = state["processed_matches"] + [state["current_match_info"]]
    return {**state, "processed_matches": processed}

# --- Graph Conditional Edges ---

def decide_what_to_do(state: AgentState) -> str:
    """Decides the next step based on the state."""
    print("\n--- DECIDING NEXT STEP ---")
    if state.get("notifications_to_send") and len(state["notifications_to_send"]) > 0:
        print(f"{len(state['notifications_to_send'])} unsent notifications to send.")
        return "process_unsent_notification"
    elif state.get("matches_to_process") and len(state["matches_to_process"]) > 0:
        print(f"{len(state['matches_to_process'])} new matches to process.")
        return "research_match"
    else:
        print("Nothing left to do.")
        return END

# --- Graph Definition ---

def build_agent_graph():
    workflow = StateGraph(AgentState)
    workflow.add_node("fetch_latest_odds", fetch_latest_odds)
    workflow.add_node("get_work_to_do", get_work_to_do)
    workflow.add_node("process_unsent_notification", process_unsent_notification)
    workflow.add_node("research_match", research_match)
    workflow.add_node("generate_analysis", generate_analysis)
    workflow.add_node("store_analysis", store_analysis)
    workflow.add_node("send_new_analysis_notification", send_new_analysis_notification)

    workflow.set_entry_point("fetch_latest_odds")
    workflow.add_edge("fetch_latest_odds", "get_work_to_do")
    workflow.add_conditional_edges("get_work_to_do", decide_what_to_do)
    workflow.add_conditional_edges("process_unsent_notification", decide_what_to_do)
    workflow.add_edge("research_match", "generate_analysis")
    workflow.add_edge("generate_analysis", "store_analysis")
    workflow.add_edge("store_analysis", "send_new_analysis_notification")
    workflow.add_conditional_edges("send_new_analysis_notification", decide_what_to_do)
    
    app = workflow.compile()
    print("Agent graph built successfully.")
    return app

def run_agent():
    app = build_agent_graph()
    print("\nRunning agent...")
    final_state = app.invoke({}, config={"recursion_limit": 150})
    print("\n--- AGENT RUN COMPLETE ---")
    print(f"Total matches processed in this run: {len(final_state.get('processed_matches', []))}")

if __name__ == "__main__":
    run_agent()
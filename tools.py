# tools.py
from langchain_core.tools import tool
import os
from dotenv import load_dotenv
import io
import contextlib
from datetime import datetime

# Import the pipeline function from our odds_fetcher script
from odds_fetcher import run_pipeline

# Load environment variables
load_dotenv()

# NEWS AGENT TOOLS

@tool
def google_grounding_search(query: str) -> str:
    """
    Search for the latest and most relevant news about sports teams and players, focusing on:
    - Injuries, suspensions, fitness updates
    - Team news, lineup changes, motivation
    - Recent developments that could impact match outcomes

    Use this tool when you need factual, real-time information directly from web search.

    Args:
        query: A focused search query, e.g., "Liverpool FC injury report August 2025"

    Returns:
        Up-to-date news snippets and information found via Google Search.
    """
    try:
        # Import the newer Google genai library
        from google import genai
        from google.genai import types
        import os

        # Get API key from environment
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            return "Error: GEMINI_API_KEY not found in environment variables"

        # Initialize client and grounding tool
        client = genai.Client(api_key=api_key)
        grounding_tool = types.Tool(google_search=types.GoogleSearch())

        # Configure for grounding
        grounding_config = types.GenerateContentConfig(
            tools=[grounding_tool]
        )

        # Make grounded search request
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=(
                f"You are a sports news assistant. "
                f"Use Google Search to find the latest factual information about: {query}. "
                "Focus on player injuries, team updates, and relevant news that affects match outcomes. "
                "Provide concise and accurate information with citations."
            ),
            config=grounding_config
        )

        result = response.text.strip()

        if not result:
            return "No results found from grounded search"

        return f"Current Information (via Google Search):\n{result}"

    except ImportError as e:
        return f"Error: google-genai library not available. Import error: {str(e)}"
    except Exception as e:
        return f"Error performing grounded search: {str(e)}"

@tool
def get_telegram_chat_id(username: str) -> str:
    """
    Finds the integer chat ID for a given Telegram username by checking the bot's recent updates.
    The user MUST have started a conversation with the bot for this to work.

    Args:
        username (str): The user's @username, without the leading '@'.

    Returns:
        str: The integer chat ID as a string if found, otherwise an error message.
    """
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        return "Error: TELEGRAM_BOT_TOKEN not found."

    if username.startswith('@'):
        username = username[1:]

    url = f"https://api.telegram.org/bot{bot_token}/getUpdates?limit=100"
    try:
        import requests
        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            return f"Error: API request failed with status {response.status_code}"

        updates = response.json().get('result', [])
        if not updates:
            return "Error: No recent updates found. Please message the bot first."

        for update in reversed(updates):
            message = update.get('message') or update.get('edited_message') or update.get('channel_post')
            if message:
                chat = message.get('chat')
                if chat and chat.get('username') == username:
                    return str(chat['id'])

        return f"Error: Chat ID for username '{username}' not found in recent updates. Please send a message to the bot."
    except Exception as e:
        return f"Error during API call: {str(e)}"


@tool
def send_telegram_message(message: str, chat_id: str = None, parse_mode: str = "Markdown") -> str:
    """
    Send a message to a Telegram chat using bot API.
    This tool can now resolve @usernames to numeric chat IDs.
    
    Args:
        message (str): The message text to send
        chat_id (str, optional): Target chat ID or username (@username). 
                                If None, uses default from TELEGRAM_DEFAULT_CHAT_ID.
        parse_mode (str): Message formatting - "Markdown", "HTML", or None.
        
    Returns:
        str: Success confirmation or an error description.
    """
    try:
        import requests

        bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        if not bot_token:
            return "❌ Error: TELEGRAM_BOT_TOKEN not found"

        if not chat_id:
            chat_id = os.getenv("TELEGRAM_DEFAULT_CHAT_ID")
            if not chat_id:
                return "❌ Error: No chat_id provided and TELEGRAM_DEFAULT_CHAT_ID not set"

        if chat_id.startswith('@'):
            print(f"Resolving username {chat_id} to a chat ID...")
            numeric_id = get_telegram_chat_id.invoke({"username": chat_id})
            
            if not numeric_id.lstrip('-').isdigit():
                return f"❌ Error resolving username: {numeric_id}"
            
            chat_id = numeric_id
            print(f"Resolved to chat ID: {chat_id}")

        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {'chat_id': chat_id, 'text': message}
        if parse_mode and parse_mode.lower() != "none":
            payload['parse_mode'] = parse_mode

        response = requests.post(url, json=payload, timeout=10)
        response_data = response.json()

        if response.status_code == 200 and response_data.get('ok'):
            chat_title = response_data.get('result', {}).get('chat', {}).get('title', chat_id)
            return f"✅ Message sent successfully to {chat_title}"
        else:
            error_desc = response_data.get('description', response.text)
            return f"❌ HTTP Error {response.status_code}: {error_desc}"

    except Exception as e:
        return f"❌ An unexpected error occurred: {str(e)}"


@tool 
def batch_team_search(home_team: str, away_team: str, match_date: str = None) -> str:
    """
    Efficiently search for comprehensive match-related information about both teams in a single API call.
    
    This tool batches multiple searches to minimize API usage while gathering all relevant betting information:
    - Injury reports and player availability for both teams
    - Recent form, motivation, and team news
    - Head-to-head analysis and match preview insights
    
    Args:
        home_team (str): Name of the home team
        away_team (str): Name of the away team  
        match_date (str, optional): Match date for more targeted search
        
    Returns:
        str: Comprehensive information about both teams and the upcoming match
    """
    try:
        current_date = match_date or datetime.now().strftime("%B %Y")
        
        comprehensive_query = (
            f"Find current betting-relevant information for {home_team} vs {away_team} match in {current_date}: "
            f"1) {home_team} injury report, suspensions, player availability, recent home form and motivation; "
            f"2) {away_team} injury report, suspensions, player availability, recent away form and motivation; "
            f"3) Head-to-head history, tactical matchups, and match preview analysis; "
            f"4) Any other factors affecting match outcome like team news, lineup changes, or external motivations. "
            f"Focus on factual information that could impact betting decisions."
        )
        
        result = google_grounding_search.invoke({"query": comprehensive_query})
        
        return result
        
    except Exception as e:
        return f"Error in batch team search: {str(e)}"

@tool
def update_odds_database() -> str:
    """
    Runs the odds fetching pipeline to get the latest match odds from the API
    and load them into the database. This should be done before any analysis
    to ensure the data is fresh.
    """
    print("--- Calling odds fetching pipeline ---")
    
    output_stream = io.StringIO()
    try:
        with contextlib.redirect_stdout(output_stream):
            run_pipeline()
        
        output = output_stream.getvalue()
        print("--- Odds fetching pipeline finished ---")
        return f"Successfully ran odds fetching pipeline. Output:\n{output}"
    except Exception as e:
        return f"An error occurred while running the odds fetching pipeline: {e}"
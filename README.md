# AI Sports Betting Analysis Agent



---

## Features

- **Automated Odds Fetching**: The agent runs a script to pull the latest odds from an API for multiple leagues.
- **Historical Odds Tracking**: Stores odds over time in a database to track line movement.
- **AI-Powered Analysis**: Uses a Large Language Model (Gemini) to analyze qualitative data (news, form, injuries) in the context of quantitative data (betting odds and their movement).
- **Configurable Strategy**: A `config.json` file allows you to change the agent's analytical priorities without touching the code.
- **Robust Workflow**: Built with LangGraph, the agent is fault-tolerant, able to retry sending failed Telegram messages and avoiding re-analysis of completed work.
- **Notifications**: Delivers final betting picks through a Telegram bot.

---

## Project Structure

```
.env                  # Holds API keys and secrets
config.json           # Configures the agent's analysis strategy
requirements.txt      # Python package dependencies

soccer_analysis.db    # The SQLite database

database_setup.py     # Defines the database schema and helper functions
odds_fetcher.py       # Script to fetch odds from API and load into the DB
tools.py              # Contains tools for the agent (web search, Telegram)
news_agent.py         # The main LangGraph agent logic

reset_flags.py        # Helper script to reset Telegram sent status in the DB
visualize_agent.py    # Script to generate a diagram of the agent's workflow
```

---

## Setup and Installation

**1. Create and Activate Virtual Environment:**

It is highly recommended to use a virtual environment to manage dependencies.

```bash
# Create the virtual environment
python -m venv venv

# Activate it (on Windows)
.\venv\Scripts\activate
```

**2. Install Dependencies:**

Install all the required Python packages from the `requirements.txt` file.

```bash
pip install -r requirements.txt
```

**3. Set Up Environment Variables:**

Create a file named `.env` in the project root. This file will hold your secret API keys. Add the following lines, replacing the placeholder text with your actual keys:

```
# Get this from a provider like The Odds API
ODDS_API_KEY="your_odds_api_key_here"

# Get this from Google AI Studio
GEMINI_API_KEY="your_gemini_api_key_here"

# Get this from BotFather on Telegram
TELEGRAM_BOT_TOKEN="your_telegram_bot_token_here"

# Your personal/group/channel chat ID. Must start a chat with the bot first.
TELEGRAM_DEFAULT_CHAT_ID="@your_telegram_username_or_id"
```

---

## How to Use

The project is designed to be run from a single entry point.

**1. Configure Your Strategy (Optional):**

Open `config.json`. You can change the `analysis_persona` and `instructions` to modify the agent's tone and focus. Most importantly, you can re-order the `search_priorities` by changing the numbers (1 is highest priority).

**2. Run the Agent:**

Execute the main agent script. It will run the entire end-to-end pipeline.

```bash
python news_agent.py
```

This command will:
- Trigger the `odds_fetcher.py` script to get the latest odds and update the database.
- Check for any previously analyzed matches that failed to send to Telegram and retry sending them.
- Find all new, un-analyzed matches.
- For each new match, it will perform the research, generate an AI analysis based on your `config.json`, save it to the database, and send the result to Telegram.

**3. Visualize the Workflow (Optional):**

You can print an ASCII diagram of the agent's logic to the console.

```bash
python visualize_agent.py
```

This will output a text-based representation of the agent's workflow.

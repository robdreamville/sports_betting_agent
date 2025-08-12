# visualize_agent.py
from news_agent import build_agent_graph

def main():
    """Builds the agent graph and prints an ASCII visualization to the console."""
    print("Building agent graph...")
    app = build_agent_graph()
    
    print("\n--- Agent Workflow Diagram ---")
    print(app.get_graph().draw_ascii())
    print("----------------------------\n")

if __name__ == "__main__":
    main()
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from streamlit_analytics_client.components.ask_flow import render_page  # noqa: E402

render_page()

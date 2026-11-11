import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st  # noqa: E402

from streamlit_analytics_client.utils.session import init_session  # noqa: E402

DATA_RANGE_LABEL = "Data available: January 2026 to July 2026"

st.set_page_config(page_title="Transport Analytics", layout="wide")
init_session(st.session_state)

# Ask Analytics is the landing page; the pages are registered explicitly so no separate home page is shown.
pages = [
    st.Page("pages/1_Ask_Analytics.py", title="Ask Analytics", icon=":material/chat:", default=True),
    st.Page("pages/2_Query_History.py", title="Query History", icon=":material/history:"),
    st.Page("pages/3_Metric_Definitions.py", title="Metric Definitions", icon=":material/menu_book:"),
]
st.sidebar.info(DATA_RANGE_LABEL, icon=":material/calendar_month:")
page = st.navigation(pages)
st.caption(f":material/calendar_month: {DATA_RANGE_LABEL}")
page.run()

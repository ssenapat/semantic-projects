import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from streamlit_analytics_client.models import session_models as sm  # noqa: E402
from streamlit_analytics_client.utils.session import init_session  # noqa: E402

init_session(st.session_state)
st.title("Query History")
hist = st.session_state[sm.KEY_HISTORY]
if not hist:
    st.write("No queries yet.")
else:
    df = pd.DataFrame(list(reversed(hist)), columns=list(sm.HISTORY_FIELDS))
    df["applied_filters"] = df["applied_filters"].astype(str)
    df["metrics"] = df["metrics"].apply(lambda m: ", ".join(m) if isinstance(m, (list, tuple)) else (m or ""))
    st.dataframe(df, use_container_width=True)

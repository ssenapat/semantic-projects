import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st  # noqa: E402

from streamlit_analytics_client.components.api_client import ApiError, make_client  # noqa: E402
from streamlit_analytics_client.utils.errors import user_message  # noqa: E402



def combines_with_line(metric: dict, metrics: list) -> str:
    """C-2.9.10: other metric labels in the same combinable_with group, sorted by metric name."""
    group = metric.get("combinable_with")
    others = sorted((o for o in metrics if group and o.get("combinable_with") == group and o.get("name") != metric.get("name")),
                    key=lambda o: o.get("name", ""))
    labels = ", ".join(o.get("label") or o.get("name", "") for o in others)
    return f"Combines with: {labels or 'none'}"


st.title("Metric Definitions")
try:
    cat = make_client().list_metrics()
except ApiError as e:
    st.error(user_message(e.status, e.request_id))
else:
    st.caption(f"Catalog: {cat.get('catalog_version', '')}")
    all_metrics = cat.get("metrics", [])
    for m in all_metrics:
        with st.expander(m.get("label") or m["name"]):
            st.write(m.get("description", ""))
            version = m.get("version") or m.get("metric_version") or cat.get("catalog_version", "")
            st.write(f"Status: {m.get('status', '')} · Version: {version}")
            if m.get("unit"):
                st.write(f"Unit: {m['unit']}")
            if m.get("time_basis"):
                st.write(f"Time basis: {m['time_basis']}")
            dims = ", ".join(d["label"] for d in m.get("allowed_dimensions", []))
            if dims:
                st.write(f"Dimensions: {dims}")
            st.write(combines_with_line(m, all_metrics))
            if m.get("allowed_time_grains"):
                st.write("Time grains: " + ", ".join(m["allowed_time_grains"]))
            if m.get("synonyms"):
                st.write("Also known as: " + ", ".join(m["synonyms"]))
            for c in m.get("caveats", []):
                st.caption(c)

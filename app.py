import numpy as np
import pandas as pd
import json
import datetime as dt
import streamlit as st
import zipfile
from streamlit_local_storage import LocalStorage

# REMINDER for myself: Use Rubberducking

st.title("Carry Free Pedometer")

zip = st.file_uploader(
    "Upload the Google Fit's Takeout .zip file",
    type="zip")

st.info("Large Takeout files may take a minute or two to process after upload.")

if zip is not None:
    with st.spinner("Processing your Takeout file. This may take a minute..."):
        rows = []  # List for dict rows

        try:
            with zipfile.ZipFile(zip, "r") as z:
                # Returns all the file path, even in subfolders
                for file in z.namelist():
                    if (
                        file.startswith("Takeout/Fit/All sessions/")
                    ):
                        try:
                            with z.open(file) as f:
                                j = json.load(f)

                        except (json.JSONDecodeError, UnicodeDecodeError) as e:
                            st.warning(f"Skipping {name}: {e}")
                            continue

                        # Extracting data into dict
                        r = {
                            "activity" : j.get("fitnessActivity"),
                            "duration_s": float(
                                j.get("duration", "0s").rstrip("s")
                            ),
                        }

                        # Iterating inside aggregate nested key-value pairs 
                        for a in j.get("aggregate", []):
                            name = a.get("metricName")
                            if not name:
                                continue
                            # Fallback to intValue
                            r[name.split("com.google.")[-1]] = (
                                a.get("floatValue", a.get('intValue'))
                            )

                        rows.append(r)

                sessions = pd.DataFrame(rows) # Converting list into DataFrame

                # Imputing missing columns
                missing_cols = [
                    "heart_minutes.summary",
                    "step_count.delta",
                    "distance.delta",
                ]

                for col in missing_cols:
                    sessions[col] = sessions[col].fillna(
                        sessions.groupby("activity")[col].transform("median")
                    ).fillna(sessions[col].median())  # Global fallback

                # Curve fitting
                def linear_reg(df, x, y):
                    out = {}  # {Parameter {Activity {m, c}}}

                    for activity, row in df.groupby("activity"):
                        cols = row[[x, y]]

                        if len(cols) < 3 or cols[x].nunique() < 2:
                            continue

                        # Linear regression
                        m, c = np.polyfit(cols[x], cols[y], 1)
                        out[activity] = {
                            "m": float(m),
                            "c": float(c)
                        }

                    return out

                # Storing 'm' and 'c' in json for each activity
                coeffs = {
                    "heart_minutes": linear_reg(sessions, "active_minutes", "heart_minutes.summary"), 
                    "cal": linear_reg(sessions, "duration_s", "calories.expended"), 
                    "dist_m": linear_reg(sessions, "active_minutes", "distance.delta")
                }

                json_data = json.dumps(coeffs, indent=2)

                st.success("JSON file created successfully!")

                with st.expander("Preview JSON file"):
                    st.json(coeffs)

                st.download_button(
                    label="Download your JSON file",
                    data=json_data,
                    file_name="coeffs.json",
                    mime="application/json",
                )

        except zipfile.BadZipFile:
            st.error("The uploaded file is not a valid ZIP.")


# Reading JSON
local_storage = LocalStorage()

uploaded_json = st.file_uploader(
    "Upload your JSON file",
    type="json"
)

if uploaded_json is not None:
    try:
        coeffs = json.loads(uploaded_json.getvalue().decode("utf-8"))

        required = {"heart_minutes", "cal", "dist_m"}
        if not required.issubset(coeffs):
            st.error("Invalid JSON structure.")
        else:
            # Make a session state
            st.session_state["coeffs"] = coeffs

        local_storage.setItem(
            "reg_coeffs",
            json.dumps(coeffs)
        )

        st.success("JSON loaded and values saved.")

    except (json.JSONDecodeError, UnicodeDecodeError):
        st.error("Could not read the JSON file.")

if "coeffs" not in st.session_state:
    # Retrieving coeffs from browser storage after loading JSON
    stored = local_storage.getItem("reg_coeffs")

    if stored:
        try:
            st.session_state["coeffs"] = (
                json.loads(stored) if isinstance(stored, str)
                else stored
            )
        except (json.JSONDecodeError, TypeError):
            st.warning("Cannot retrieve values. Upload the JSON file again.")

coeffs = st.session_state.get("coeffs")
if not coeffs:
    st.info("Upload your JSON file to get started.")


# Estimating parameters
if coeffs:
    st.header("Estimate your activity")

    # Activities dropdown from JSON
    activities = (
        set(coeffs["heart_minutes"]) &
        set(coeffs["cal"]) &
        set(coeffs["dist_m"])
    )

    if activities:
        activity = st.selectbox(
            "Select activity", sorted(activities)
        )
    
    else:
        st.error("No activities found.")
        st.stop()

    # Inputs
    c1, c2 = st.columns(2)  # Divide the page into 2 cols
    start = c1.time_input("Left at", dt.time(6, 0))
    end = c2.time_input("Back at", dt.time(6, 40))
    brks = st.number_input("Break time (in mins)", 0, 120, 0)

    # Calculating duration
    today = dt.date.today()
    t0 = dt.datetime.combine(today, start)
    t1 = dt.datetime.combine(today, end)
    if t1 <= t0:
        t1 += dt.timedelta(days=1)  # Crossed midnight

    duration = (t1 - t0).total_seconds() / 60
    active_minutes = duration - brks

    st.caption(
        f"Total duration: {duration:.0f} min · "
        f"Active time: {active_minutes:.0f} min"
    )

    def estimate(duration, active_minutes, coeffs, activity):

        heart_minutes = (coeffs["heart_minutes"][activity]["m"]
        * active_minutes + coeffs["heart_minutes"][activity]["c"])

        cal = (coeffs["cal"][activity]["m"]
        * duration * 60 + coeffs["cal"][activity]["c"])

        dist_m = (coeffs["dist_m"][activity]["m"]
                * active_minutes + coeffs["dist_m"][activity]["c"])

        speed = dist_m / (active_minutes * 60)
        
        return heart_minutes, cal, dist_m, speed

    if st.button("Estimate"):  # If clicked
        heart_minutes, cal, dist_m, speed = estimate(duration, active_minutes, coeffs, activity)

        st.subheader("Heart minutes")

        h1, h2, h3 = st.columns(3)

        with h1:
            st.metric("Low intensity", f"{heart_minutes * 0.6:.1f}")
            st.caption("Can talk or sing")

        with h2:
            st.metric("Medium intensity", f"{heart_minutes:.1f}")
            st.caption("Can talk but can't sing")

        with h3:
            st.metric("High intensity", f"{heart_minutes * 1.4:.1f}")
            st.caption("Can only speak short sentences")

        st.divider()

        c1, c2, c3 = st.columns(3)

        c1.metric("Calories", f"{cal:.1f}")
        c2.metric("Distance", f"{dist_m / 1000:.3f} km")
        c3.metric("Average speed", f"{speed:.2f} m/s")
                            

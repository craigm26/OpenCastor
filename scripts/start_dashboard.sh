#!/bin/bash
# Auto-start script for CastorDash on Raspberry Pi (Kiosk Mode)
cd "$HOME/opencastor" || exit 1
source venv/bin/activate
streamlit run castor/dashboard.py --server.headless true --server.port 8501

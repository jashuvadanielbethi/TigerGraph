"""Analyst dashboard for the HHGOA fraud investigation agent.

Reads the 20 answer files in cases/*.json (the actual submission artifacts)
and renders case progression, evidence, uncertainty, and next-best-action
before/after evidence gathering. Also exposes a "new investigation" panel
that runs the live pipeline (agent/investigate.py) against any transaction
in the dataset, not just the 20 case-pack cases -- demonstrating the
"monitors beyond the 20 cases" optional capability from the dataset README.

Run: streamlit run ui/app.py

Note: this file is presentation only. Every data/investigation function
(load_cases, load_data_index, has_processed_data, and the call into
agent.investigate.investigate) is untouched -- only markup, CSS, and layout
changed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
CASES_DIR = ROOT / "cases"
PROCESSED_DIR = ROOT / "data" / "processed"

st.set_page_config(page_title="HHGOA Fraud Investigation Agent", layout="wide", page_icon="\U0001F50E")

VERDICT_META = {
    "fraud": {"label": "Fraud", "color": "#ff3b30", "tint": "rgba(255,59,48,0.10)", "icon": "alert"},
    "legitimate": {"label": "Legitimate", "color": "#0c9d45", "tint": "rgba(12,157,69,0.10)", "icon": "shield"},
    "uncertain": {"label": "Uncertain", "color": "#ff9500", "tint": "rgba(255,149,0,0.10)", "icon": "help"},
}
ROUTE_META = {
    "auto": {"label": "Auto-executed", "color": "#0c9d45", "icon": "check"},
    "L1": {"label": "L1 — team lead", "color": "#ff9500", "icon": "user"},
    "L2": {"label": "L2 — fraud manager", "color": "#ff3b30", "icon": "shieldalert"},
}
STATUS_LABEL = {
    "closed_fraud": "Closed · fraud", "closed_legitimate": "Closed · legitimate",
    "escalated": "Escalated to analyst", "open": "Open",
}

# Kept for backwards-compat with any external reference; UI now renders
# badges via badge_html() below instead of plain text.
VERDICT_BADGE = {k: v["label"] for k, v in VERDICT_META.items()}
ROUTE_BADGE = {k: v["label"] for k, v in ROUTE_META.items()}
STATUS_BADGE = STATUS_LABEL


# ---------------------------------------------------------------------------
# Data layer -- unchanged from the previous version, only comments trimmed.
# ---------------------------------------------------------------------------

@st.cache_data
def load_cases() -> dict[str, dict]:
    cases = {}
    for f in sorted(CASES_DIR.glob("*.json")):
        cases[f.stem] = json.loads(f.read_text())
    return cases


@st.cache_resource
def load_data_index():
    from agent.data_index import get_index
    return get_index()


def has_processed_data() -> bool:
    return (PROCESSED_DIR / "transactions.pkl").exists()


# ---------------------------------------------------------------------------
# Design system: theme injection, icon set, small HTML component builders.
# Apple-style typography/spacing + Blinkit-style accent color & pop-in
# motion. Pure CSS animations only (no JS), so they replay reliably every
# time Streamlit re-renders a fresh result.
# ---------------------------------------------------------------------------

_ICONS = {
    "search": '<path d="M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14Z"/><path d="m20 20-4.35-4.35"/>',
    "shield": '<path d="M12 3 4.5 6v6c0 4.5 3 7.5 7.5 9 4.5-1.5 7.5-4.5 7.5-9V6L12 3Z"/><path d="m8.5 12 2.4 2.4L15.5 9.5"/>',
    "alert": '<path d="M12 3 2 20h20L12 3Z"/><path d="M12 10v4"/><path d="M12 17h.01"/>',
    "help": '<circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 0 1 4.9.8c0 1.7-2.4 2-2.4 3.7"/><path d="M12 17h.01"/>',
    "check": '<circle cx="12" cy="12" r="9"/><path d="m8.5 12.5 2.3 2.3L16 9.5"/>',
    "user": '<circle cx="12" cy="8" r="3.3"/><path d="M5.5 20c1.2-3.6 4-5.4 6.5-5.4s5.3 1.8 6.5 5.4"/>',
    "shieldalert": '<path d="M12 3 4.5 6v6c0 4.5 3 7.5 7.5 9 4.5-1.5 7.5-4.5 7.5-9V6L12 3Z"/><path d="M12 8v4.5"/><path d="M12 15.2h.01"/>',
    "dollar": '<circle cx="12" cy="12" r="9"/><path d="M12 6.5v11"/><path d="M15 9.2c0-1.2-1.3-2.2-3-2.2s-3 .9-3 2.1c0 3 6 1.4 6 4.4 0 1.3-1.3 2.2-3 2.2s-3-1-3-2.3"/>',
    "activity": '<path d="M3 12h4l2.5-7L14 19l2.5-7H21"/>',
    "tag": '<path d="M11.5 3H5a2 2 0 0 0-2 2v6.5a2 2 0 0 0 .6 1.4l8.5 8.5a2 2 0 0 0 2.8 0l6.5-6.5a2 2 0 0 0 0-2.8l-8.5-8.5a2 2 0 0 0-1.4-.6Z"/><circle cx="8.2" cy="8.2" r="1.3"/>',
    "flag": '<path d="M6 21V4"/><path d="M6 4h11l-2.5 4L17 12H6"/>',
    "file": '<path d="M7 3h7l5 5v13a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Z"/><path d="M14 3v5h5"/><path d="M9 13h6"/><path d="M9 17h6"/>',
    "link": '<path d="M9.5 14.5 14.5 9.5"/><path d="M11 6.5 12.6 4.9a3.6 3.6 0 0 1 5.1 5.1L16 11.6"/><path d="M13 17.5 11.4 19a3.6 3.6 0 0 1-5.1-5.1L8 12.4"/>',
    "chip": '<rect x="7" y="7" width="10" height="10" rx="1.5"/><path d="M12 3v3.2"/><path d="M12 17.8V21"/><path d="M3 12h3.2"/><path d="M17.8 12H21"/><path d="M7 4v0"/>',
    "database": '<ellipse cx="12" cy="6" rx="7" ry="3"/><path d="M5 6v6c0 1.7 3.1 3 7 3s7-1.3 7-3V6"/><path d="M5 12v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6"/>',
    "sparkle": '<path d="M12 3v4"/><path d="M12 17v4"/><path d="M3 12h4"/><path d="M17 12h4"/><path d="m5.6 5.6 2.8 2.8"/><path d="m15.6 15.6 2.8 2.8"/><path d="m18.4 5.6-2.8 2.8"/><path d="m8.4 15.6-2.8 2.8"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.3 2"/>',
}


def icon(name: str, size: int = 16, color: str = "currentColor", stroke: float = 2.0) -> str:
    body = _ICONS.get(name, _ICONS["sparkle"])
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
            f'stroke="{color}" stroke-width="{stroke}" stroke-linecap="round" '
            f'stroke-linejoin="round" style="vertical-align:-3px">{body}</svg>')


def inject_theme() -> None:
    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=Manrope:wght@500;700;800&display=swap');

    :root{
        --accent:#0c9d45; --accent-dark:#087a35; --accent-tint:rgba(12,157,69,.10);
        --danger:#ff3b30; --warning:#ff9500;
        --ink:#1d1d1f; --ink-soft:#6e6e73; --ink-faint:#aeaeb2;
        --surface:#ffffff; --surface-alt:#f7f7fa; --bg:#f4f4f7;
        --border:rgba(0,0,0,.07); --shadow-sm:0 1px 2px rgba(0,0,0,.04);
        --shadow-md:0 10px 30px -12px rgba(0,0,0,.16);
        --radius-lg:20px; --radius-md:14px; --radius-sm:10px;
    }

    html, body, [class^="st-"], [class*=" st-"], .stApp, .stMarkdown, p, span, div, button, input, textarea, select {
        font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "Inter", "Manrope", ui-sans-serif, sans-serif !important;
    }
    .stApp { background: var(--bg); color: var(--ink); }
    h1, h2, h3 { letter-spacing:-0.02em; font-weight:800 !important; color:var(--ink); }
    p, li, label, .stMarkdown { color: var(--ink); }
    small, .stCaption, [data-testid="stCaptionContainer"] { color: var(--ink-soft) !important; }

    /* ---- hero header ---- */
    .hero { display:flex; align-items:center; gap:14px; padding:22px 26px; margin-bottom:6px;
        background: linear-gradient(135deg, #101113 0%, #1d1d1f 60%, #0c1f12 130%);
        border-radius: var(--radius-lg); box-shadow: var(--shadow-md); animation: popIn .5s cubic-bezier(.34,1.56,.64,1) both; }
    .hero-icon { width:46px; height:46px; border-radius:14px; background:var(--accent);
        display:flex; align-items:center; justify-content:center; flex:none;
        box-shadow: 0 6px 18px rgba(12,157,69,.45); }
    .hero-title { color:#fff; font-size:1.5rem; font-weight:800; letter-spacing:-0.02em; margin:0; }
    .hero-sub { color:rgba(255,255,255,.62); font-size:.86rem; margin-top:2px; }

    /* ---- tabs, Apple segmented-control style ---- */
    [data-testid="stTabs"] [role="tablist"]{ gap:4px; background:var(--surface-alt); padding:5px; border-radius:999px;
        border:1px solid var(--border); width:fit-content; margin-bottom:18px; }
    [data-testid="stTab"]{ border-radius:999px !important; padding:7px 18px !important; transition:all .2s ease;
        color:var(--ink-soft) !important; font-weight:600 !important; }
    [data-testid="stTab"] p{ font-weight:600 !important; }
    [data-testid="stTab"][aria-selected="true"]{ background:var(--surface) !important; color:var(--ink) !important;
        box-shadow: var(--shadow-sm); }
    [data-testid="stTab"][aria-selected="true"] p{ color:var(--ink) !important; }
    [data-testid="stTabs"] [role="tablist"] > div[data-baseweb], [data-baseweb="tab-highlight"], [data-baseweb="tab-border"],
    .react-aria-SelectionIndicator{ display:none !important; }

    /* ---- buttons: Blinkit-style bold pill with press feedback ---- */
    .stButton>button, [data-testid="stFormSubmitButton"]>button{ border-radius:999px !important;
        font-weight:700 !important; padding:.55rem 1.4rem !important;
        border:none !important; transition: transform .12s ease, box-shadow .2s ease !important; }
    .stButton>button[kind="primary"], [data-testid="stFormSubmitButton"]>button[kind="primaryFormSubmit"]{
        background: linear-gradient(135deg, var(--accent), var(--accent-dark)) !important;
        color:#fff !important; box-shadow: 0 8px 20px -6px rgba(12,157,69,.55) !important; }
    .stButton>button:hover, [data-testid="stFormSubmitButton"]>button:hover{ transform: translateY(-2px); }
    .stButton>button:active, [data-testid="stFormSubmitButton"]>button:active{ transform: scale(.96) translateY(0); }

    /* ---- inputs ---- */
    .stTextInput input, .stTextArea textarea,
    div[data-baseweb="select"]>div, [data-testid="stSelectbox"] div[data-baseweb="select"]>div{
        border-radius: var(--radius-sm) !important; border:1.5px solid var(--border) !important;
        transition: border-color .15s ease, box-shadow .15s ease !important;
        background:var(--surface) !important; color:var(--ink) !important;
        caret-color: var(--ink) !important; -webkit-text-fill-color: var(--ink) !important; }
    .stTextInput input::placeholder, .stTextArea textarea::placeholder{ color:var(--ink-faint) !important; opacity:1 !important; }
    div[data-baseweb="select"] *, [data-testid="stSelectbox"] *{ color:var(--ink) !important; }
    .stTextInput input:focus, .stTextArea textarea:focus{
        border-color: var(--accent) !important; box-shadow: 0 0 0 3px var(--accent-tint) !important; }

    /* ---- cards, chips, badges ---- */
    .card{ background:var(--surface); border:1px solid var(--border); border-radius:var(--radius-lg);
        padding:20px 22px; box-shadow:var(--shadow-sm); margin-bottom:14px; }
    .pop-in{ animation: popIn .5s cubic-bezier(.34,1.56,.64,1) both; }

    .badge{ display:inline-flex; align-items:center; gap:6px; padding:6px 14px; border-radius:999px;
        font-weight:700; font-size:.86rem; position:relative; }
    .badge .ring{ position:absolute; inset:-4px; border-radius:inherit; border:2px solid currentColor;
        opacity:.55; animation: pulseRing 1.7s ease-out infinite; pointer-events:none; }

    .chip{ display:inline-flex; align-items:center; gap:5px; padding:4px 10px; margin:2px 4px 2px 0;
        border-radius:8px; font-size:.78rem; font-weight:600; background:var(--surface-alt);
        border:1px solid var(--border); color:var(--ink); }

    .stat-grid{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin:14px 0 18px; }
    .stat-card{ background:var(--surface); border:1px solid var(--border); border-radius:var(--radius-md);
        padding:14px 16px; box-shadow:var(--shadow-sm); animation: popIn .45s cubic-bezier(.34,1.56,.64,1) both; }
    .stat-label{ display:flex; align-items:center; gap:6px; color:var(--ink-soft); font-size:.74rem;
        font-weight:700; text-transform:uppercase; letter-spacing:.04em; margin-bottom:6px; }
    .stat-value{ font-size:1.6rem; font-weight:800; letter-spacing:-0.02em; color:var(--ink); }

    .action-card{ display:flex; align-items:flex-start; justify-content:space-between; gap:10px;
        background:var(--surface); border:1px solid var(--border); border-radius:var(--radius-md);
        padding:12px 14px; margin-bottom:10px; animation: popIn .4s cubic-bezier(.34,1.56,.64,1) both; }
    .action-card:hover{ border-color:rgba(0,0,0,.14); }
    .action-name{ font-weight:800; font-size:.92rem; letter-spacing:-0.01em; }
    .action-reason{ color:var(--ink-soft); font-size:.8rem; margin-top:3px; line-height:1.4; }

    .quote-card{ background:var(--surface-alt); border-left:3px solid var(--accent); border-radius:0 var(--radius-sm) var(--radius-sm) 0;
        padding:14px 16px; font-size:.92rem; color:var(--ink); line-height:1.5; margin:10px 0 16px; }

    .divider-soft{ height:1px; background:var(--border); margin:14px 0; border:none; }

    /* success flash for the New investigation result -- Blinkit "order placed" feel */
    .success-flash{ display:flex; align-items:center; gap:12px; padding:16px 20px; border-radius:var(--radius-lg);
        background: linear-gradient(135deg, var(--accent-tint), rgba(12,157,69,.02));
        border:1px solid rgba(12,157,69,.25); margin:14px 0 18px;
        animation: popIn .55s cubic-bezier(.34,1.56,.64,1) both; }
    .success-flash .burst{ width:40px; height:40px; border-radius:50%; background:var(--accent); flex:none;
        display:flex; align-items:center; justify-content:center; position:relative;
        box-shadow:0 6px 16px -4px rgba(12,157,69,.6); }
    .success-flash .burst .ring{ position:absolute; inset:-8px; border-radius:50%; border:2px solid var(--accent);
        opacity:.6; animation: pulseRing 1.3s ease-out 2; }

    @keyframes popIn{ 0%{ opacity:0; transform:scale(.85) translateY(10px);} 60%{ opacity:1; transform:scale(1.03);} 100%{ transform:scale(1) translateY(0);} }
    @keyframes pulseRing{ 0%{ transform:scale(.85); opacity:.65;} 100%{ transform:scale(1.5); opacity:0;} }

    /* ---- glassmorphism: New investigation form ---- */
    [data-testid="stForm"]{
        position:relative; overflow:hidden; border:1px solid rgba(255,255,255,.55) !important;
        border-radius:26px !important; padding:26px 26px 20px !important;
        background:
            radial-gradient(420px 260px at 8% 10%, rgba(12,157,69,.38), transparent 70%),
            radial-gradient(380px 300px at 92% 18%, rgba(10,132,255,.32), transparent 70%),
            radial-gradient(360px 280px at 70% 105%, rgba(191,90,242,.30), transparent 70%),
            linear-gradient(135deg, #eaf7ee 0%, #eaf1ff 55%, #f4eaff 100%) !important;
        box-shadow: 0 24px 60px -24px rgba(20,40,90,.35) !important; }
    [data-testid="stForm"] label, [data-testid="stForm"] label p{ color:#1d1d1f !important; font-weight:700 !important; }

    [data-testid="stForm"] .stTextInput input,
    [data-testid="stForm"] .stTextArea textarea,
    [data-testid="stForm"] div[data-baseweb="select"] > div,
    [data-testid="stForm"] [data-testid="stSelectbox"] div[data-baseweb="select"] > div{
        background: rgba(255,255,255,.42) !important;
        -webkit-backdrop-filter: blur(16px) saturate(170%); backdrop-filter: blur(16px) saturate(170%);
        border:1px solid rgba(255,255,255,.75) !important; border-radius:16px !important;
        box-shadow: 0 8px 24px -10px rgba(20,40,90,.28), inset 0 1px 0 rgba(255,255,255,.85) !important;
        color:#1d1d1f !important; -webkit-text-fill-color:#1d1d1f !important; }
    [data-testid="stForm"] [data-testid="stTextInputRootElement"],
    [data-testid="stForm"] [data-testid="stTextAreaRootElement"]{
        background: rgba(255,255,255,.42) !important;
        -webkit-backdrop-filter: blur(16px) saturate(170%); backdrop-filter: blur(16px) saturate(170%);
        border:1px solid rgba(255,255,255,.75) !important; border-radius:16px !important; overflow:hidden;
        box-shadow: 0 8px 24px -10px rgba(20,40,90,.28), inset 0 1px 0 rgba(255,255,255,.85) !important;
        transition: all .2s ease; }
    [data-testid="stForm"] [data-testid="stTextInputRootElement"]:focus-within,
    [data-testid="stForm"] [data-testid="stTextAreaRootElement"]:focus-within{
        background: rgba(255,255,255,.62) !important; border-color: rgba(12,157,69,.85) !important;
        box-shadow: 0 0 0 4px rgba(12,157,69,.18), 0 10px 28px -10px rgba(12,157,69,.4) !important; }
    [data-testid="stForm"] [data-testid="stSelectbox"] div:has(> input){
        background: rgba(255,255,255,.42) !important;
        -webkit-backdrop-filter: blur(16px) saturate(170%); backdrop-filter: blur(16px) saturate(170%);
        border:1px solid rgba(255,255,255,.75) !important; border-radius:16px !important;
        box-shadow: 0 8px 24px -10px rgba(20,40,90,.28), inset 0 1px 0 rgba(255,255,255,.85) !important; }
    [data-testid="stForm"] [data-testid="stSelectbox"] div:has(> input):focus-within{
        background: rgba(255,255,255,.62) !important; border-color: rgba(12,157,69,.85) !important;
        box-shadow: 0 0 0 4px rgba(12,157,69,.18), 0 10px 28px -10px rgba(12,157,69,.4) !important; }
    [data-testid="stForm"] .stTextInput input, [data-testid="stForm"] .stTextArea textarea,
    [data-testid="stForm"] .stTextInput input:focus, [data-testid="stForm"] .stTextArea textarea:focus{
        background:transparent !important; border:none !important; box-shadow:none !important;
        -webkit-backdrop-filter:none !important; backdrop-filter:none !important; }
    [data-testid="stForm"] .stTextInput input{ padding:.7rem .95rem !important; font-size:1rem !important; }
    [data-testid="stForm"] .stTextInput input::placeholder,
    [data-testid="stForm"] .stTextArea textarea::placeholder{ color:rgba(29,29,31,.45) !important; -webkit-text-fill-color:rgba(29,29,31,.45) !important; }
    [data-testid="stForm"] .stTextInput input:focus,
    [data-testid="stForm"] .stTextArea textarea:focus,
    [data-testid="stForm"] div[data-baseweb="select"]:focus-within > div{
        background: rgba(255,255,255,.62) !important; border-color: rgba(12,157,69,.85) !important;
        box-shadow: 0 0 0 4px rgba(12,157,69,.18), 0 10px 28px -10px rgba(12,157,69,.4) !important; }
    [data-testid="stForm"] div[data-baseweb="select"] *, [data-testid="stForm"] [data-testid="stSelectbox"] *{
        color:#1d1d1f !important; -webkit-text-fill-color:#1d1d1f !important; background-color:transparent !important; }
    [data-testid="stForm"] div[data-baseweb="select"] svg{ fill:#1d1d1f !important; }

    [data-testid="stForm"] [data-testid="stFormSubmitButton"]>button{
        background: linear-gradient(135deg, rgba(12,157,69,.92), rgba(8,122,53,.92)) !important;
        -webkit-backdrop-filter: blur(12px); backdrop-filter: blur(12px);
        border:1px solid rgba(255,255,255,.55) !important; color:#fff !important; -webkit-text-fill-color:#fff !important;
        padding:.7rem 2.2rem !important; font-size:1rem !important; letter-spacing:.01em;
        box-shadow: 0 14px 30px -10px rgba(12,157,69,.65), inset 0 1px 0 rgba(255,255,255,.45) !important; }
    [data-testid="stForm"] [data-testid="stFormSubmitButton"]>button:hover{
        transform: translateY(-3px) scale(1.02); box-shadow: 0 20px 38px -10px rgba(12,157,69,.75), inset 0 1px 0 rgba(255,255,255,.5) !important; }
    [data-testid="stForm"] [data-testid="stFormSubmitButton"]>button p{ color:#fff !important; -webkit-text-fill-color:#fff !important; }

    /* ---- verdict pop-up (Detected Fraud / Safe) ---- */
    .verdict-overlay{ position:fixed; inset:0; z-index:99999; display:flex; align-items:center; justify-content:center;
        background:rgba(15,15,20,.28); -webkit-backdrop-filter: blur(10px); backdrop-filter: blur(10px);
        pointer-events:none; animation: overlayLife 4.2s ease both; }
    .verdict-modal{ position:relative; min-width:min(86vw,420px); text-align:center; padding:38px 44px 34px;
        border-radius:32px; color:#fff; border:1px solid rgba(255,255,255,.45);
        -webkit-backdrop-filter: blur(24px) saturate(180%); backdrop-filter: blur(24px) saturate(180%);
        animation: modalPop .7s cubic-bezier(.34,1.56,.64,1) both; }
    .verdict-modal.fraud{ background:linear-gradient(145deg, rgba(255,59,48,.88), rgba(160,20,20,.90));
        box-shadow: 0 30px 80px -10px rgba(255,59,48,.6), inset 0 1px 0 rgba(255,255,255,.4); }
    .verdict-modal.safe{ background:linear-gradient(145deg, rgba(12,180,80,.88), rgba(6,110,48,.92));
        box-shadow: 0 30px 80px -10px rgba(12,157,69,.6), inset 0 1px 0 rgba(255,255,255,.4); }
    .verdict-modal.review{ background:linear-gradient(145deg, rgba(255,159,10,.9), rgba(200,100,0,.92));
        box-shadow: 0 30px 80px -10px rgba(255,149,0,.55), inset 0 1px 0 rgba(255,255,255,.4); }
    .verdict-icon{ width:92px; height:92px; margin:0 auto 16px; border-radius:50%; background:rgba(255,255,255,.2);
        border:1px solid rgba(255,255,255,.5); display:flex; align-items:center; justify-content:center; position:relative; }
    .verdict-icon .vring{ position:absolute; inset:-10px; border-radius:50%; border:3px solid rgba(255,255,255,.7);
        animation: pulseRing 1.2s ease-out 3; }
    .verdict-icon svg{ animation: iconBounce .9s .25s cubic-bezier(.34,1.56,.64,1) both; }
    .verdict-modal p.verdict-title{ font-size:2.4rem !important; line-height:1.15 !important; font-weight:800 !important;
        letter-spacing:-0.02em; margin:0 !important; color:#fff !important; }
    .verdict-modal .verdict-sub{ margin-top:6px; font-size:.95rem; color:rgba(255,255,255,.88) !important; }
    .verdict-modal.fraud{ animation: modalPop .7s cubic-bezier(.34,1.56,.64,1) both, shake .5s .7s ease both; }

    @keyframes overlayLife{ 0%{opacity:0; visibility:visible;} 8%{opacity:1;} 82%{opacity:1;} 100%{opacity:0; visibility:hidden;} }
    @keyframes modalPop{ 0%{opacity:0; transform:scale(.55) translateY(30px);} 100%{opacity:1; transform:scale(1) translateY(0);} }
    @keyframes iconBounce{ 0%{transform:scale(0) rotate(-25deg);} 100%{transform:scale(1) rotate(0);} }
    @keyframes shake{ 0%,100%{transform:translateX(0);} 20%{transform:translateX(-9px);} 40%{transform:translateX(8px);} 60%{transform:translateX(-5px);} 80%{transform:translateX(3px);} }

    [data-testid="stDataFrame"], [data-testid="stTable"]{ border-radius: var(--radius-md); overflow:hidden;
        border:1px solid var(--border); box-shadow: var(--shadow-sm); }
    #MainMenu, footer, header{ visibility:hidden; }
    </style>
    """, unsafe_allow_html=True)


def badge_html(kind: str, meta: dict, key: str, pulse: bool = True) -> str:
    m = meta.get(key, {"label": key, "color": "#6e6e73", "icon": "sparkle"})
    ring = f'<span class="ring" style="color:{m["color"]}"></span>' if pulse else ""
    return (f'<span class="badge pop-in" style="background:{m.get("tint", m["color"] + "1A")}; '
            f'color:{m["color"]}">{ring}{icon(m["icon"], 15, m["color"])} {m["label"]}</span>')


def verdict_popup(verdict: str, case_id: str) -> None:
    kind, title, sub, ic = {
        "fraud": ("fraud", "Detected Fraud", "This transaction shows fraud indicators", "alert"),
        "legitimate": ("safe", "Safe", "This transaction looks legitimate", "shield"),
    }.get(verdict, ("review", "Needs Review", "Evidence is inconclusive", "help"))
    st.markdown(f"""
    <div class="verdict-overlay">
      <div class="verdict-modal {kind}">
        <div class="verdict-icon"><span class="vring"></span>{icon(ic, 46, '#ffffff', 2.2)}</div>
        <p class="verdict-title">{title}</p>
        <div class="verdict-sub">{case_id}</div>
        <div class="verdict-sub">{sub}</div>
      </div>
    </div>""", unsafe_allow_html=True)


def stat_grid(items: list[tuple[str, str, str]]) -> None:
    """items: list of (icon_name, label, value)."""
    cards = "".join(
        f'<div class="stat-card" style="animation-delay:{i*0.04}s">'
        f'<div class="stat-label">{icon(name, 13)} {label}</div>'
        f'<div class="stat-value">{value}</div></div>'
        for i, (name, label, value) in enumerate(items)
    )
    st.markdown(f'<div class="stat-grid">{cards}</div>', unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Case rendering
# ---------------------------------------------------------------------------

def render_evidence_table(evidence: list[dict]) -> None:
    if not evidence:
        st.caption("No evidence recorded.")
        return
    df = pd.DataFrame(evidence)
    df["entity_ids"] = df["entity_ids"].apply(lambda ids: ", ".join(str(i) for i in ids[:6])
                                                + (f" (+{len(ids) - 6} more)" if len(ids) > 6 else ""))
    st.dataframe(df[["source", "claim", "ref", "entity_ids"]], use_container_width=True, hide_index=True)


def render_actions(actions: list[dict]) -> None:
    if not actions:
        st.caption("No actions.")
        return
    for a in actions:
        rm = ROUTE_META.get(a["route"], {"label": a["route"], "color": "#6e6e73", "icon": "sparkle"})
        st.markdown(f"""
        <div class="action-card">
          <div>
            <div class="action-name">{a['action'].replace('_', ' ').title()}</div>
            <div class="action-reason">{a['reason']}</div>
          </div>
          <span class="badge" style="background:{rm['color']}1A; color:{rm['color']}; white-space:nowrap;">
            {icon(rm['icon'], 13, rm['color'])} {rm['label']}
          </span>
        </div>
        """, unsafe_allow_html=True)


def render_case(case_id: str, answer: dict) -> None:
    c = answer["case"]
    vmeta = VERDICT_META.get(c["verdict"], {"label": c["verdict"], "color": "#6e6e73", "tint": "#6e6e731A", "icon": "sparkle"})

    st.markdown(f"""
    <div style="display:flex; align-items:center; gap:12px; margin:4px 0 2px;">
      <h3 style="margin:0;">{case_id}</h3>
      {badge_html('verdict', VERDICT_META, c['verdict'])}
    </div>
    """, unsafe_allow_html=True)

    stat_grid([
        ("activity", "Fraud probability", f"{c['fraud_probability']:.2f}"),
        ("dollar", "Exposure", f"${c['exposure_usd']:.2f}"),
        ("tag", "Pattern", c["pattern"].replace("_", " ")),
        ("flag", "Status", STATUS_LABEL.get(c["status"], c["status"])),
        ("file", "SAR filed", "Yes" if answer["sar"]["file"] else "No"),
    ])

    st.markdown(f'<div class="quote-card">{c["summary"]}</div>', unsafe_allow_html=True)

    if c["pattern"] == "undocumented" and c["pattern_description"]:
        st.markdown(f"""
        <div class="card" style="border-left:3px solid var(--warning);">
          <b>{icon('sparkle', 15, '#ff9500')} Undocumented pattern</b>
          <p style="margin:6px 0 0; font-size:.88rem; color:var(--ink-soft);">{c['pattern_description']}</p>
        </div>""", unsafe_allow_html=True)

    if c["connected_card_ids"]:
        chips = "".join(f'<span class="chip">{icon("link", 12)} {cc}</span>' for cc in c["connected_card_ids"])
        st.markdown(f'<div style="margin:4px 0 6px;"><b style="font-size:.85rem;">Connected cards (shared origin)</b><br>{chips}</div>',
                    unsafe_allow_html=True)
    if c["connected_device_profiles"]:
        st.caption(f"Shared device profile(s): {', '.join(c['connected_device_profiles'])}")

    tab_evidence, tab_actions, tab_sar = st.tabs(
        ["Evidence & case memory", "Next-best-action", "SAR"])

    with tab_evidence:
        st.markdown(f"**{icon('database', 14)} Evidence**", unsafe_allow_html=True)
        render_evidence_table(c["evidence"])
        if c["similar_prior_cases"]:
            st.markdown("**Similar prior cases (case memory retrieved)**")
            chips = "".join(f'<span class="chip">{cid}</span>' for cid in c["similar_prior_cases"])
            st.markdown(chips, unsafe_allow_html=True)
        st.markdown('<hr class="divider-soft">', unsafe_allow_html=True)
        st.markdown("**Evidence requests**")
        if answer["evidence_requests"]:
            for er in answer["evidence_requests"]:
                st.markdown(f'<span class="chip">{er["type"]}</span> after step {er["asked_after_step"]}: '
                             f'{er["assumed_response"]}', unsafe_allow_html=True)
        else:
            st.caption("None requested — initial evidence was sufficient to decide.")
        st.caption(f"{icon('chip', 12)} tool_calls={answer['tool_calls']} · tokens={answer['tokens']} · "
                   f"latency={answer['latency_s']}s", unsafe_allow_html=True)

    with tab_actions:
        col_before, col_after = st.columns(2)
        with col_before:
            st.markdown("##### Before evidence gathering")
            render_actions(answer["next_best_actions"]["initial"])
        with col_after:
            st.markdown("##### After evidence gathering")
            render_actions(answer["next_best_actions"]["final"])
        st.markdown('<hr class="divider-soft">', unsafe_allow_html=True)
        st.markdown(f'<div class="quote-card">{icon("sparkle", 14)} <b>What changed:</b> '
                     f'{answer["next_best_actions"]["what_changed"]}</div>', unsafe_allow_html=True)

    with tab_sar:
        sar = answer["sar"]
        if not sar["file"]:
            pass
        else:
            st.markdown(f"""
            <div class="card" style="border-left:3px solid var(--danger);">
              <b style="color:var(--danger);">{icon('file', 15, '#ff3b30')} Suspicious Activity Report filed</b>
              <p style="margin:8px 0 2px; font-size:.85rem;"><b>Reason:</b> {sar['reason']}</p>
              <p style="margin:2px 0; font-size:.85rem;"><b>Subjects:</b> {', '.join(sar['subjects'])}</p>
              <p style="margin:2px 0; font-size:.85rem;"><b>Total amount:</b> ${sar['total_amount_usd']:.2f}</p>
              <p style="margin:2px 0 10px; font-size:.85rem;"><b>Activity dates:</b> {sar['activity_dates'][0]} to {sar['activity_dates'][1]}</p>
              <b style="font-size:.85rem;">Narrative</b>
              <p style="margin:6px 0 0; font-size:.85rem; color:var(--ink-soft); line-height:1.55;">{sar['narrative']}</p>
            </div>""", unsafe_allow_html=True)



def render_overview(cases: dict[str, dict]) -> None:
    rows = []
    for case_id, a in cases.items():
        c = a["case"]
        rows.append({
            "case_id": case_id, "verdict": c["verdict"], "pattern": c["pattern"],
            "probability": c["fraud_probability"], "exposure_usd": c["exposure_usd"],
            "sar_filed": a["sar"]["file"], "status": c["status"],
            "connected_cards": len(c["connected_card_ids"]),
        })
    df = pd.DataFrame(rows)

    stat_grid([
        ("chip", "Cases", str(len(df))),
        ("alert", "Fraud", str(int((df["verdict"] == "fraud").sum()))),
        ("shield", "Legitimate", str(int((df["verdict"] == "legitimate").sum()))),
        ("file", "SARs filed", str(int(df["sar_filed"].sum()))),
    ])

    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"**{icon('activity', 14)} Verdicts**", unsafe_allow_html=True)
        st.bar_chart(df["verdict"].value_counts(), color="#0c9d45")
    with c2:
        st.markdown(f"**{icon('tag', 14)} Patterns**", unsafe_allow_html=True)
        st.bar_chart(df["pattern"].value_counts(), color="#1d1d1f")

    st.markdown(f"**{icon('database', 14)} All cases**", unsafe_allow_html=True)
    st.dataframe(
        df.sort_values("probability", ascending=False),
        use_container_width=True, hide_index=True,
        column_config={
            "probability": st.column_config.ProgressColumn("probability", min_value=0, max_value=1),
            "exposure_usd": st.column_config.NumberColumn("exposure_usd", format="$%.2f"),
        },
    )


def render_new_investigation() -> None:
    if not has_processed_data():
        st.error("data/processed/*.pkl not found. Run `python data_ingest/build_index.py` first.")
        return

    idx = load_data_index()

    # A plain st.button() only submits on click -- it ignores Enter pressed
    # inside the text field, which reads as "I typed an ID and nothing
    # happened" for anyone used to a search-box. st.form() makes Enter in
    # any of its widgets submit the form, same as clicking the button.
    with st.form("new_investigation_form", clear_on_submit=False):
        col1, col2 = st.columns(2)
        with col1:
            txn_id = st.text_input("Transaction ID", placeholder="e.g. 3514030", key="live_txn_id")
        with col2:
            trigger_type = st.selectbox("Trigger", ["risk_score", "customer_report", "analyst_request"])
        trigger_text = st.text_area("Trigger text", value="Manually triggered from the analyst dashboard.")
        submitted = st.form_submit_button("Investigate", type="primary")

    if submitted:
        txn_id = (txn_id or "").strip()
        if not txn_id or not txn_id.isdigit():
            st.error("Enter a numeric transaction ID.")
            return
        txn = idx.get_transaction(int(txn_id))
        if txn is None:
            st.error(f"Transaction {txn_id} not found in the dataset.")
            return

        from agent.investigate import investigate

        case_row = pd.Series({
            "case_id": f"LIVE-{txn_id}",
            "flagged_txn_id": int(txn_id),
            "card_id": txn["card_id"],
            "customer_id": txn["customer_id"],
            "trigger_type": trigger_type,
            "trigger_text": trigger_text,
            "risk_score": float(txn["risk_score"]) if trigger_type == "risk_score" else None,
        })
        with st.spinner("Investigating… pulling card history, checking devices, retrieving case memory"):
            answer = investigate(case_row, idx, [])

        verdict_popup(answer["case"]["verdict"], answer["case_id"])
        st.toast("Investigation complete", icon="✅")
        st.markdown(f"""
        <div class="success-flash">
          <div class="burst"><span class="ring"></span>{icon('check', 20, '#fff')}</div>
          <div>
            <div style="font-weight:800; font-size:1.02rem;">Investigation complete</div>
            <div style="color:var(--ink-soft); font-size:.83rem;">
              {answer['latency_s']}s &middot; {answer['tool_calls']} graph query calls &middot; case {answer['case_id']}
            </div>
          </div>
        </div>""", unsafe_allow_html=True)
        render_case(answer["case_id"], answer)


def main() -> None:
    inject_theme()

    st.markdown(f"""
    <div class="hero">
      <div class="hero-icon">{icon('search', 22, '#ffffff', 2.2)}</div>
      <div>
        <p class="hero-title">TigerGraph Agentic Fraud Investigation</p>
        <p class="hero-sub">HHGOA case pack &middot; 20 graded cases investigated against the full IEEE-CIS-derived
        transaction graph under Fraud Policy v1.0</p>
      </div>
    </div>
    """, unsafe_allow_html=True)

    cases = load_cases()
    if not cases:
        st.error("No answer files found in cases/. Run `python run_case_pack.py` first.")
        return

    tab_overview, tab_detail, tab_new = st.tabs(["Overview", "Case detail", "New investigation"])

    with tab_overview:
        render_overview(cases)

    with tab_detail:
        options = list(cases.keys())
        labels = {cid: f"{cid} — {VERDICT_META.get(cases[cid]['case']['verdict'], {}).get('label', '')}"
                  for cid in options}
        selected = st.selectbox("Case", options, format_func=lambda cid: labels[cid])
        render_case(selected, cases[selected])

    with tab_new:
        render_new_investigation()


if __name__ == "__main__":
    main()

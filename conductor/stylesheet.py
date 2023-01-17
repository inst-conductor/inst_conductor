def get_master_stylesheet(platform, theme):
    """Get the top-level style sheet for the entire application."""
    return f"""
QGroupBox {{
    border: 2px solid gray;
    border-radius: 9px;
    margin-top: 0.5em;
    padding-top: 0.25em;
    font-size: 12px;
    font-weight: bold;
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 3px 0 3px;
}}
"""

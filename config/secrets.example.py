"""Copy to config/secrets.py (gitignored) or set the env var instead.

    export GOOGLE_PLACES_KEY="..."          # preferred
    # or: cp config/secrets.example.py config/secrets.py and fill it in

The key must be restricted in Cloud Console to the Places API only. An
unrestricted key is a billing liability the moment it leaks.
"""
GOOGLE_PLACES_KEY = ""

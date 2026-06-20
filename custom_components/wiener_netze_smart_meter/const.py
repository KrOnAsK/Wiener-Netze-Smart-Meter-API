DOMAIN = "wiener_netze_smart_meter"

CONF_CLIENT_ID = "client_id"
CONF_CLIENT_SECRET = "client_secret"
CONF_API_KEY = "api_key"

UPDATE_INTERVAL_HOURS = 12

# Days of quarter-hour history to pull on first run (no statistics yet).
# Raise for more historical backfill on the Energy dashboard.
BACKFILL_DAYS = 30

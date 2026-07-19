import json
from urllib.request import urlopen
from urllib.parse import urlencode
import os

KEY = os.environ["GOOGLE_API_KEY"]
RATUSZ = "52.408528,16.934544"

# 1. Геокодируем Widokowa 45, Komorniki
geo_url = "https://maps.googleapis.com/maps/api/geocode/json?" + urlencode({
    "address": "Widokowa 45, Komorniki, Polska",
    "key": KEY, "language": "pl", "region": "pl"
})
geo = json.loads(urlopen(geo_url).read())
loc = geo["results"][0]["geometry"]["location"]
origin = f"{loc['lat']},{loc['lng']}"
print(f"Coords: {origin}")
print(f"Address: {geo['results'][0]['formatted_address']}")

# 2. Distance Matrix → Ratusz
dm_url = "https://maps.googleapis.com/maps/api/distancematrix/json?" + urlencode({
    "origins": origin,
    "destinations": RATUSZ,
    "mode": "driving",
    "key": KEY, "language": "pl",
    "departure_time": "now"
})
dm = json.loads(urlopen(dm_url).read())
el = dm["rows"][0]["elements"][0]
print(f"Расстояние: {el['distance']['text']}")
print(f"Время (без пробок): {el['duration']['text']}")
if "duration_in_traffic" in el:
    print(f"Время (с пробками): {el['duration_in_traffic']['text']}")

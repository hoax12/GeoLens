import asyncio
import os
import requests
from dotenv import load_dotenv

load_dotenv()

def test_apis():
    city = "Tokyo"
    time_state = "day"
    
    print("Testing GNews API...")
    news_key = os.getenv("GNEWS_API_KEY")
    if news_key:
        url = f"https://gnews.io/api/v4/search?q={city} local&lang=en&max=3&apikey={news_key}"
        resp = requests.get(url, timeout=5)
        print(f"GNews Status Code: {resp.status_code}")
        if resp.status_code == 200:
            print("GNews Output:", resp.json().get("articles", [])[:1]) # print just 1
        else:
            print("GNews Error:", resp.text)
    else:
        print("No GNews API Key")

    print("\nTesting Foursquare API...")
    fs_key = os.getenv("FOURSQUARE_API_KEY")
    if fs_key:
        query = "cafe" if time_state == "day" else "restaurant"
        url = f"https://api.foursquare.com/v3/places/search?near={city}&query={query}&limit=3"
        headers = {"Authorization": fs_key, "accept": "application/json"}
        resp = requests.get(url, headers=headers, timeout=5)
        print(f"Foursquare Status Code: {resp.status_code}")
        if resp.status_code == 200:
            results = resp.json().get("results", [])
            if results:
                print("Foursquare Output (first):", results[0].get("name"))
            else:
                print("No results found.")
        else:
            print("Foursquare Error:", resp.text)
    else:
        print("No Foursquare API Key")

if __name__ == "__main__":
    test_apis()

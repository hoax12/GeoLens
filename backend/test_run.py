import asyncio
from graph import run_graph
import json

async def main():
    print("Testing GeoLens Agents for Tokyo (Day mode)...")
    try:
        result = await run_graph("Tokyo", "day")
        print("Done. Writing to out.json")
        with open("out.json", "w") as f:
            json.dump(result, f, indent=2)
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())

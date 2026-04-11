import os
import asyncio
from dotenv import load_dotenv
from langchain_groq import ChatGroq

load_dotenv()

async def test_groq():
    try:
        llm = ChatGroq(model="gemma-4-e4b")
        res = await llm.ainvoke("Say hello")
        print("Success:", res.content)
    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    asyncio.run(test_groq())

import asyncio
import websockets
import json

async def test():
    async with websockets.connect("wss://stream.binance.com:9443/ws/btcusdt@bookTicker") as ws:
        for _ in range(3):
            res = await asyncio.wait_for(ws.recv(), timeout=5.0)
            print("Recv:", res[:200])

asyncio.run(test())

"""Rough load test: many new chats at once. Measures how fast Qiscus gets its reply.

Start the CRM (port 8001) and the middleware (port 8000) first, then from the project folder:
    python -m tools.load_test 300 50      # 300 chats, 50 at a time
Numbers depend on the machine. It empties both the CRM and the middleware first.
"""
import asyncio
import statistics
import sys
import time

import httpx

from tests.samples import make_webhook

MIDDLEWARE, CRM = "http://127.0.0.1:8000", "http://127.0.0.1:8001"


async def main(total: int, at_once: int):
    async with httpx.AsyncClient(timeout=30) as client:
        await client.post(f"{CRM}/mock/reset")
        await client.post(f"{MIDDLEWARE}/admin/reset")
        gate, reply_times = asyncio.Semaphore(at_once), []

        async def one(i):
            webhook = make_webhook(5000 + i, user_id=f"601{20000000 + i}")
            async with gate:
                start = time.perf_counter()
                r = await client.post(f"{MIDDLEWARE}/webhooks/qiscus", json=webhook)
                reply_times.append(time.perf_counter() - start)
                return r.status_code

        start = time.perf_counter()
        codes = await asyncio.gather(*(one(i) for i in range(total)))
        answered = time.perf_counter() - start
        while True:
            events = (await client.get(f"{MIDDLEWARE}/admin/events")).json()
            if not any(e["status"] in ("received", "processing") for e in events):
                break
            await asyncio.sleep(0.2)
        finished = time.perf_counter() - start
        leads = len((await client.get(f"{CRM}/mock/leads")).json())

    reply_times.sort()
    print(f"{total} chats, {at_once} at a time: all answered in {answered:.1f}s, status codes {sorted(set(codes))}")
    print(f"reply to Qiscus: median {statistics.median(reply_times) * 1000:.0f} ms, "
          f"95th percentile {reply_times[int(0.95 * total) - 1] * 1000:.0f} ms, worst {reply_times[-1] * 1000:.0f} ms")
    print(f"all {leads} leads in the CRM after {finished:.1f}s")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 300, int(sys.argv[2]) if len(sys.argv) > 2 else 50))

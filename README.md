# AutoSmart: Qiscus to CRM lead integration

A small service that receives Qiscus Omnichannel Chat **New Session** webhooks and creates a lead in AutoSmart's CRM for each new conversation, with the phone number in E.164, no duplicate leads, and no crashes when the CRM is slow or down. A mock of AutoSmart's CRM and a leads page are included so everything runs locally.

```
Customer -> Qiscus -> (webhook) -> middleware -> (POST /api/leads) -> AutoSmart CRM
                                       |
                                  own logbook (SQLite)
```

## 1. Install and run (about 5 minutes)

Needs **Python 3.10 or newer** (built and tested on 3.13 and 3.14).

```bash
git clone <this repo>
cd autosmart-integration
python -m venv .venv
# Windows:  .venv\Scripts\Activate.ps1      macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt
```

Start the two services, each in its own terminal (activate the venv in each):

```bash
uvicorn mock_crm.app:app --port 8001        # mock AutoSmart CRM
uvicorn middleware.main:app --port 8000     # the integration middleware
```

Then, in a third terminal:

```bash
python demo.py          # plays Qiscus end to end, prints PASS/FAIL, about 1 minute
python -m pytest -q     # 41 unit and integration tests, about 1 second
```

While the demo runs, open:

- http://127.0.0.1:8001/leads : leads in the mock CRM (one row per customer)
- http://127.0.0.1:8000/review : every webhook the middleware received and what happened to it

Qiscus would be configured to send its New Session Webhook to `POST http://<host>:8000/webhooks/qiscus`.

To send one webhook by hand:

```bash
curl -X POST http://127.0.0.1:8000/webhooks/qiscus -H "Content-Type: application/json" -d @sample_webhook.json
```

(any body shaped like Appendix A works; `tests/samples.py` holds the Appendix A sample)

## 2. What is in the repo

| Path | What it does |
|---|---|
| `middleware/main.py` | Receives the webhook, stores it, replies to Qiscus at once, hands the rest to the processor |
| `middleware/store.py` | The middleware's own logbook (SQLite). The webhook's stamp is the primary key, so a repeat is refused in one step |
| `middleware/mapping.py` | Turns the Appendix A webhook into the Appendix B lead (R1) |
| `middleware/phone.py` | Phone numbers to E.164 for Malaysia, Singapore and Indonesia (R2) |
| `middleware/processor.py` | Works on a stored webhook after the reply: parks bad data, applies the returning-customer rule, calls the CRM with retries (R3, R4) |
| `middleware/crm_client.py` | One call to the CRM, and what its answer means |
| `mock_crm/app.py` | Mock CRM following Appendix B, including slow replies, 500, 503, 429, no duplicate check, and the leads page |
| `demo.py` | The R5 proof: sends every case to the running services and prints PASS/FAIL |
| `tests/` | Unit tests for phones and mapping, and integration tests against an in-test fake CRM |
| `tools/load_test.py` | Rough load test: many webhooks at once, measures the reply time to Qiscus |
| `try_crm.py` | Pokes the mock CRM by hand to see each behaviour |
| `CLIENT_GUIDE.md` | One-page guide for AutoSmart's IT team (B3) |

## 3. How it works

1. **Store, then reply.** Qiscus waits up to 5 seconds and may resend the same webhook if it gets no 2xx. The CRM can take up to 10 seconds. So the middleware never calls the CRM before replying: it writes the webhook to its logbook and answers Qiscus straight away (well under a second), then calls the CRM in the background.
2. **One stamp per webhook.** The stamp is `room id : message id`. It is the logbook's primary key, so a redelivered webhook, even five copies arriving at the same instant, is stored once and creates one lead.
3. **Translate.** `wa`, `fb`, `ig` become `whatsapp`, `facebook`, `instagram`, anything else `other`. The source is read from `room_log.source`, falling back to the JSON string in `payload.room.options`. For a photo or file, the caption is used as the first message, never the `[file] url [/file]` placeholder. Name is cut to 100 characters and the message to 1,000, the CRM's limits.
4. **Call the CRM, retrying only when it is safe.**

| CRM answer | Meaning | What the middleware does | Status in the logbook |
|---|---|---|---|
| 201 | Created | Saves the lead id | `sent` |
| 400, 401 | Our data or settings are wrong | No retry, logged as an error | `needs_attention` |
| 429 | Rate limited | Waits the `Retry-After` seconds, retries | `sent` or `failed` |
| 500, 503 | Temporary error | Waits 1, 2, 4 s (or `Retry-After`), retries | `sent` or `failed` |
| Can't connect | CRM down, nothing sent | Retries as above | `sent` or `failed` |
| No answer within 15 s | Sent, but we don't know if it was created | **No automatic retry**, a person checks the CRM first | `unconfirmed` |

After 4 attempts the event is parked as `failed` and can be resent with `POST /admin/replay` (all failed events) or `POST /admin/replay?key=<stamp>` (one event). Nothing is ever dropped silently, and every retry and give-up is one log line.

5. **Start-up recovery.** If the service stops between replying and finishing, then on restart any event still `received` is processed, and any event that was mid-way (`processing`) is marked `unconfirmed`, because the lead may already exist.
6. **Protecting the CRM.** At most 10 CRM calls run at once (`CRM_MAX_CONCURRENT`). A burst of chats queues in the logbook instead of hitting AutoSmart's CRM all at the same moment.

## 4. Design decisions and assumptions

### Answers to section 3.3

**A returning customer starts a new session weeks later: new lead or not?**
No new lead if the same customer (same channel and same WhatsApp number or platform id) already got a lead within the last **30 days**. The event is linked to the existing lead instead (status `returning_customer`). After 30 days a new lead is created, because by then it is most likely a new buying interest that sales should follow up as such. The window is a setting (`RETURNING_WINDOW_DAYS`) since the right number depends on AutoSmart's sales cycle, which I'd confirm with their sales team. Per Qiscus's documentation the New Session Webhook fires on a customer's first message or on a new message after the previous room was resolved, so "a new session weeks later" means an agent closed the earlier chat.

**What happens to a lead whose phone number can't be normalised?**
It is never guessed and never dropped. For WhatsApp, where the CRM requires a phone, the event is parked as `needs_attention` with the exact text that arrived, no lead is sent, and a person corrects it and resends it. For Facebook and Instagram the lead is created without a phone, because that field holds a platform id, not a phone. The customer is not left unanswered in either case: the conversation itself is in the Qiscus inbox and an agent replies there. Only the CRM record waits.

A number that is invalid for the default country but valid in another country AutoSmart serves (for example `0812 0000 0009`, valid in Indonesia but not Malaysia) is marked ambiguous rather than guessed, since a wrong guess sends a sales rep to the wrong market. With per-country WhatsApp lines, the channel would tell us the country (see section 7).

**What does the service return to Qiscus when the CRM is down?**
`200` as soon as the webhook is safely in the logbook. The reply tells Qiscus that we have received the webhook, not that the CRM has the lead, and the middleware owns the retrying. Returning an error would only make Qiscus resend a body we already hold. The service returns `503` only if it cannot store the webhook itself, which is the one case where a resend from Qiscus helps.

### Other decisions

- **Phone numbers:** Google's `phonenumbers` library, not hand-written patterns. A number written with `+` keeps its stated country. Without `+` it is read first as country code plus number (how WhatsApp sends it, e.g. `60123456781`), then as a local number in the default country (`DEFAULT_PHONE_REGION`, Malaysia). Only valid numbers in Malaysia, Singapore or Indonesia are accepted.
- **CRM timeout of 15 seconds**, above the CRM's 10 second worst case, so a slow but successful call is seen as a success.
- **Storage:** SQLite, one file, no install. It survives restarts, which an in-memory store would not, and that matters for the duplicate check and the returning-customer rule. It suits one running instance. Production would use a managed database and a queue (section 7).
- **Unknown sources** (`other`): a phone is sent only if the id is a valid phone number.

### Assumptions I couldn't verify

- **503 and 429 mean no lead was created.** Appendix B says a slow call still creates the lead, but it doesn't say this for errors. I assumed errors are safe to retry. I'd confirm it with AutoSmart's CRM team, and ask whether the CRM can look up or enforce uniqueness on `external_ref`, which would let `unconfirmed` events be settled automatically.
- **Redelivery sends an identical body.** As stated in Appendix A. **How I'd verify it:** the Qiscus dashboard's outgoing webhook logs, a question to the Qiscus platform team about retry timing and count, and a daily reconciliation comparing new sessions in Qiscus with leads in the CRM.
- **Mock CRM choices:** a "slow" reply always takes the full 10 seconds (the worst case, set by `MOCK_SLOW_SECONDS`), and a request without `Content-Type: application/json` gets `400`, since Appendix B lists the header but doesn't say what happens without it.

### Load

I ran `tools/load_test.py` against both services on one machine. The first version slowed down as load grew: with 100 webhooks at a time the median reply to Qiscus was about 3.5 s, close to Qiscus's 5 s limit. Two changes fixed it: logbook writes moved off the main event loop, and CRM calls capped at 10 at a time. After that, 1,000 webhooks at 100 at a time gave a median reply of about 1.2 s and a worst of about 2.2 s, with all 1,000 leads created and no duplicates. Numbers depend on the machine (`python -m tools.load_test 300 50` to repeat). The real limits for AutoSmart are their chat volume at peak times and the CRM's own rate limit, which I'd ask for during discovery.

## 5. Settings

| Setting | Default | Used by |
|---|---|---|
| `CRM_URL` | `http://127.0.0.1:8001` | middleware |
| `CRM_API_TOKEN` | `test-token` | both (must match) |
| `CRM_TIMEOUT_SECONDS` | `15` | middleware |
| `CRM_MAX_ATTEMPTS` | `4` | middleware |
| `CRM_BACKOFF_SECONDS` | `1` (then 2, 4) | middleware |
| `CRM_MAX_CONCURRENT` | `10` | middleware |
| `RETURNING_WINDOW_DAYS` | `30` | middleware |
| `DEFAULT_PHONE_REGION` | `MY` | middleware |
| `MIDDLEWARE_DB` | `middleware.db` | middleware |
| `ALLOW_ADMIN_RESET` | `1` | middleware (set `0` outside a demo) |
| `MOCK_SLOW_SECONDS` | `10` | mock CRM |
| `MOCK_RETRY_AFTER` | `3` | mock CRM |
| `MOCK_RANDOM_FAILURE_RATE` | `0` (e.g. `0.3` for random misbehaviour) | mock CRM |

The mock CRM can also be told to misbehave per request with the header `X-Mock-Scenario: slow | error | server_error | rate_limit`, or for the next N requests with `POST /mock/scenario {"scenario": "slow", "count": 1}`.

## 6. Known limits of this version

- **Security.** The admin endpoints (`/admin/reset`, `/admin/replay`, `/admin/events`, `/review`) have no authentication because this runs locally for review. Reset can be switched off with `ALLOW_ADMIN_RESET=0`. Before going live I'd put them behind an admin token, protect the webhook address with a secret token, confirm with the Qiscus platform team whether the New Session Webhook can be signed and verify that signature, serve everything over HTTPS, and agree with AutoSmart where customer data may be stored, how long it is kept and how it is encrypted, since it covers customers in Malaysia, Singapore and Indonesia.
- **Replay counter.** A replayed event's attempt count starts again from 1. Earlier attempts are still in the log.
- **The mock CRM's leads page** highlights rows that share a phone. A returning customer who comes back after the 30-day window gets a second lead on purpose, so those rows show as highlighted.
- **One instance only.** Two webhooks for the same returning customer arriving at the same moment could both create a lead, and SQLite can't be shared across servers.

## 7. What I'd improve with more time

- The security items in section 6.
- A shared database and a proper queue (for example Postgres with SQS or Redis), so the service can run on several servers and absorb larger bursts.
- A review screen where a person corrects a parked phone and resends in one click, instead of using the replay endpoint.
- A table mapping each WhatsApp line or Facebook page to its country, so the default country for phone numbers comes from the channel the customer wrote to.
- Write the CRM lead id back into the Qiscus chat sidebar (Qiscus's Set User Info API), so agents see the lead while chatting, and close the lead when the chat is resolved.
- Alerts when events wait in `needs_attention`, `unconfirmed` or `failed` for too long.
- A CRM lookup by `external_ref`, if AutoSmart can provide one, to settle `unconfirmed` events automatically.

## 8. Bonus tasks

- **B1, retry and dead-letter: done.** Retries for 5xx, 429 and connection failures with backoff and `Retry-After`. Events that still fail are kept as `failed` and resent with `/admin/replay`. I chose it because without it, a CRM outage would mean lost leads, which is the exact problem AutoSmart came with.
- **B2, docker compose: skipped.** I'm not yet at the level where I could explain and support a Docker setup properly, and I'd rather submit something I can explain line by line. The setup above takes about five minutes.
- **B3, client integration guide: done**, see `CLIENT_GUIDE.md`. I chose it because explaining the integration to the client's IT team, what they need to provide and how to troubleshoot it, is the part of this work a TAM owns day to day.

## 9. How I used AI

**Tools.** Claude, through the Claude desktop app connected to my project folder and GitHub. I used it to explain concepts I was new to (webhooks, status codes, idempotency, E.164), plan the work across the five days, draft and test the code file by file, and draft this README. I ran every test and the demo on my own machine, reviewed each piece, and made the design decisions above.

**Prompts that helped most** (as I wrote them):

1. *"is appendix B covered exactly as stated regarding the json request and response in the exact order of the json they gave?"* This made the AI audit the mock CRM against the spec line by line.
2. *"check online for verified sources on how this can be handled, because if i reject the lead doesnt it mean nobody will respond back to the user? and company ends up losing customer because of this?"* This turned "reject bad numbers" into "park them, never drop them", using the dead-letter pattern.
3. *"how do we test latency for a larger number of inputs coming in, how do we handle that overload, will this be our system issue or cloud scaling issue"* This led to the load test and the two changes in section 4.

**Where the AI got it wrong, and how I caught it:**

- **The mock CRM didn't fully match Appendix B.** When I asked whether it matched the spec exactly, the review found three gaps: it didn't check `Content-Type`, it never returned `500`, and `503` came without `Retry-After`. All three were fixed and retested.
- **A file update reported success but never reached my machine.** After a fix, my page still behaved the old way. I said so, and checking the file on my machine showed the copy hadn't landed. Since then every file was checked against the tested version before I ran it.
- **"Reject" was the wrong default for bad phone numbers.** The first suggestion was to reject them. I pushed back that rejecting a lead could mean losing a customer, which led to the park-and-replay design and the point that the customer is still answered in Qiscus.
- **The first middleware slowed down under load.** It passed every functional test, but when I asked about volume, the load test showed replies to Qiscus creeping towards the 5 second limit. Two small changes fixed it.
- **Security was left out.** When I asked whether security was needed, the admin endpoints turned out to be unauthenticated, including one that deletes every stored event. Given the deadline I chose to document the limits and the fixes (section 6) rather than build them.

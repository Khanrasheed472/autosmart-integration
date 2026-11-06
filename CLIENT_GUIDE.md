# AutoSmart CRM integration: guide for the AutoSmart IT team

## What it does

When a customer starts a new conversation with AutoSmart on WhatsApp, Facebook Messenger or Instagram, Qiscus notifies a small integration service, which creates a lead in your CRM through `POST /api/leads` with the customer's name, phone number (WhatsApp only, in +60 / +65 / +62 format), channel, first message and the Qiscus room id as `external_ref`. Your sales team no longer needs to type chat leads into the CRM by hand.

```
Customer -> Qiscus inbox -> integration service -> your CRM
```

The service sends each conversation once, so a retried notification never creates a second lead, and the same customer coming back within 30 days is linked to their existing lead instead of creating a new one.

## What we need from you

| Item | Why |
|---|---|
| The CRM's base URL and an API token for the integration | To create leads (`Authorization: Bearer <token>`) |
| A test (sandbox) CRM environment and a contact who owns it | To test before go-live without touching real leads |
| Your CRM's rate limit, and what a 503 or 429 means | So retries stay within your limits, and to confirm that an error means no lead was created |
| Whether the CRM can look up a lead by `external_ref` or phone | Lets the service settle uncertain cases automatically |
| Where customer data may be hosted and how long it can be kept | Customers are in Malaysia, Singapore and Indonesia, each with its own data protection rules |
| Your preferred window for treating a returning customer as the same lead (default 30 days) | Matches your sales follow-up cycle |

Qiscus configures the New Session Webhook in the Qiscus dashboard to point at the service. No change is needed on the CRM side, since the CRM only receives standard API calls.

## How to check it is working

- `GET /health` on the service returns `{"ok": true}` when it is running.
- The review page (`/review`) lists every conversation received and what happened to it: green means a lead was created, amber means a person should check it, red means the CRM could not be reached after several tries.
- Each new lead in the CRM carries the Qiscus room id in `external_ref`, so any lead can be traced back to its conversation.

## Troubleshooting

| What you see | Likely cause | What to do |
|---|---|---|
| No leads arriving at all | Service down, or webhook not configured in Qiscus | Check `/health`, then ask Qiscus to confirm the webhook URL |
| Review page shows many `failed` | CRM was unreachable or returning errors | Once the CRM is back, resend them with `POST /admin/replay` |
| `needs_attention` with "CRM answered 401" | API token wrong or expired | Issue a new token and update the service setting |
| `needs_attention` with "WhatsApp phone is invalid" or "ambiguous" | The number could not be put into +60 / +65 / +62 format without guessing | The conversation is still answered in Qiscus. Correct the number and create or resend the lead |
| `unconfirmed` | The CRM did not answer in time, so the lead may or may not exist | Search the CRM for the room id in `external_ref`. If no lead is there, resend that one event |
| `returning_customer` | Same customer within the window | Expected: the conversation is linked to their existing lead |
| A lead you think is a duplicate | Same customer after the window, or the same person on two channels | Expected behaviour for now; the window and cross-channel matching can be tuned |

## Contacts

Integration owner (Qiscus TAM): _name and email_. AutoSmart CRM owner: _to be confirmed at kickoff_.

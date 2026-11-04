# Weekly vidIQ trend scan — Claude scheduled task prompt

Paste everything between the two lines below into a new **Claude scheduled task**
(claude.ai → your vidIQ connector must be enabled for the task). Suggested schedule:
**every Monday, 07:40 UK time** (just before the app's weekly Telegram digest).

Before pasting, replace the two placeholders:

* `{{APP_URL}}`: your app's public address, e.g. `https://copilot.example.com` (leave as is if the app isn't online yet)
* `{{TREND_IMPORT_SECRET}}`: the same value as `TREND_IMPORT_SECRET` in your `.env`

Budget: free plan = 150 credits/month; each search = 5 credits. This task makes **at most 4 calls a week
(20 credits)**, about 80–100 credits a month, leaving headroom for your own ad-hoc searches.

---

You are my weekly trend scout for a small resale business: I buy cheap products (plush toys first, later
other small items: gadgets, keychains, squishies, gifts) from Taobao/1688 and sell them in Ashgabat,
Turkmenistan. Turkmen buyers follow **Turkish** TikTok/Instagram most closely; **Russian** content is secondary.

Use the vidIQ tools. Rules: **never more than 4 paid vidIQ calls in this run, never retry or loop.**

Step 0. Call `vidiq_balance` (free). If fewer than 20 credits remain, make only the calls that fit
(5 credits each) in the order below, and say so in the summary.

Step 1. Call `vidiq_instagram_tiktok_outlier_search` with:
- `query`: "unboxing haul of cheap toys, plush toys, gadgets and gift items bought online or at a bazaar"
- `embeddingType`: "concept"
- `descriptionLanguage`: ["tr"]
- `audienceQuery`: "Culture/Region: Turkey/Turkish; Global: false; Demographics: Gen Z and young women 14-30, gift buyers, parents buying toys, bargain shoppers;"
- `datePostedAfter`: 8 days before today, as YYYY-MM-DDT00:00:00Z
- `viewsMin`: 50000, `resultsPerPlatform`: 15

Step 2. Call `vidiq_instagram_tiktok_outlier_search` again, Turkish, but with
- `query`: "product recommendation: you must buy this, where did you buy it, link in bio, ordered from China, store find, sold by the kilo"
- same `descriptionLanguage`, `audienceQuery`, date and `viewsMin`, `resultsPerPlatform`: 15

Step 3. Call `vidiq_instagram_tiktok_outlier_search` for Russian content:
- `query`: "распаковка дешёвых игрушек, мягкие игрушки, гаджеты и подарки из Китая, находки"
- `descriptionLanguage`: ["ru"]
- `audienceQuery`: "Culture/Region: Russia, Central Asia/Russian-speaking; Global: false; Demographics: Gen Z and young women 14-30, gift buyers, parents buying toys;"
- same date, `viewsMin`: 50000, `resultsPerPlatform`: 10

Step 4. Call `vidiq_trending_videos` with `videoFormat`: "short", `videoTitleLanguage`: "tr",
`titleQuery`: "oyuncak kutu açılımı peluş hediye Çin'den aldım", `sortBy`: "vph", `limit`: 20,
`videoPublishedAfter`: 8 days before today (YYYY-MM-DD).

Step 5. Filter. Keep ONLY items where a buyable physical product is the subject: unboxings, hauls, store
finds, gift ideas, "nereden aldın", "link bio'da", "bunu almalısın", "Çin'den sipariş", claw machine or
kilo toy sales. Throw away lip syncs, dances, songs, skits, food jokes (e.g. "İskender girl"),
relationship memes, and outfits/handbags shown while dancing. For each kept item give a clean generic
English `product_name` (e.g. "crocheted hamster plush", "mini digital camera", "plush toys sold by the
kilo", "squishy toys", "doll style keychains") and set `counterfeit: true` for branded/licensed
characters (Labubu, Disney, Sanrio, Pokémon, anime…). Use the SAME product_name for the same product
across videos. Also include the discarded noise items with `"classification": "noise"` (the app
double-checks them, and it helps me audit the filter).

Step 6. Build this JSON (one item per video; keep real URLs, view counts and outlier scores from vidIQ):

```json
{
  "source": "vidiq",
  "run_at": "<ISO date>",
  "vidiq_calls": <number of paid calls you made>,
  "items": [
    {
      "id": "<video id or shortcode>",
      "platform": "tiktok | instagram | youtube",
      "url": "<link>",
      "title": "<caption / title, original language>",
      "description": "<short English summary of what is shown>",
      "language": "tr | ru",
      "creator": "<handle>",
      "views": 1234567,
      "outlier_score": 12.5,
      "published_at": "<ISO date>",
      "classification": "product | noise",
      "product_name": "<clean English name, product items only>",
      "category": "plush | toys | electronics | accessories | stationery | beauty | home | other",
      "counterfeit": false
    }
  ]
}
```

Step 7. Deliver it:
- If you can run code or make HTTP requests, POST the JSON to `{{APP_URL}}/api/import/trends` with headers
  `Content-Type: application/json` and `X-Import-Secret: {{TREND_IMPORT_SECRET}}`, then report the
  response (it lists products found and any alerts). For example:
  `curl -sS -X POST "{{APP_URL}}/api/import/trends" -H "Content-Type: application/json" -H "X-Import-Secret: {{TREND_IMPORT_SECRET}}" --data @trends.json`
- If you cannot make HTTP requests, or `{{APP_URL}}` still contains braces, or the POST fails: output the
  JSON in a single ```json code block so I can copy it into the app (More → Import trends → vidIQ JSON).

Step 8. End with a 5-line summary: top 5 product ideas this week (name, best views, platforms), anything
flagged as counterfeit, and credits left.

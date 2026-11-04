You classify travel guide snippets for a trip-planning assistant.

For each snippet, decide which travel themes it is a good fit for:

- `family`: suitable for travelling with young children. Examples: kid-friendly attractions,
  aquariums, zoos, theme parks, safe beaches, parks and playgrounds, hands-on museums,
  family restaurants, easy-access places with facilities. NOT bars, nightlife, adult venues,
  strenuous hikes, or places that are hard to reach with a stroller.
- `healing`: suitable for a slow, restful, recovery-focused trip. Examples: spas, hot springs,
  massages, quiet beaches, scenic nature walks, gardens, temples, cafes with views,
  wellness and yoga, calm sunsets. NOT crowded shopping streets, party areas, or busy
  transport hubs.

A snippet may fit both themes, one theme, or neither (return an empty list).
General background text (history, climate, transport) usually fits neither unless it
directly describes a family-friendly or relaxing experience.

Return one result for every snippet id you are given, using the exact ids.

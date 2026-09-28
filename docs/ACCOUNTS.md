# Accounts: seed verification and proposed additions

All checks were run on 2026-09-28 against `api.bsky.app` with `squawk resolve` (handle
resolution via `com.atproto.identity.resolveHandle`, activity via `getAuthorFeed`;
"inactive" = no own posts in the last 14 days). Re-run any time:

```
uv run scripts/resolve_dids.py                   # report
uv run scripts/resolve_dids.py --check a.handle  # ad-hoc handles
```

## Seed accounts (verified)

| handle | resolved | last own post | verdict |
|---|---|---|---|
| bruceandy.bsky.social (Andy Bruce, Reuters UK economy) | yes | 28 Sep 2026 | tier 1 |
| tobyn.bsky.social (Toby Nangle, FT Alphaville) | yes | 28 Sep 2026 | tier 1 |
| iankmsmith.ft.com (Ian Smith, FT markets) | yes | 28 Sep 2026 | tier 1 |
| domw.bsky.social (Dom White, Absolute Strategy) | yes | 26 Sep 2026 | tier 1 |
| matthewholehouse.bsky.social (The Economist) | yes | 28 Sep 2026 | tier 1 |
| robin-j-brooks.bsky.social (Robin Brooks, Brookings) | yes | 28 Sep 2026 | tier 2 (global, high volume of charts) |
| hermgamma.bsky.social (Holger Binding) | yes | 27 Sep 2026 | tier 2 (mostly reposts; only 1 own post in the last 20 items) |
| reuters.com | yes | 28 Sep 2026 | tier 3 wire (~95 posts/day, mostly non-UK) |
| financialtimes.com | yes | 28 Sep 2026 | tier 3 wire (~45 posts/day) |
| cityam.com.web.brid.gy (Bridgy Fed mirror) | yes | 28 Sep 2026 | tier 3; posts are link cards with empty text, so the card title/description is ingested |
| semafor.com | yes | 28 Sep 2026 | tier 3 (mostly US/global) |
| fintwitter.bsky.social | yes | 28 Sep 2026 | tier 3 aggregator, ~400 posts/day (dominates volume; see note) |
| bankofengland.bsky.social | yes | never posted | **dropped** (listed under `disabled` in accounts.yaml) |

Volume note: in the captured 27-28 Sep sample, fintwitter accounted for 831 of 1,241 events
(two thirds). It is the main driver of token cost and of noise the writer has to reject. If
cost matters more than coverage, demote it to a separate poll cadence or drop it.

## Proposed additions (need your approval; not in accounts.yaml yet)

Method: (1) `app.bsky.feed.searchPosts` on api.bsky.app for 25 UK rates/fiscal terms
(gilts, Bank Rate, MPC, OBR, DMO, PWLB, SONIA, UK CPI, fiscal rules, ...); (2) the follow
lists of the six UK-focused seeds, keeping accounts followed by 3+ of them; (3) profiling
578 candidates: bio, followers, own posts in the last 30, how many of those hit a UK
rates/fiscal keyword list, last post date. Ranked by relevant recent posts and by how many
seeds follow them. "fb" = followed by N of the 6 UK seeds; "rel" = relevant posts / own posts
in the last 30.

| # | handle | who | fb | rel | last post | evidence (recent relevant posts) |
|---|---|---|---|---|---|---|
| 1 | davidmilliken.bsky.social | David Milliken, Reuters economics reporter covering the BoE and HM Treasury | 5 | 17/18 | 28 Sep | 28 Sep: ONS reworking of LFS labour data using PAYE; 17 Sep: three takeaways from a BoE decision day (with Andy Bruce) |
| 2 | jamessmithrf.bsky.social | James Smith, chief economist, Resolution Foundation (ex-BoE) | 6 | 20/28 | 24 Sep | 24 Sep: NIESR London Macro Policy Forum on the monetary policy outlook; housing tax reform |
| 3 | robwoodecon.bsky.social | Rob Wood, chief UK economist, Pantheon Macroeconomics | 5 | 14/23 | 25 Sep | 24 Sep: whether March's fiscal buffer is needed given borrowing and energy costs; 25 Sep: polling and the political outlook |
| 4 | philaldrick.bsky.social | Phil Aldrick, senior economics reporter, Bloomberg | 2 | 20/23 | 23 Sep | 23 Sep: BoE plans MPC meetings in Leeds; 23 Sep: calls for stronger efforts to cut spending and debt |
| 5 | rjpartington.bsky.social | Richard Partington, Guardian senior economics correspondent | 4 | 13/22 | 27 Sep | 21 Sep: global stock market risk piece; regular UK economy reporting |
| 6 | dsmitheconomics.bsky.social | David Smith, economics editor, The Sunday Times | 1 | 10/16 | 26 Sep | 26 Sep: column on the Chancellor's growth claims; 22 Sep: productivity and hours worked |
| 7 | julianhjessop.bsky.social | Julian Jessop, independent economist (IEA) | 3 | 13/27 | 28 Sep | 24 Sep: 30-year gilt yields after the BoE halted sales; 24 Sep: Budget options and fiscal headroom |
| 8 | chriswhittall.bsky.social | Chris Whittall, IFR, derivatives and secondary markets | 5 | 12/28 | 28 Sep | 21 Sep: government bonds heading for the worst year since 2022; 28 Sep: record corporate bond sale |
| 9 | robinwigglesworth.ft.com | Robin Wigglesworth, editor, FT Alphaville | 5 | 7/18 | 28 Sep | 28 Sep: century-bond losses; frequent sovereign debt commentary |
| 10 | benzaranko.bsky.social | Ben Zaranko, economics editor, The Observer (ex-IFS) | 6 | 10/24 | 25 Sep | 8 Sep: Treasury changing a fiscal rule for the first time in 20 years; 25 Sep: Labour conference |
| 11 | ruthcurtice.bsky.social | Ruth Curtice, chief executive, Resolution Foundation (ex-HMT) | 5 | 9/17 | 25 Sep | 25 Sep: new productivity data; 23 Sep: how the Chancellor sets spending totals |
| 12 | gilesyb.bsky.social | Giles Wilkes, Institute for Government (ex-No 10 economic adviser) | 6 | 7/20 | 28 Sep | 28 Sep: industrial policy threads; fiscal commentary |
| 13 | niesrorg.bsky.social | NIESR | 2 | 17/25 | 24 Sep | 24 Sep: London Macro Policy Forum sessions on monetary policy |
| 14 | resolutionfoundation.org | Resolution Foundation | 4 | 10/28 | 28 Sep | 27-28 Sep: labour market (NEETs) analysis; regular fiscal event analysis |
| 15 | tonytassell.bsky.social | Tony Tassell, FT financial opinion editor | 6 | 6/26 | 28 Sep | 28 Sep: Treasury appointment; 1976 IMF bailout retrospective |

Alternates if any of the above are declined: beeboileau.bsky.social (IFS economist; active
but relevant posts are older), chris-williamson.bsky.social (S&P Global PMI economist;
global), soumayakeynes.ft.com (FT economics columnist; more trade than rates),
carsjung.bsky.social (IPPR, ex-BoE; last post 13 days ago), pdonovan-econ.bsky.social
(UBS chief economist; global).

Not proposed despite ranking high in the search: US-focused economists and reporters
(El-Erian, Timiraos, Casselman, Sahm, Swonk), PR accounts (deVere Group), MPs, and
aggregators with no editorial filter.

## Adding an approved account

Append to the right tier in `config/accounts.yaml` (handle only is fine) and run
`uv run scripts/resolve_dids.py --write` to fill in the DID. Jetstream picks up the new
DID on the next start of `squawk run`/`squawk listen`.

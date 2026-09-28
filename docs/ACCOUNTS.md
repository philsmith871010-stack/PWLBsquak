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

## Local government coverage (checked 2026-09-28)

Method: 60 handle guesses (cipfa.*, room151.*, lgcplus.*, themj.*, publicfinance.*, lgiu.*,
local.gov.uk, arlingclose.*, ukmba.*, ccla.*, ...), typeahead and full actor search for 55
names, post search for 24 sector terms (section 114, local government finance settlement,
PWLB, council tax, CIPFA, Room151, fair funding review, exceptional financial support, LGPS,
housing revenue account, ...), then profiling 885 candidates for bio, UK relevance, recent
own posts and how many hit a council-finance keyword list.

**The sector press and bodies you named are not usably on Bluesky.** Most have claimed a
handle and stopped:

| account | state |
|---|---|
| CIPFA (`cipfa.bsky.social`) | 1 post, Nov 2024; dormant |
| Room151 (`room151.co.uk`, `room151.bsky.social`) | domain handle with 0 posts; the other 7 posts, last Nov 2024 |
| LGC (`sarahcalkin.bsky.social`, editor) | 7 posts, last Jun 2026; no LGC brand account |
| The MJ / Municipal Journal | no account found (`themj.bsky.social` is empty) |
| Public Finance (CIPFA magazine) | no account; `publicfinance.bsky.social` is an unrelated academic journal |
| LGiU (`lgiu.bsky.social`, `joncarrwest.bsky.social`) | 0 posts each |
| SIGOMA, Grant Thornton UK, Local Government Lawyer, Localis | 0 posts / dormant since 2024-25 |
| CCN, DCN, Arlingclose, Link, UK Municipal Bonds Agency, CCLA, MHCLG (official) | no account found |
| Tony Travers, David Phillips (IFS), Kate Ogden (IFS), Rob Whiteman, Jack Shaw | not on Bluesky (namesakes only, or empty) |
| Patrick Butler (Guardian social policy editor) | 32 posts on council insolvency and SEND deficits, but nothing since Apr 2025 |

**Active, relevant accounts found** (rel = council-finance-relevant posts / own posts in the last 30; suggested tier in brackets):

| # | handle | who | followers | rel | last post | evidence |
|---|---|---|---|---|---|---|
| 1 | local.gov.uk | Local Government Association | 1,572 | 19/30 | 28 Sep | daily; conference panels on adult social care, LGA statements on funding and reorganisation [tier 2] |
| 2 | kirstyweakley.bsky.social | Kirsty Weakley, LGC journalist | 1,010 | 11/26 | 27 Sep | 22 Jul: how well Burnham's cabinet understands local government; regular LGC links [tier 1] |
| 3 | theifs.bsky.social | Institute for Fiscal Studies | 12,198 | 8/21 | 28 Sep | 24 Sep: school funding per pupil; regular public-finance analysis incl. local government finance [tier 2] |
| 4 | centreforcities.bsky.social | Centre for Cities | 5,785 | 11/27 | 28 Sep | 22 Sep: fiscal devolution timetable; 24 Sep: council housebuilding briefing [tier 2] |
| 5 | mhclg-answers.bsky.social | bot posting every MHCLG written parliamentary answer | 385 | 4/30 | 28 Sep | 23 Sep: answers on local government reorganisation; useful early signal on settlement/EFS questions [tier 3] |
| 6 | nao.org.uk | National Audit Office | 547 | 7/30 | 22 Sep | reports on local authority financial sustainability and audit backlogs [tier 2] |
| 7 | jenwilliamsft.bsky.social | Jennifer Williams, FT northern correspondent | 28,074 | 1/16 | 25 Sep | devolution, mayors and council housing; low volume [tier 2] |
| 8 | localauthority.news | Local Authority (Medway), Ed Jennings' outlet | 284 | 14/30 | 25 Sep | 19 Sep: Medway's budget gap moving from £8.4m to £65.9m in four months [tier 3] |
| 9 | edjennings.bsky.social | Ed Jennings, editor of Kent Current / Local Authority | 760 | 8/26 | 21 Sep | Kent devolution and council finances [tier 3] |
| 10 | instituteforgovernment.org.uk | Institute for Government | 21,803 | 1/22 | 28 Sep | devolution events; mostly Whitehall [tier 3] |
| 11 | meganisaac.bsky.social / antbreach.bsky.social | IfG devolution researcher; Centre for Cities policy director | 205 / 2,865 | 5/12, 4/13 | 24 Sep | fiscal devolution numbers, council housebuilding finance [tier 2] |
| 12 | inlogov.bsky.social | INLOGOV, University of Birmingham | 181 | 16/30 | 14 Sep | reorganisation and unitary transitions; two weeks since last post [tier 3] |
| 13 | insidehousing.bsky.social / jessmccabe.bsky.social | Inside Housing and its deputy editor | 1,790 / 4,360 | 6/30, 7/24 | 28 Sep | Homes England grant funding, HRA and council housing finance [tier 3] |
| 14 | monadohle.bsky.social | Mona Dohle, LGPS editor at Room151 | 165 | 4/24 | 21 Sep | the only Room151 presence; pensions rather than treasury [tier 3] |
| 15 | se13ed.bsky.social | Ed Hammond, Centre for Governance and Scrutiny | 652 | 3/28 | 25 Sep | council governance and LGA statements [tier 3] |

Also active but less useful for this feed: New Local (last post 3 Sep), Co-op Councils
Innovation Network (conference chatter), Local Government Studies journal (academic),
Inside Croydon (one council), Sam Freedman (public services commentary, 91k followers, low
council-finance density), several Local Democracy Reporters (Somerset, Doncaster, London,
Brighton: useful only if you want individual councils).

Recommendation: if you want a local-government stream in the feed, approve 1-6 as a start;
they are institutional, post daily, and the writer prompt already knows how to frame
settlement, reorganisation and audit news for treasury teams. The council-finance
conversation itself (CIPFA, Room151, LGC, The MJ) still lives on X and LinkedIn, so an X
adapter would be the way to get it, not more Bluesky accounts.

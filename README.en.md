# CSDN Publisher — Automate your CSDN blog operations with a real browser

A toolkit that drives a real Chrome via Playwright and reuses **your own login session** to
automate the whole blogging loop: publish → measure → review → engage.

> CSDN exposes no public write API (non-browser clients get HTTP 521), so this project
> automates the real editor UI instead. It **never touches or stores your password** — the
> login session lives only in the local `.profile/` directory (treat it like your account
> password; never share it).

---

## ⚠️ Compliance & risk notice (read first)

- The tool only reads and publishes **your own content**. It does not fake traffic.
- **Never automate CAPTCHA solving.** When a slider / security challenge appears, the tool
  stops and waits for a human. Keep an eye on the browser window during any run.
- Bulk publishing, engagement and DMs are sensitive behaviors on the platform.
  **Account safety first**: don't publish in rapid bursts, and you own the DM content.
- Automation of this kind is a gray area — **use at your own risk**. Publish only your own
  original writing; CSDN has originality checks and plagiarism detection.

---

## 1. Features

| # | Capability | Entry point | Notes |
|---|---|---|---|
| 1 | Declarative batch publishing | `csdn_job.py` | Describe the job in `jobs/job.*.json`; `prepare` collects sources + builds skeletons + generates covers → you write `posts/*.md` → `publish` fills title/tags/summary/cover/category and submits |
| 2 | Scheduled publishing | `publish.on="schedule"` / `csdn_schedule_drafts.py` | `auto` (best slot from your own retro data) / absolute time / `+26h`; also batch-convert existing drafts to scheduled (content untouched) |
| 3 | Read-data review | `csdn_stats.py` | Read-only scrape of views/likes/comments/favorites, snapshots, true deltas, read velocity, grouped report |
| 4 | Batch review / cross-batch ranking | `csdn_retro.py` / `csdn_rank.py` | Same-age comparison + 24h extrapolation + online verification; cross-batch leaderboards |
| 5 | Pre-publish lint | `csdn_lint.py` | Empty title, skeleton body, tag overflow, **title collision with your own published articles** block publishing before the browser even starts |
| 6 | Bulk engagement | `csdn_interact.py` | Like/collect/comment on recent posts by small authors; content-farm filtering; per article×op dedup DB; comments are two-phase (write reflections, review, then send) |
| 7 | Bulk DM | `csdn_message.py` | Sends `templates/message.txt` verbatim to followers/following; **does nothing without `--yes`/`--wait`** (hard gate) |
| 8 | Topic research | `csdn_search.py` / `csdn_hot.py` | Dual ranking (heat + favorites) → deep-read fingerprints → cross-check with your account → md report |
| 9 | Covers & figures | `make_cover*.py` / `csdn_figure.py` / `csdn_image.py` | Gradient/designed covers; Mermaid vector diagrams; tables/terminal output rendered to PNG and uploaded to CSDN's image host |
| 10 | Draft management | `csdn_drafts.py` / `csdn_draft_clean.py` / `csdn_draft_check.py` | Draft count verification, list/delete with title+time checks, image-liveness verification |

---

## 2. Requirements

| Item | Requirement | Notes |
|---|---|---|
| OS | **Windows 10/11** | Launchers are `.bat`; selectors and browser behavior were verified on Windows + Chrome |
| Python | **3.10+** | Only stdlib + the two packages below |
| Browser | **Google Chrome** installed | Uses `channel=chrome`; alternatively `python -m playwright install chromium` |
| playwright | `>=1.40` | Drives the real browser |
| Pillow | `>=10.0` | Cover and figure rendering |

No CSDN open-platform account and no API key are needed. The login session is your local
Chrome cookie.

---

## 3. Installation

### Option A: one command (recommended)

From the project root (the directory of this README):

```bat
setup.bat
```

It finds Python → creates `.venv` → installs `playwright` + `Pillow` (falls back to the
Tsinghua mirror) → checks for Chrome.

Then log in once (QR code or password):

```bat
login.bat
```

Verify the session:

```bat
.\csdn.bat check
```

### Option B: manual

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe publish_csdn.py login
.\.venv\Scripts\python.exe publish_csdn.py check
```

All commands below assume you run them **from the project root** with
`.\.venv\Scripts\python.exe`. The `*.bat` launchers are thin wrappers around the same calls.

---

## 4. Quick start: publish one article

```powershell
# 0. list the bundled example jobs
.\.venv\Scripts\python.exe csdn_job.py

# 1. verify login
.\csdn.bat check

# 2. copy an example job and edit it (keywords / minViews / count / publish.on)
Copy-Item jobs\job.all.json jobs\job.mytopic.json

# 3. collect sources + build skeletons + generate covers
.\.venv\Scripts\python.exe csdn_job.py prepare jobs\job.mytopic.json

# 4. ★ write the body into posts\*.md (the tool never writes articles; remove the TODO line)

# 5. check completeness + lint results
.\.venv\Scripts\python.exe csdn_job.py status jobs\job.mytopic.json

# 6. publish (on = yes publish / no draft / schedule scheduled)
.\.venv\Scripts\python.exe csdn_job.py publish jobs\job.mytopic.json
```

Publish mode is a single switch in the job file:

```jsonc
"publish": { "on": "yes" }       // publish publicly now
"publish": { "on": "no" }        // save to CSDN draft box (tags/summary/category still filled)
"publish": { "on": "schedule", "schedule": "auto", "scheduleGap": "1d" }
```

~24h after publishing, run the review once:

```powershell
.\csdn_stats.bat
type output\stats\latest.md
```

---

## 5. Examples per capability

Markers: 🌐 = needs browser + login; 💻 = fully local/offline; 🖥 = needs Chrome but no login.

### 5.1 Single publish / scheduled publish

```powershell
# 🌐 publish one file (dialog field read-back verified; category mismatch aborts rc=7)
.\.venv\Scripts\python.exe publish_csdn.py publish posts\demo.md

# 🌐 schedule +26h
.\.venv\Scripts\python.exe publish_csdn.py publish posts\demo.md --schedule "+26h"

# 💻 export publish-dialog structure (selector calibration)
.\.venv\Scripts\python.exe publish_csdn.py inspect
```

### 5.2 Batch-convert existing drafts to scheduled

```powershell
.\.venv\Scripts\python.exe csdn_schedule_drafts.py --at "2026-10-20 16:30" --dry-run --only 1
.\.venv\Scripts\python.exe csdn_schedule_drafts.py --at "2026-10-20 16:30"
```

### 5.3 Draft box management

```powershell
.\.venv\Scripts\python.exe csdn_draft_clean.py --tab 草稿箱 --raw      # 🌐 list drafts (read-only)
.\.venv\Scripts\python.exe csdn_draft_clean.py --delete IDX --match <title-substring> --expect-time "2026-10-20 16:30"   # 🌐 delete (double-checked)
.\.venv\Scripts\python.exe csdn_draft_check.py <articleId>            # 🌐 verify images survived
```

### 5.4 Read-data / batch / cross-batch review

```powershell
.\csdn_stats.bat --pages 2                        # 🌐 scrape + report
.\.venv\Scripts\python.exe csdn_retro.py probe    # 🌐 online verification
.\.venv\Scripts\python.exe csdn_retro.py build    # 💻
.\.venv\Scripts\python.exe csdn_retro.py analyze  # 💻
.\.venv\Scripts\python.exe csdn_rank.py --since 2026-10-08 --until 2026-10-14   # 💻
```

For daily reviews only change `day` in `retro/config.json` (or pass `--day`) — **never
clone the script for a specific day**.

### 5.5 Pre-publish lint

```powershell
.\.venv\Scripts\python.exe csdn_lint.py posts\docker-basics.md --tags Docker,容器 -k Docker   # 💻 exit 1 = error
```

### 5.6 Bulk engagement

```powershell
.\csdn_interact.bat --prepare-comments --ops 三连 --count 50     # 🌐 phase 1: build review list
#     ↓ write ~100-char reflections into output\interact\pending-<runid>.comments.json
.\csdn_interact.bat --preview output\interact\pending-<runid>.json   # 🌐 review
.\csdn_interact.bat --send --pending output\interact\pending-<runid>.json   # 🌐 send after your approval
.\csdn_interact.bat --ops like --count 50                       # 🌐 like only
.\csdn_interact.bat --ops 三连 --count 50 --dry-run              # 💻 judge only, no clicks
```

`count` has **no default** — always specify it.

### 5.7 Bulk DM

```powershell
.\csdn_message.bat --to follower --limit 20 --wait      # 🌐 list only, sends nothing
.\csdn_message.bat --to follower --limit 20 --yes       # 🌐 send to the confirmed list
.\csdn_message.bat --to follower --limit all --yes --skip-sent   # 🌐 resend undelivered
.\csdn_message.bat --stats                              # 💻 history
```

The body is always the verbatim content of `templates\message.txt` (500 chars max; the
script refuses to send beyond that). `csdn_top_urls.py` refreshes the `1.url`~`4.url`
slots with today's top-4 articles by views.

### 5.8 Topic research

```powershell
.\.venv\Scripts\python.exe csdn_search.py --q "AI Agent" --limit 30 --deep 5 --my-pages 2   # 🌐
```

### 5.9 Covers & figures

```powershell
.\.venv\Scripts\python.exe make_cover.py -t "Title" -s "Subtitle" -o posts\c1.png           # 💻
.\.venv\Scripts\python.exe make_cover_pro.py -t "Title" --theme purple -o posts\c2.png      # 💻
.\.venv\Scripts\python.exe csdn_figure.py diagram --code exp\examples\rag-flow.mmd --out img\flow.png --title "Pipeline"   # 🖥
.\.venv\Scripts\python.exe csdn_image.py render --table exp\examples\chunk-recall-numbers.md --out img\t1.png --title "Recall"   # 💻
.\.venv\Scripts\python.exe csdn_image.py upload --files img\t1.png --out img\urls.json      # 🌐 (max 3 per session)
```

### 5.10 One experiment per article

```powershell
.\.venv\Scripts\python.exe exp\new_experiment.py <slug> --title "…"    # 💻 scaffolds README/run.py/numbers.md
```

---

## 6. Configuration

Three layers — **prefer config over code** when the site changes.

### 6.1 `config.json` (global selectors / thresholds / endpoints)

| Section | Controls |
|---|---|
| `browser` | Robustness: stale profile lock cleanup, translate-bubble suppression, launch retries, heartbeat |
| `risk` | Risk gate: stop and wait for a human on slider detection (**publish pipeline only**) |
| `crash` | Crash self-healing: graded recovery (tab closed → new page in place; process dead → relaunch) |
| `selectors` | All publish-dialog selectors (each list takes the first visible one) |
| `message` | DM: body file, 500-char cap, roster API, rate-limit phrases, pacing, dedup policy |
| `interact` | Engagement: keywords, three filters, candidate sources, pacing, comment-box selectors |
| `urls` / `login_cookies` / `default_tags` / `category_names` | Entry URLs, login-session probe, default tags, **whitelist of your real column names** |

Print the full annotated list:

```powershell
.\csdn.bat config
```

or read `skills\csdn-publish\CONFIG.md`.

### 6.2 `jobs/job.<topic>.json` (what to publish)

Six fields (`title` / `content` / `tags` / `cover` / `summary` / `category`) share one value
syntax: `"auto"` | `"file:path"` | literal string.

- `search.keywords` / `search.minViews` are **required** (no defaults).
- `count` XOR `articles`.
- Top-level same-named fields act as defaults for all articles (except `content`).
- `publish.on`: `yes` / `no` / `schedule`.

Two runnable examples ship in `jobs/`.

### 6.3 `retro/config.json` (review methodology)

Baselines, same-age reference batches, 24h extrapolation params, probe throttling.
Only `day` changes day to day.

### 6.4 Precedence

CLI flags > job file > `config.json`.

---

## 7. Directory layout

See the Chinese README section 七 / the tree below — layout is identical:

```
csdn-publisher/
├── publish_csdn.py          # publish engine (risk gate + crash self-healing live here)
├── csdn_job.py              # ★ declarative job runner
├── csdn_stats.py            # read-data collection + report (read-only)
├── csdn_retro.py            # single-batch review: probe/build/analyze/compare
├── csdn_rank.py             # cross-batch ranking
├── csdn_lint.py             # pre-publish lint (offline)
├── csdn_research.py         # source collection (reused by csdn_job.py)
├── csdn_refs.py             # per-article independent search + global dedup
├── csdn_search.py           # hot-list topic research → md report
├── csdn_hot.py              # hot-list scraping + viral style fingerprint
├── csdn_interact.py         # bulk like/collect/comment
├── csdn_message.py          # bulk DM
├── csdn_top_urls.py         # today's top-4 links into the DM template
├── csdn_figure.py           # Mermaid vector diagrams / stock photos
├── csdn_image.py            # table/terminal → PNG + upload to CSDN image host
├── make_cover.py            # gradient covers
├── make_cover_pro.py        # designed covers
├── csdn_drafts.py           # draft-box count (draft-mode acceptance)
├── csdn_draft_clean.py      # draft/scheduled list & delete
├── csdn_draft_check.py      # draft image-liveness check
├── csdn_schedule_drafts.py  # batch schedule drafts (content untouched)
├── csdn_preview_dump.py     # probe: real preview DOM + computed styles
├── _probe_delivery.py       # probe: per-recipient DM delivery re-check (read-only)
├── _probe_category.py       # probe: real column membership (read-only)
├── _probe_column.py         # probe: column page article list (read-only; pass column ID)
├── probe_writable.py        # diagnostic: is the workspace writable (read-only)
├── analyze_excl.py          # one-off analysis: recompute after excluding articles
├── test_*.py                # offline self-tests
├── config.json              # selectors / thresholds / endpoints
├── requirements.txt
├── setup.bat / login.bat    # one-command setup / first QR login
├── csdn*.bat                # launchers for the four main commands
├── install-skill.ps1        # install skills/csdn-publish as an AI skill
├── jobs/                    # job configs (two examples; yours are gitignored)
├── templates/               # start.txt banner / message.txt DM body / comment.txt
├── retro/config.json        # review methodology config
├── log/                     # README.md convention + known-issues.md pitfalls
├── skills/csdn-publish/     # skill docs: SKILL.md / COMMANDS.md / CONFIG.md
├── design-system/           # cover design spec
├── exp/                     # new_experiment.py scaffold + examples/ inputs
├── posts/                   # your markdown articles (runtime, gitignored)
├── output/                  # runtime artifacts (gitignored)
├── .profile/                # browser login session = account password (gitignored)
└── .venv/                   # virtualenv (gitignored)
```

---

## 8. Tests (all offline: no browser, no network)

```powershell
.\.venv\Scripts\python.exe test_robustness.py     # 33 checks
.\.venv\Scripts\python.exe test_launch_paths.py   # 25 checks
.\.venv\Scripts\python.exe test_category_gate.py  # category hard gate
.\.venv\Scripts\python.exe test_schedule.py       # schedule parsing & bounds
```

---

## 9. FAQ

**Browser won't start — `Executable doesn't exist at .pw-browsers\chromium-…`?**
Don't install chromium. It's almost always a stale `.profile\lockfile`. Stale locks are now
cleaned automatically before launch; if not, kill only the chrome processes whose command
line contains this project's `.profile`, then delete the lockfile. **Never**
`taskkill /IM chrome.exe`.

**"Chrome was not shut down properly, restore?" / "Translate this page?" bubbles?**
Both are suppressed via launch args plus a page-level fallback. If you still see
"⛔ profile is in use by another process", another session is really using it — don't force-clean.

**A slider / security challenge appears mid-run?**
Stop immediately and wait for a human, indefinitely. Don't skip, don't retry around it,
don't record it as success. Only an explicit `--on-risk abort` gives up. ⚠️ The engagement
and DM pipelines are **not** wired to this gate yet — watch the browser yourself for those.

**Publish rejected / "daily quota reached"?**
Account-level quota (drafts don't consume it; scheduled posts do). Check
"今日发文额度还有 N 篇" at the editor bottom.

**Article not in its column though publish "succeeded"?**
`category` must match a real column name exactly (case-sensitive). The log prints the full
list of available names; a dialog read-back mismatch aborts with rc=7. `_probe_category.py`
reads the real online membership.

**DM run stopped halfway / someone didn't receive it?**
Everyone sent is in the `sends` table of `output/message/message.db`. Correct resend =
re-run `--wait`, remove "already sent today + confirmed blocked" from pending.json, then
`--yes`. `--skip-sent` skips *historically successful* people, not "not yet reached this
round". Don't trust the in-run sent/failed verdicts — re-check with `_probe_delivery.py`.

**Not enough engagement candidates?**
Normal: only ~5% of search results are from the last 7 days, plus three filters. Add
`--keywords` or widen `--days`; the tool honestly reports "did X/N".

**Browser fails / screenshots missing / draft count unreadable for no clear reason?**
Run `probe_writable.py` first to distinguish "environment not writable" from "tool broken".

**Why is there no reverse-chronological article list on CSDN?**
There isn't one (homepage tabs are categories, channel pages show 24-day-old first items,
rankings have no time field, the "newest" search sort is broken). The only usable source is
the search page's **comprehensive** sort `s=0`, whose result rows carry dates.
See `log/known-issues.md` item 2.

More recurring pitfalls: [`log/known-issues.md`](log/known-issues.md).

---

## 10. Use as an AI skill (optional)

`skills/csdn-publish/` is a complete agent playbook (SKILL.md holds all the rules, flows
and hard-won pitfalls).

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File install-skill.ps1
```

For other agent frameworks, copy `skills/csdn-publish` into your framework's skills
directory (replace `__TOOL_DIR__` in SKILL.md with the real project path).

---

## 11. Known limitations

- Daily publish quota is account-level; hitting it aborts with rc=3 (drafts don't consume it).
- At most 3 columns per article, chosen from your existing columns only.
- `csdn_stats.py` sees only content-management fields (no tag/column dimensions).
- Cross-article comparisons have inherent age bias; reports default to the last 7 days and say so when falling back.
- Tag auto-derivation is heuristic; write `tags` by hand when quality matters.
- `csdn_research.py --pages` paging is unreliable (client-side paging); pass more keywords instead.

---

## 12. License & credits

- **License**: [MIT](LICENSE) (Copyright (c) 2026 朱昱先 / Zhu Yuxian)
- **Author / copyright holder**: 朱昱先 (Zhu Yuxian)
- **Hosting platform**: GitHub — <https://github.com/ZYX200603/csdn-publisher>

Credits — this project stands on the shoulders of:

- [Playwright](https://playwright.dev) (Apache-2.0) — browser automation
- [Pillow](https://python-pillow.org) (MIT-CMU) — image processing
- [Mermaid](https://mermaid.js.org) (MIT) — vector diagram rendering (cached to `img/vendor/` on first use)

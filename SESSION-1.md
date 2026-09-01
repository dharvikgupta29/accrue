# Session 1 — two hours, three people

Everything to do today, in order. Drop this in the repo so all three of us are
working off the same thing.

---

## What we're building (for Siddh and Benji)

AI agents take actions on your behalf — spend money, read files, send emails.
Companies put rules on them, like *"never transfer more than $10,000."*

The thing that checks those rules has **no memory**. It looks at one action,
says yes or no, and forgets. So an agent spends $9,999 fifty times. Every check
passes, correctly, and half a million dollars is gone — because the rule was
about one payment and the damage was about the total.

**We're building the memory.** A table that records every action an agent takes,
so totals can be checked.

That's the whole project. Today we build a five-row version of it.

---

## Before we start — everyone check

```
python3 --version
git --version
sqlite3 --version
```

All three should print a version. If `git` is missing on macOS, running it once
triggers the install prompt.

---

## 0:00–0:10 · Repo

**Dharvik:**

```
rm -rf ~/Developer/accrue
mkdir -p ~/Developer/accrue && cd ~/Developer/accrue
git init
printf '__pycache__/\n*.pyc\n*.db\n' > .gitignore
mv ~/Downloads/PRIMER.md .
mv ~/Downloads/SESSION-1.md .
git add . && git commit -m "init: primer and session plan"
gh repo create accrue --public --source=. --push
```

Then on github.com → the repo → **Settings → Collaborators → Add people** →
add Siddh and Benji. They get an email; accept it.

**Siddh and Benji**, once you've accepted:

```
mkdir -p ~/Developer && cd ~/Developer
git clone https://github.com/dharvikgupta29/accrue.git
cd accrue
code .
```

Don't discuss any of this. It's mechanical. Ten minutes.

---

## 0:10–0:45 · Read PRIMER.md together

Out loud, taking turns. All three following along.

- **Dharvik** reads Part 1 — the concepts
- **Siddh** reads Part 2 — SQL
- **Benji** reads Part 3 — Python

**Stop and argue at two places. Don't move on until all three can say it in your
own words:**

1. **§1.5** — why Cedar *can't* just add things up. (Because no loops means it
   can be proved correct, and proof was the whole reason to use it.)
2. **§2.4** — `SUM`, `COUNT`, `COUNT(DISTINCT)` are exactly the three things
   Cedar can't do. This is the project in three lines of SQL.

---

## 0:45–1:00 · Dharvik explains it from memory

Laptops shut. Start to finish, no notes.

Siddh and Benji: interrupt with anything unclear, including things that feel too
basic to ask. Those are the good questions.

One of you writes every question that gets a shaky answer into `NOTES.md` under
`## Open questions`.

---

## 1:00–1:45 · Write `hello_ledger.py`

One typist, **rotate every 15 minutes.** The two not typing read the primer and
call out what comes next.

**Assemble it from the primer. Don't copy blocks out of it.**

### The seven steps

1. `import sqlite3`
2. Connect to `ledger.db`
3. `CREATE TABLE IF NOT EXISTS events` with columns:
   `id`, `timestamp`, `session_id`, `action`, `resource`, `cost_usd`
4. Insert five rows, all with `session_id = "sess-001"`:

   | timestamp | action | resource | cost_usd |
   |---|---|---|---|
   | 10:04:12 | read_file | config.py | null |
   | 10:04:15 | read_file | main.py | null |
   | 10:04:22 | write_file | main.py | null |
   | 10:04:31 | transfer | acct-1234 | 9999 |
   | 10:04:48 | transfer | acct-1234 | 9999 |

5. `conn.commit()`
6. Run three queries and print each result:
   - `SUM(cost_usd)` where session_id = 'sess-001'
   - `COUNT(*)` where session_id = 'sess-001'
   - `COUNT(DISTINCT resource)` where session_id = 'sess-001'
7. `conn.close()`

### Run it

```
python3 hello_ledger.py
```

### Target output

```
19998.0
5
3
```

**Why 3 and not 5:** five actions touched only three distinct resources —
`main.py` appears twice, `acct-1234` appears twice. That's `COUNT(DISTINCT)`
doing precisely what this project exists to do.

### The two bugs everyone hits

- **Script runs, table is empty, no error at all** → you forgot `conn.commit()`
- **Confusing error on a query with one value** → `("sess-001")` must be
  `("sess-001",)`. A one-item tuple needs the trailing comma.

### If stuck more than 10 minutes

Stop. Paste the code and the error into the chat. Don't burn the session on one
line.

---

## 1:45–2:00 · Commit and close out

```
git add .
git commit -m "hello ledger: table, five rows, three queries"
git push
```

Each person adds their lines to `NOTES.md`:

```markdown
## Next action
Dharvik: <one specific sentence — where exactly you'd start next time>
Siddh:   <...>
Benji:   <...>

## Confused me today
Dharvik: <...>
Siddh:   <...>
Benji:   <...>
```

Commit that too.

---

## Done means

- [ ] Repo public, all three have push access
- [ ] All three can explain why a memoryless rule engine can't stop 50 × $9,999
- [ ] `python3 hello_ledger.py` prints `19998.0`, `5`, `3`
- [ ] Pushed, with next actions written

**Not** required today: a good schema, real data, Cedar, anything working
properly. Those are session 2.

---

## Next session, so it's not a blank page

1. Find a real agent log file on Dharvik's machine (`~/.codex`, `~/.copilot`,
   `~/.claude`) and see what fields it actually has
2. Replace the five hand-typed rows with real ones read from that file
3. Argue properly about the schema now that we've seen real data

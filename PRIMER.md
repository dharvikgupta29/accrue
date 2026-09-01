# Primer

Everything you need to know to build this. Read it together, out loud if that
helps. Roughly 30 minutes to read, 60 to work through.

Nobody here needs to already know any of it.

---

# Part 1 — The five concepts

## 1.1 Authentication vs authorization

Two different questions that people constantly mash together.

**Authentication** — *who are you?* A password, an ID card. The bouncer checking
your driving licence.

**Authorization** — *are you allowed to do this?* The guest list. You can be
perfectly authenticated and still not allowed in.

This whole project is about the second one. Nobody doubts who the AI agent is.
The question is whether what it's about to do is permitted.

## 1.2 What a policy engine is

A **policy** is a rule, written down. *"Never transfer more than $10,000."*

A **policy engine** is a program whose only job is to take a rule and a proposed
action, and answer yes or no.

The old way of doing this was an `if` statement buried in your code:

```python
if amount > 10000:
    refuse()
```

That works and it's bad, for three reasons: nobody can see the rule without
reading the code, changing it means shipping software, and you can't check it.

So people moved rules out of code and into files. Which means you need a small
language for writing rules.

**Cedar** is one of those languages. Amazon made it. A Cedar rule looks like:

```
forbid (principal, action == Action::"transfer", resource)
when { context.amount > 10000 };
```

Read it aloud: *forbid anyone from doing a transfer, when the amount is over
10,000.* That's a whole rule, in a file, readable by someone who can't code.

## 1.3 Stateless vs stateful — the important one

**Stateless** means: no memory between decisions.

The policy engine looks at one action, says yes or no, and forgets everything.
Next action arrives, it decides again from scratch. It has no idea the previous
action ever happened.

**Stateful** means the opposite — it remembers what came before.

Cedar is stateless. Deliberately.

## 1.4 Why that's a problem

```
rule: never transfer more than $10,000

transfer $9,999   →   9,999 > 10,000?  no   →   ALLOWED
transfer $9,999   →   9,999 > 10,000?  no   →   ALLOWED
... fifty times ...

                                    total: $499,950
```

Every single decision was correct. The rule was about one payment. The damage
was about the total.

And it isn't only money:

| rule | how it gets beaten |
|---|---|
| no bulk access to customer data | read 800 records, one at a time |
| no mass email | send 300 individually |
| don't delete the repo | 4,000 single-file deletions |

**Every individual action is legal. The sequence is the violation.**

## 1.5 Why Cedar can't just fix it

Adding things up over a history requires looping — "for each past action, add
the cost." Cedar has no loops. On purpose.

Because no rule can ever loop, every rule is guaranteed to finish, which means
the whole rule can be turned into mathematics and checked by a solver *before*
you deploy it. You can ask "is there **any** possible request that gets past
this rule?" and get a real, proven answer.

Add loops and you lose that. And that proof was the only reason to pick Cedar
over just writing Python.

**So it's not a bug anyone will patch. Something outside has to keep the score.**

That outside thing is what we're building.

## 1.6 What an append-only event log is

A table you only ever add rows to. Never update, never delete.

```
when       session    action       resource      cost
10:04:12   sess-001   read_file    config.py     -
10:04:15   sess-001   write_file   main.py       -
10:04:31   sess-001   transfer     acct-1234     9999
10:04:48   sess-001   transfer     acct-1234     9999
```

Why append-only: it's a record of what happened. History doesn't change. If a
row could be edited, it would be a database, not a record — and the entire value
here is that it's trustworthy evidence.

Everything the project does is a question you ask this table.

---

# Part 2 — SQL, the four things you need

A **database** is a file full of tables. A **table** is rows and columns, like a
spreadsheet. **SQL** is the language for talking to it.

We're using **SQLite**, which is a database that lives in a single file on your
laptop. No server, no install, no setup. It's already inside Python.

## 2.1 CREATE TABLE — make the shape

```sql
CREATE TABLE events (
    id          INTEGER PRIMARY KEY,
    timestamp   TEXT,
    session_id  TEXT,
    action      TEXT,
    resource    TEXT,
    cost_usd    REAL
);
```

- `TEXT` — words. `REAL` — a number with decimals. `INTEGER` — a whole number.
- `INTEGER PRIMARY KEY` — SQLite fills this in automatically, 1, 2, 3… so every
  row has a unique id without you doing anything.

## 2.2 INSERT — add a row

```sql
INSERT INTO events (timestamp, session_id, action, resource, cost_usd)
VALUES ('10:04:31', 'sess-001', 'transfer', 'acct-1234', 9999);
```

Text goes in single quotes. Numbers don't.

## 2.3 SELECT — read rows back

```sql
SELECT * FROM events;                              -- every column, every row
SELECT action, cost_usd FROM events;               -- just two columns
SELECT * FROM events WHERE session_id = 'sess-001';  -- only matching rows
```

`WHERE` filters. Everything downstream depends on it, because every question you
ask is scoped to one session.

## 2.4 The aggregates — and these ARE the project

```sql
SELECT SUM(cost_usd) FROM events WHERE session_id = 'sess-001';
SELECT COUNT(*) FROM events WHERE session_id = 'sess-001';
SELECT COUNT(DISTINCT resource) FROM events WHERE session_id = 'sess-001';
```

- `SUM` — add a column up → *cumulative spend*
- `COUNT(*)` — how many rows → *how many actions taken*
- `COUNT(DISTINCT x)` — how many different values → *how many unique files touched*

**Stop and notice this.** These three functions are exactly the thing Cedar
cannot do. SQL has counting built in; Cedar deliberately doesn't. Our whole
project is: run these queries, hand the answers to Cedar.

That's it. That's the insight, in three lines of SQL.

One more, useful later:

```sql
SELECT session_id, SUM(cost_usd) FROM events GROUP BY session_id;
```

`GROUP BY` runs the aggregate once per group — a total for every session at once.

---

# Part 3 — Python talking to SQLite

`sqlite3` is built into Python. Nothing to install.

## 3.1 The five-line shape of every database script

```python
import sqlite3

conn = sqlite3.connect("ledger.db")   # opens the file, creates it if missing
cur = conn.cursor()                   # the thing you send SQL through

cur.execute("SELECT * FROM events")   # run some SQL

conn.commit()                         # save changes to disk
conn.close()
```

- `connect` — hands you the database. The file appears on disk the first time.
- `cursor` — the object you actually run statements on.
- `commit` — **without this, your inserts are silently lost.** Single most common
  beginner bug in this entire library.

## 3.2 Running SQL with values in it

Never build SQL by gluing strings together. Use `?` placeholders:

```python
cur.execute(
    "INSERT INTO events (timestamp, session_id, action, resource, cost_usd) "
    "VALUES (?, ?, ?, ?, ?)",
    ("10:04:31", "sess-001", "transfer", "acct-1234", 9999.0)
)
```

One `?` per value, and a tuple of the values in the same order. Python fills them
in safely and handles quoting for you.

## 3.3 Getting answers back

```python
cur.execute("SELECT SUM(cost_usd) FROM events WHERE session_id = ?", ("sess-001",))
row = cur.fetchone()      # one row, as a tuple: (19998.0,)
total = row[0]            # pull the first item out
```

- `fetchone()` → one row, as a tuple
- `fetchall()` → a list of tuples

Two things that catch everyone:

**A single-item tuple needs a trailing comma** — `("sess-001",)` not
`("sess-001")`. Without the comma Python thinks it's just a string in brackets
and you'll get a confusing error.

**`SUM` returns `None`, not 0, when there are no matching rows.** So
`total = row[0] or 0` is worth writing.

## 3.4 Reading a JSON file

JSON is just text in a structure. A file like:

```json
[
  {"timestamp": "10:04:12", "session_id": "sess-001", "action": "read_file",
   "resource": "config.py", "cost_usd": null},
  {"timestamp": "10:04:31", "session_id": "sess-001", "action": "transfer",
   "resource": "acct-1234", "cost_usd": 9999}
]
```

reads into Python as a list of dictionaries:

```python
import json

with open("events.json") as f:
    events = json.load(f)

for e in events:
    print(e["action"], e["cost_usd"])
```

`json.load` turns the file into normal Python lists and dicts. `null` in JSON
becomes `None` in Python.

---

# Part 4 — Now build it

You now know everything needed. Write this yourselves — don't copy anything,
assemble it from the pieces above.

**`hello_ledger.py`, about 25 lines:**

1. Import `sqlite3`
2. Connect to `ledger.db`
3. `CREATE TABLE IF NOT EXISTS events (...)` with the six columns from §2.1
4. Insert five rows by hand: two file reads, one write, and two transfers of
   9999 — all with `session_id = "sess-001"`
5. `commit()`
6. Run three queries and print the results:
   - total cost for that session
   - how many actions
   - how many distinct resources touched
7. `close()`

When it prints `19998.0`, `5`, `3` — you have built the project in miniature.

Everything from here is: more rows, more columns, and real data instead of typed-in
data. The shape does not change.

**Then:**

```
git add . && git commit -m "hello ledger: table, five rows, three queries" && git push
```

---

# Part 5 — Cheatsheet

| Word | Meaning |
|---|---|
| authentication | proving who you are |
| authorization | deciding what you're allowed to do |
| agent | an AI program that takes actions in a loop |
| tool | one thing an agent can do — read a file, send money |
| policy | a rule, written down |
| policy engine | program that applies rules and answers yes/no |
| Cedar | Amazon's small language for writing rules |
| stateless | no memory between decisions |
| stateful | remembers what came before |
| append-only log | a table you only add to, never change |
| SQLite | a database that's just a file on your laptop |
| SQL | the language for talking to a database |
| row / column | one record / one field of every record |
| aggregate | a function that collapses many rows into one number |
| commit (git) | save a snapshot of your code |
| commit (database) | save your changes to disk — different thing, same word |

## The four SQL statements, together

```sql
CREATE TABLE events (id INTEGER PRIMARY KEY, timestamp TEXT, session_id TEXT,
                     action TEXT, resource TEXT, cost_usd REAL);

INSERT INTO events (timestamp, session_id, action, resource, cost_usd)
VALUES ('10:04', 'sess-001', 'transfer', 'acct-1', 9999);

SELECT * FROM events WHERE session_id = 'sess-001';

SELECT SUM(cost_usd), COUNT(*), COUNT(DISTINCT resource)
FROM events WHERE session_id = 'sess-001';
```

## The Python shape

```python
import sqlite3
conn = sqlite3.connect("ledger.db")
cur = conn.cursor()
cur.execute("SQL HERE", (value1, value2))
result = cur.fetchone()[0]
conn.commit()
conn.close()
```

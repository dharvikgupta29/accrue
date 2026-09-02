# Cedar primer

What Cedar is, how it decides, and how to run it. ~20 minutes to read,
~20 to work through. Companion to PRIMER.md.

You already have it installed: `cedar --version` → 4.12.0

---

# Part 1 — What Cedar is

A tiny language for writing authorization rules, and a program that applies
them. It answers exactly one question:

> Given who is acting, what they want to do, and to what — is it allowed?

Answer is always `ALLOW` or `DENY`. Nothing else.

Cedar is not a database, not a web server, not a library you build an app on.
It's a decision function. You give it a question and it gives you a verdict.

## The shape of a policy

```
permit (
    principal,
    action == Action::"transfer",
    resource
)
when {
    context.amount < 10000
};
```

Three parts:

| | |
|---|---|
| **effect** | `permit` or `forbid` — the only two |
| **scope** | the `(principal, action, resource)` triple — *which requests this rule is about* |
| **conditions** | optional `when { }` / `unless { }` — *extra tests that must pass* |

Every policy ends with `;`.

## Reading the scope

Each of the three slots can be open or pinned:

```
principal                          any principal at all
principal == User::"alice"         only alice
principal in Group::"admins"       anyone in that group

action == Action::"transfer"       only this action
action in [Action::"read", Action::"write"]    either of these
```

An open slot means "this rule doesn't care about that part."

In your `budget.cedar`, `principal` and `resource` are open and only the
action is pinned — the rule is about transfers, by anyone, to anything.

## Entity UIDs

`User::"alice"` is an **entity UID** — a type and an id. `Action::"transfer"`,
`Session::"sess-001"`, `Account::"acct-1234"` are all the same shape.

The quotes matter. `Action::"transfer"` is correct; `transfer` on its own is a
syntax error, and this trips everyone at least once.

---

# Part 2 — The request

To make a decision, Cedar needs four things plus a lookup table.

| Part | What it is | Example |
|---|---|---|
| **principal** | who is acting | `Session::"sess-001"` |
| **action** | what they want to do | `Action::"transfer"` |
| **resource** | what they want to do it to | `Account::"acct-1234"` |
| **context** | extra facts about *this one request* | `{"cumulative_cost": 15000}` |
| **entities** | a table of objects and their attributes | `[]` |

## Context vs entities — the distinction that matters

**Context** is about *this request, right now*. The amount being transferred.
The time of day. It's a fresh JSON object every time, supplied by whoever is
asking.

**Entities** is a store of objects that exist independently of any request —
users, their group memberships, resources, their owners. It's a JSON array
where each item looks like:

```json
{
  "uid":     { "type": "User", "id": "alice" },
  "attrs":   { "department": "finance" },
  "parents": [ { "type": "Group", "id": "admins" } ]
}
```

That's what makes `principal.department == "finance"` and
`principal in Group::"admins"` work.

**Your entities file is `[]` — empty.** Your policy never looks at an
attribute of the principal or resource; it only reads `context.cumulative_cost`.
So there's nothing to store. Cedar still requires the file to exist.

This is a real design decision, not laziness: you put the running total in
**context** rather than as an attribute on a Session entity. Context is a fresh
file per request, which you're generating anyway. Entities would mean rewriting
the entity store before every single decision.

---

# Part 3 — How Cedar decides

Three rules, in order. All of them matter.

**1. Default deny.** If nothing matches, the answer is DENY. You cannot get an
ALLOW by accident — something has to explicitly permit it.

**2. Any matching `permit` allows.** One is enough.

**3. Any matching `forbid` wins, always.** A `forbid` cannot be outvoted by any
number of `permit`s.

So the algorithm is:

```
does any forbid match?          → DENY
else does any permit match?     → ALLOW
else                            → DENY
```

That's why your policy needs both rules. The `permit` opens transfers up; the
`forbid` claws back the over-budget case. Delete the `permit` and *everything*
is denied, because of default deny. Delete the `forbid` and everything is
allowed regardless of the total.

---

# Part 4 — Running it

```
cedar authorize \
  --policies  src/policies/budget.cedar \
  --entities  entities.json \
  --principal 'Session::"sess-001"' \
  --action    'Action::"transfer"' \
  --resource  'Account::"acct-1234"' \
  --context   context.json
```

Notes:

- `--entities` is **required**, even when empty
- The single quotes around the UIDs protect the double quotes from the shell
- `-v` adds which policies applied — genuinely useful, use it while learning
- `-t` prints timing
- Exit is via stdout: it prints `ALLOW` or `DENY`

---

# Part 5 — Read your own policy

Open `src/policies/budget.cedar` and go through it together.

```
permit (
    principal,
    action == Action::"transfer",
    resource
);
```

*Anyone may transfer to anything.* No conditions.

```
forbid (
    principal,
    action == Action::"transfer",
    resource
) when {
    context.cumulative_cost > 10000
};
```

*Except when the running total exceeds 10,000.*

**Cedar has no idea what `cumulative_cost` means or where it came from.** It's
just a number in the request. Something outside — `authorize.py` — added up the
session's history and put it there before asking.

That's the whole architecture of this project, visible in five lines: the
memory lives outside, the rule stays memoryless, and Cedar never learns that
anything happened before.

---

# Part 6 — The hands-on

Two files in the repo root.

**`entities.json`**
```json
[]
```

**`context.json`**
```json
{ "cumulative_cost": 5000 }
```

### 1 · Under the limit

Run the command from Part 4. → **ALLOW**

### 2 · Over the limit

Change `context.json` to `{ "cumulative_cost": 15000 }`. Same command.
→ **DENY**

One number changed. Nothing else did. Stop and notice that this is the project
working, before a single line of Python is involved.

### 3 · See which rule fired

Add `-v` and run both again. Cedar names the policy responsible. Watch the
`forbid` take over at 15000.

### 4 · Break it deliberately

- Delete the `permit` rule → everything DENYs, even at 5000. That's default deny.
- Put it back, delete the `forbid` → everything ALLOWs, even at 15000.
- Restore both.
- Change the action to `Action::"read_file"` in the command → DENY, because
  neither rule's scope matches and default deny takes over.

Breaking it on purpose teaches more than four successful runs.

### 5 · Clean up

```
rm context.json entities.json
```

They get generated per request once `_ask_cedar()` exists.

---

## If step 1 errors instead of printing ALLOW

Most likely Cedar wants the entities to exist even with no attributes. Try:

```json
[
  { "uid": { "type": "Session", "id": "sess-001" }, "attrs": {}, "parents": [] },
  { "uid": { "type": "Account", "id": "acct-1234" }, "attrs": {}, "parents": [] }
]
```

If that fixes it, **write it down** — it's a real finding about how the CLI
behaves, and `_ask_cedar()` will need to generate that file rather than `[]`.

---

# Cheatsheet

| Term | Meaning |
|---|---|
| effect | `permit` or `forbid` |
| scope | the `(principal, action, resource)` triple |
| condition | `when { }` / `unless { }` |
| entity UID | `Type::"id"`, e.g. `Account::"acct-1234"` |
| context | per-request JSON facts |
| entities | store of objects and their attributes |
| default deny | no match → DENY |
| deny overrides | any `forbid` beats every `permit` |

```
does any forbid match?       → DENY
else does any permit match?  → ALLOW
else                         → DENY
```

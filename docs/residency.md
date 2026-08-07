# §23. Residency: should something always hold the current save?

The question as it was asked: this system is stateless, every autosave throws the cache away, and a
resident service fed by a file watcher could hold the parsed world so nothing ever parses on a
request. There are two processes with no shared memory — the MCP server is stdio and the LLM client
starts one per session; the web server is uvicorn on 8712 and the player starts it — so "resident"
has to mean one of four arrangements. This section measures the problem first, because one measured
number decides most of it.

Every number here was taken on the reference world (`Han Solo`, 3.0 MB save, 26-key projection) on the
owner's machine, which was not idle: a torrent client and a file indexer were resident throughout, and
that is the machine the game also runs on. Paired comparisons alternate between the two builds inside
one loop so load lands on both.

---

## 23.1 What it actually costs

| | |
|---|---|
| one save parse, sidecar, alone | **3.7–4.0 s** |
| `scan_saves()` — the `--list` header sidecar | **87 ms** idle, **190 ms** under load |
| reading the 1.6 MB projection pickle | **~15 ms** |
| `load_projection()`, warm, in-process | **~90 ms** — of which 87 ms is the scan |
| normalising Docs.json, once per process | 142 ms |
| the five derived views, per `WorldState` | graph 51, structures 84, pipe_flow 13, conduit_runs 170, proposals **497** — **815 ms** |
| a projection in memory / its five views | ~13 MB / ~7 MB |

**The stampede is real and was worse than the audit said.** One autosave, then the eleven layers the
map page fetches: **24 peak parser subprocesses** — eleven `--list` scans and eleven full parses of the
same file — and 6.4–8.4 s of wall clock, against 3.7 s for one parse alone. The audit reported
4.35 s and eleven parses; the extra eleven scans it did not name are the second half.

**The second finding is real too.** `WorldState` was rebuilt per request, so its `cached_property`
views bought nothing across requests: 815 ms of graph, structures, pipe flow, conduit runs and
proposals, per request, per layer. That is most of what a warm request cost.

After single-flighting the parse, the scan and the five views (`core/singleflight.py`, [§11](mcp-surface.md#11-caching)):

| 11 concurrent GETs | HEAD | flighted |
|---|---|---|
| cold — save just rewritten | 8.40 s | **4.19 s** |
| warm — projection cached | 1.21 s | **0.36 s** |
| peak parser subprocesses | 24 | **2** |

Medians of five paired reps. Cold is now what one parse costs, which is the floor until something
parses *before* anyone asks.

## 23.2 The number that decides the rest

**A second, cold process reading a save a first process already parsed pays 205 ms** — ~190 ms of
which is its own `--list` scan, and ~15 ms is the pickle. The projection already crosses the process
boundary, through the disk cache that has been there since the beginning, for the price of a directory
listing.

So a resident service can buy exactly three things over what ships today:

1. **the derived views across processes** (~0.8 s) — only the projection is on disk, not the graph;
2. **the duplicate parse** when both processes miss the same key at the same moment — one wasted 4 s
   parse and one wasted core, at most once per autosave, and only when both are asked in that window;
3. **pre-warming** — moving the 4 s off the request path entirely.

Nothing else. In particular it does not buy the projection handoff, because that is already 15 ms.

## 23.3 The four arrangements

### (a) Status quo, plus single-flight and a shared-view memo — **built, measured, shipped in this branch**

Cold start unchanged: each process parses once for a save nobody has cached, or pays 205 ms if the
other already did. Independent of whether the game is running — the newest file on disk is the answer,
and if nothing moves nothing is parsed. No daemon, so no daemon death. Read-only posture untouched:
the only writes are still the cache pickle and the label/plan stores. The "answers name the file they
read" contract is untouched — the flight key *is* `(path, mtime_ns, size, schema)` and every answer
still carries its own header. Windows-clean: `threading.Lock` and `threading.Event`, no fork, no file
locking, no sockets, no port. Nothing new to run. The suite still spawns sidecars freely — 941 fast
tests green, and the `-m integration` failure set is byte-identical to master's.

What it does not fix: two processes both parsing when both miss at once, and the 4 s still landing on
a request.

### (b) The web server becomes the source of truth; the MCP server asks it over HTTP

This is the worst ratio of new failure modes to seconds saved, and the seconds are only item 1 above.

The fallback must exist, because the player may never have started the web server — so this option has
two code paths for every read, permanently, and the fallback is the one that runs for everyone who
only uses the LLM. The MCP server would have to distinguish "connection refused" from "connected then
hung" from "half a response" *per call*, under a client's timeout, and fall back correctly from each.
It also inverts the stated architecture: the web stack is an optional extra (`[web]`), `interfaces/web`
may not be importable at all, and `tests/test_architecture.py` enforces that the stdio server does not
depend on an ASGI stack. And the answer-names-its-file contract becomes a claim relayed from a process
that may be pinned to a different `?save=` than the caller asked about — solvable, but it is a new way
to be wrong about which file an answer describes, and that is the contract this project guards hardest.

### (c) A third daemon, both servers as clients

Fails the packaging test directly: a stranger cloning this would have three things to run, or two
processes racing to spawn the third. On Windows there is no fork, so it is a detached process with a
console handle and a lifetime to manage, plus a socket or pipe and another port to keep clear of 8712.
Serving a 13 MB projection over that socket costs 30–60 ms of pickling per request — and the way to
avoid that is to put the pickle on disk and let readers mmap it, which is a shared on-disk cache with
extra steps. Every reader still needs the full fallback path for when the daemon is not up, plus a
restart policy, and the suite must either start it or bypass it; bypassing it leaves the daemon path
untested. No.

### (d) Shared on-disk cache with a file lock, and a watcher that pre-warms it — **the recommendation**

Half of this already exists and always has: that is the 205 ms in §23.2. Two pieces are missing, and
both are small.

**Pre-warming.** `interfaces/web/watch.py` already polls the save tree every 3 s and already knows the
moment the newest mtime moves. Calling `load_projection()` from a thread on that edge moves the 4 s off
the request path: the browser's refetch, which the same event triggers, arrives to a warm cache instead
of starting the parse. With the flight in place a pre-warm and a request that arrives mid-parse
*collapse into one* — which is why this is now roughly fifteen lines rather than a design. It costs
nothing when the game is not running, because nothing moves. It helps the MCP side too, but only when
the web server happens to be up; for an LLM-only user it changes nothing.

**A cross-process lock**, one file per cache key in the cache directory, created `O_CREAT|O_EXCL`
(atomic on NTFS), so the second process waits for the first's pickle rather than parsing in parallel.
The fiddly part is a stale lock left by a killed process, which needs an mtime-based break — and it is
fiddly in exactly the direction the existing posture already handles: *the cache is an optimisation,
never a requirement*, so on any doubt, parse. This is worth less than pre-warming and should be built
second.

Neither adds a process, a port, or a fallback path. Both keep every answer naming the file it read.

## 23.4 What to build next, in order

| | what | where | worth |
|---|---|---|---|
| 1 | Pre-warm on the watcher's edge: parse in a thread when the newest mtime moves. | `interfaces/web/watch.py:103` `poll_once` | the 4 s leaves the request path for anyone with the map open |
| 2 | Fingerprint the save tree with one `os.scandir` and skip the `--list` sidecar when it is unchanged. | `core/saveio/projection.py` `scan_saves` | the last ~90 ms floor under every warm read, on both surfaces |
| 3 | A cross-process lock file per cache key, `O_CREAT|O_EXCL`, with an mtime-based break for a stale one. | beside the pickle in `config.cache_dir()` | one duplicate 4 s parse per autosave, when both processes miss together |

Item 2 outranks item 3 by measurement and is not one of the four arrangements at all — see below.

## 23.5 What would change the recommendation

- **If a fresh MCP process is common** — one per client launch, and some clients restart often — then
  the 815 ms of derived views is paid on every launch, and the fix is not a daemon but *one more
  pickle beside the projection*: persist the views on the same two-tier cache with the same key. That
  beats all four arrangements and nobody has measured how often a client restarts.
- **If the parse grows.** At 4 s a duplicate parse is waste; at 30 s, two of them on the machine
  running the game is a stutter the player feels, and the cross-process lock moves ahead of
  pre-warming.
- **If `scan_saves` stays this expensive.** It is now the floor under every warm read — a subprocess to
  list a directory, 87–190 ms — and §11's "1 ms" describes the memo, not the call. The cheapest fix is
  not in this list at all: fingerprint the tree with one in-process `os.scandir` over `(name, mtime,
  size)` and reuse the previous scan when the fingerprint is unchanged. That is a memo with a correct
  invalidation key rather than a TTL, it costs well under a millisecond, and it would make a warm read
  genuinely ~1 ms. Measured but not built here.
- **If the cross-process handoff turns out to be narrower than it looks.** `prune_cache` keeps the 12
  newest pickles and autosaves rotate every ~5 minutes, so the shared cache spans about an hour. That
  is ample for the current save, which is the only one the handoff needs — but a workflow that reads
  across many old saves would not get it.

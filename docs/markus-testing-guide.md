# Set Up MarkUs and Test the New AI Server Changes

This guide does two things:

1. It shows you how to start everything, step by step.
2. It then runs you through tests that prove the new changes work.

You do not need to know how the code works.
Plan for about 45 minutes the first time.

**Good news:** the practice MarkUs database comes with a ready-made course
called **csc108**. It already has a teacher, students, and an assignment
called **autotest_py** with an AI test group and a handed-in student file.
We only fix its settings and run it. We do not build anything from scratch.

> Menu names can change a little between versions.
> If a button name is not exact, look for one that means the same thing.

---

## The map

Here is how the pieces talk to each other:

```
MarkUs (the grading website)
   → Autotester (runs the tests)
      → AI server (this project)
         → Ollama (holds the AI models)
```

And here is who watches for break-ins:

```
AI server → log book (Loki) → alarms (Grafana) → email (Mailpit)
```

---

## What changed (what we are testing)

1. **The server keeps a log book of break-in tries.**
   A wrong key gets written down with the caller's address (IP).
2. **Two alarms ring when the server is attacked.**
   Alarm 1: one address tries more than 10 wrong keys in 5 minutes.
   Alarm 2: more than 50 wrong keys arrive from anywhere in 5 minutes.
   Each alarm sends an email.
3. **Two fixes to the chat service.**
   The server now reads the instructions MarkUs sends. Before, it threw them away.
   A wrong key now gets a clean "not allowed" answer, not a crash.

---

## Part 1 — Start all the programs

You need a **Terminal** window. On a Mac, open the app called **Terminal**.
Paste each command as **one line** and press Enter.

**1.1 — Check Ollama (the AI models).**

```bash
ollama list
```

**PASS:** you see a list of models, and `qwen3:1.7b` is in it.
If the command fails, open the **Ollama** app first, then try again.

**1.2 — Start the AI server and its watchers.**

```bash
cd ~/work/ai-server && docker compose --profile monitoring up -d
```

Wait for it to finish. Then check:

```bash
docker ps --format '{{.Names}}'
```

**PASS:** the list has `ai-server-app`, `grafana`, `loki`, `mailpit`, and `ai-server-redis`.

**1.3 — Give the tests a working key.**

```bash
docker exec ai-server-redis redis-cli set "api-key:secret123" alice
```

**PASS:** you see `OK`.
The Autotester is already set up to use this same key (`secret123`).

**1.4 — Make the shared network.**

```bash
docker network create markus_dev
```

**PASS:** you see a long code, **or** a note that it already exists. Both are fine.

**1.5 — Start the Autotester.**

```bash
cd ~/work/autotesting && docker compose up -d
```

**PASS:** `docker ps` now also shows `autotest-client` and autotesting containers.
If you see an error about a missing network, run:

```bash
cd ~/work/autotesting && docker compose down && docker compose up -d
```

**1.6 — Start MarkUs.**

```bash
cd ~/work/Markus && docker compose up -d rails
```

The **first start is slow**. It builds and fills a practice database.
This can take 10 minutes or more. Then open this page in your browser:

    http://host.docker.internal:3000/csc108

We use this special address instead of `localhost` so the Autotester
can talk back to MarkUs. Use it for all MarkUs pages in this guide.
(If it does not load on a Mac, try `http://docker.for.mac.localhost:3000/csc108`.)

**PASS:** you see the MarkUs login page.

> **About logins:** this practice MarkUs accepts **any password**.
> Only the user name matters. We will use the teacher account: `instructor`.

---

## Part 2 — Open the ready-made course

**2.1** Log in as `instructor`. Password: anything, like `x`.

**2.2** Open the course **csc108**.

**2.3** Find the assignment called **autotest_py** and open it.
This assignment already has automated tests turned on,
and one student group has already handed in a file called `submission.py`.

---

## Part 3 — Check the Autotester connection

**3.1** Open the course **Settings** (the course edit page).

**3.2** Find the box called **Autotest URL**. It should already say:

    http://autotest-client:5000

**3.3** Click the **Test** button next to the connection settings.

**PASS:** a message says the connection works.

If it fails, the Autotester may have a new key. Retype the same URL,
save the page, and click **Test** again.
If there is a **Refresh** button, click it too.

---

## Part 4 — Fix the AI test group settings

The assignment already has an AI test group called **AI_TESTY**,
but it points at the wrong server and has two broken settings.
We fix all three here.

**4.1** Make our special instruction file. Paste this in the Terminal:

```bash
echo 'You are a helpful teaching assistant. Start your reply with the words TA_OK: then shortly explain what the code does and any problems in it.' > ~/Desktop/sys_taok.txt
```

The words `TA_OK:` are our tracer.
If the AI's answer starts with `TA_OK:`, our instructions got through.

**4.2** Open the assignment **autotest_py → Settings → Automated Testing** tab.

**4.3** Use the upload button to upload **`sys_taok.txt`** as a test file.

**4.4** Find the test group **AI_TESTY** (tester type **ai**) and fix these fields:

| Field | Change it to | Why |
|---|---|---|
| remote_url | `http://host.docker.internal:5001/chat` | It pointed at the school's real server. This points at ours. |
| system_prompt | `sys_taok.txt` | It held a plain sentence. It must be a **file name**. |
| prompt | `code_feedback_v3` | It was empty. This tells the AI how to review code. |
| Model Name | `qwen3:1.7b` | Without it, the tool asks for a big model this machine does not have. |
| Timeout | `300` | The small AI model can be slow. |

Leave the other fields alone (`scope: code`, `submission: submission.py`,
`model: remote`, Output Display: `overall_comment`).

Save the page. A status message appears at the top.
Wait until it says **Completed**. The first save may take a few minutes.

> **If there is no Model Name box:** your Autotester has an older settings
> form. Ask for the one-line schema fix (`model_name` in the ai tester's
> `settings_schema.json`), restart the Autotester, then log in as `.admin`
> and press **Refresh schema** on the Admin → Courses → csc108 page.
> The box will appear.

---

## Part 5 — The big test: AI feedback with our instructions

The student's work is already handed in and collected. We just run the test.

**5.1** In **autotest_py**, open the **Submissions** tab.

**5.2** Click the group's name to open the grading view.
If the name is not a link yet, tick its box, click **Collect Submissions**,
wait a moment, refresh, and try again.

**5.3** Open the **Test Results** tab. Click **Run Tests**.
Now wait. The AI is thinking. This can take one to three minutes.
Refresh the test results now and then.

**5.4** When the test run finishes, look for the feedback.
Open the tab that holds the **Overall Comment** box (the annotations tab).

**PASS — the most important check in this guide:**

- A comment about the student's code appears, **and**
- it **starts with `TA_OK:`**.

The `TA_OK:` proves our fix. The instruction file traveled
MarkUs → Autotester → AI server → the model, and the model obeyed it.
Before this fix, the AI server threw those instructions away.

---

## Part 6 — Break-in checks through the real system

Now we prove the watchdog sees **real** traffic, not just test commands.

**6.1** Take the key away, so the Autotester's key becomes wrong:

```bash
docker exec ai-server-redis redis-cli del "api-key:secret123"
```

**6.2** In MarkUs, click **Run Tests** again and wait for the result.

**PASS:** the test result shows a clear error that says **401**
or **Invalid API key**. It must **not** say error **500**.

**6.3** Now look in the log book. Open Grafana:

    http://localhost:3001

Click the menu (three lines) → **Explore**. The picker should say **Loki**.
Click **Code**, paste this line, and click **Run query**:

```
{service_name="ai-server"} | event="auth_failure"
```

**PASS:** the newest line is the Autotester's failed try, just now.
Open the line: it has a `client_ip`. It must **not** show any key text.

**6.4** Give the key back:

```bash
docker exec ai-server-redis redis-cli set "api-key:secret123" alice
```

---

## Part 7 — The alarms

Open the email catcher in your browser and keep the tab open:

    http://localhost:8025

**7.1 — Alarm 1: many bad tries from one address.**
Paste this one line. It clears old emails, then knocks 12 times with wrong keys:

```bash
curl -s -X DELETE http://localhost:8025/api/v1/messages >/dev/null; for i in $(seq 1 12); do curl -s -o /dev/null -X POST http://localhost:5001/chat -H "X-API-KEY: bad-try-$i" -H 'X-Forwarded-For: 203.0.113.66' -F 'content=hi'; done; echo "Done."
```

In Grafana: menu → **Alerting** → **Alert rules** → open the **AI Server** folder.
Wait up to 2 minutes and refresh.

**PASS:** **Auth brute force from a single IP** turns red (**Firing**),
and an email lands in Mailpit naming the address `203.0.113.66`.

**7.2 — Alarm 2: bad tries from many addresses.**
Paste this one line. It knocks 60 times, each from a different address:

```bash
for i in $(seq 1 60); do curl -s -o /dev/null -X POST http://localhost:5001/chat -H "X-API-KEY: spray-$i" -H "X-Forwarded-For: 198.51.100.$i" -F 'content=hi'; done; echo "Done."
```

**PASS:** **Auth failure spray across many IPs** turns **Firing**,
and a second email arrives. It is marked **critical**.

> Alarm 1 may ring again during this test. That is normal.
> Later you may get `[RESOLVED]` emails. That just means the alarms turned off.

---

## Results sheet

| # | What it checks | Pass? |
|---|---|---|
| 5.4 | AI feedback in MarkUs starts with `TA_OK:` (instructions get through) | ☐ |
| 6.2 | Bad key shows a clean 401 in MarkUs, not a 500 crash | ☐ |
| 6.3 | The failed try is in the log book, with the address, without the key | ☐ |
| 7.1 | Brute force alarm fires and emails | ☐ |
| 7.2 | Spray alarm fires and emails | ☐ |

If all five boxes are checked, everything we built works.

---

## If something goes wrong

| What you saw | What it likely means |
|---|---|
| MarkUs page never loads | First start is slow. Wait 10 minutes. Then run `docker compose logs rails` in `~/work/Markus` and look for errors. |
| Autotest URL **Test** button fails | The Autotester is not running, or its key changed. Redo steps 1.5 and 3.3. |
| Autotester tests never start, or setup errors | The Autotester may be missing its parts (fresh checkout). In `~/work/autotesting` run `docker compose run --rm server-deps-updater` and then `docker compose run --rm client-deps-updater`, then restart it. |
| Error says `LimitExceededException` | The Autotester allows 20 calls per minute. Wait one minute and try again, or raise the limit: `docker exec autotesting-redis-1 redis-cli set "autotest:ratelimit:<api-key>:limit" 200` (the api key is on the course's autotest setting). |
| Error says connection refused to `localhost:3000` | MarkUs told the Autotester to call back at an address only your browser knows. Use a MarkUs build that sets `MARKUS_URL` to an address the Autotester can reach, or do every save and test run from a `host.docker.internal` browser tab. |
| Test result says "System Prompt file ... not found" | The system_prompt box holds plain words. It must be a **file name**. Redo steps 4.1–4.4. |
| Test result says model not found | The Model Name box is empty or misspelled. Set it to `qwen3:1.7b` and save again. |
| Test result says timeout | The AI was too slow. Raise Timeout to `600` and run again. |
| Feedback appears but no `TA_OK:` | Run the test once more. If it is still missing, the instructions fix is broken. Report it. |
| Test result shows error 500 | The wrong-key fix is broken. Report it. |
| Log book empty in Grafana | Logs are not flowing. The collector or Loki may be off. Redo step 1.2. |
| No alarm email after 2 minutes | Check the alert rules page first. If the rule fires but no email comes, Grafana cannot reach Mailpit. |

When you report a problem, copy the exact words you saw. That helps a lot.

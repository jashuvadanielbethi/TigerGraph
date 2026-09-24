# I Built a Fraud Investigation Agent on 590,000 Transactions. The Hard Part Wasn't the AI.

For the TigerGraph x Hacker House Goa hackathon, I built an agent that investigates card-fraud alerts the way a bank analyst would: pull the history, look for connected activity, decide what to do, and explain why. Here is what I built, how it works, and the problems that nearly broke it.

**Code:** https://github.com/jashuvadanielbethi/TigerGraph

## The problem

Fraud analysts get an alert and then spend their time gathering transaction history, checking devices, reading policy, and deciding whether to block a card. It is slow, and often the money is gone before the decision. The task: take 20 alerts (a model risk score, a customer complaint, or an analyst request) and, for each, produce a case record, a suspicious activity report if the policy requires one, and the next best action with who must approve it.

The data was the IEEE-CIS dataset: 590,742 transactions, 144,432 device records, and 5,565 closed past cases. Half the alerts were legitimate, so an agent that blocks everything scores badly.

## What I built

For each alert the agent:

1. Pulls the card's full transaction history.
2. Checks for known fraud patterns: card testing, unusual online purchases, new devices, out-of-region use, and other cards sharing the same device.
3. Retrieves similar closed cases as memory.
4. Scores fraud probability, and every point of the score is tied to a specific piece of evidence.
5. Recommends actions under the bank's fraud policy, once before asking for more evidence and once after.
6. Writes the case, the report when needed, and a reason that cites the policy rule.

There is also a dashboard where you type any transaction ID and get a red "Detected Fraud" or green "Safe" popup with the full case behind it.

## Architecture

- **Data layer:** raw CSVs are indexed once into a fast local store.
- **Graph queries:** five queries (card history, device neighbors, region cluster, similar past cases, case write-back), each with a matching GSQL version.
- **Pattern detectors:** small, explainable rules over those queries.
- **Policy engine:** the bank's actions, approval routes (auto, team lead, fraud manager), and the report trigger, encoded as code.
- **Retrieval:** text search over the policy and past analyst notes to ground the reasoning.
- **Dashboard:** Streamlit.

One deliberate choice: **no LLM is in the decision path.** Probabilities, actions, and approval routes come from deterministic code. In a fraud setting I want the same input to give the same answer, and I never want a model deciding whether an action needs a human sign-off. That is why every answer file shows zero tokens used.

## How I used TigerGraph

I designed the graph around how fraud investigations actually think: customers own cards, cards make transactions, transactions come from devices and billing regions, and closed cases attach to all of them. The schema follows the dataset's suggested model, plus Case, Evidence, and Decision vertices so each investigation can become memory for the next.

The queries that matter are graph-shaped. Finding other cards that used the same device is a short hop from a device to its transactions to their cards, which is exactly what GSQL is good at and what would be an ugly self-join in SQL.

**An honest note:** in this submission the agent runs those same five queries against a local index, not a live TigerGraph cluster. The GSQL schema, loading job, and queries are written and ready to load, but I did not run the agent against a live instance. [Add your own Savanna or Community Edition experience here if you tried it: setup, load time, what surprised you.]

## What went wrong (and what I learned)

**1. The card ID doesn't exist in the raw data.** Cases refer to cards like `C12382-K1`, but the transaction file has no such column. My first idea, numbering each customer's cards by first use, was wrong about half the time. The publisher had deliberately shifted the timestamps, which scrambled the order. The fix was to use the real card IDs given in the closed cases and case pack as anchors and spread them to every transaction with the same card details. That resolved 77.6% of the dataset with zero mismatches on the 20 graded cases.

**2. My first "fraud ring" was fake.** The first full run flagged 11 of 20 cases as coordinated rings. The cause: the `DeviceInfo` field is often just `Windows` (47,741 rows) or `iOS Device`, not a real device fingerprint. I was linking thousands of strangers together. Only specific values, like Android build strings, are usable.

**3. A region "ring" that was just a busy city.** I tried linking cards by shared billing region. It fired almost every time because ordinary customers live in the same places. I turned it off rather than ship a signal that looked smart and was noise.

**4. Evidence stacking.** A customer with several old incidents kept pushing new alerts toward "fraud." I capped how much past history can move a score.

**5. Small bugs that mattered.** Transaction IDs turned into `"3000120.0"` strings and silently broke lookups. Two rules recommended the same action, so lists had duplicates. Each was minor and each would have corrupted results quietly.

**6. The frontend "bug" that wasn't a bug.** My input box seemed to ignore typing. The text was there, but my styling had made it white on white. A one-line color fix.

The pattern across all of these: every clever shortcut was wrong until checked against the data's own ground truth. I ended up validating everything, and I trust the results more because of it.

## Results

- 20 of 20 cases investigated, each in well under a second.
- 10 flagged fraud, 10 cleared as legitimate, matching the dataset's hint that about half are legitimate.
- 3 suspicious activity reports filed, where the policy required them.
- Every answer passes a schema and consistency check: real IDs only, no duplicate actions, and the report flag agrees with the recommended actions.
- 9 automated tests pass, including a run over all 20 real cases.

Some cases are what I like most about it: a customer complaint that traced back to a device shared with two other customers' cards, found by graph traversal that nobody asked for.

## What I'd do next

Connect the agent to a live TigerGraph and use its vector search for retrieval, add a real customer-reply channel instead of simulated responses, and do a proper per-card check to bring region-based ring detection back.

[Add a closing line in your own voice: what the hackathon was like for you, who helped, what you would tell someone starting out.]

*#TigerGraph #GraphDatabase #FraudDetection #AI #Hackathon*

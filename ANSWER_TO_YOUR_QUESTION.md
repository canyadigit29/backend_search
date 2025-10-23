# Direct Answer: "What is the per file cap you're talking about?"

## The Simple Answer

The **per file cap** is a setting in your `search_docs.py` file that controls **how many chunks from the same document file can be sent to the AI for summarization at once**.

## What You Currently Have

Looking at line 543 in `app/api/file_ops/search_docs.py`:

```python
# Temp change for testing: summarize all top 50 chunks at once.
included_chunks, pending_chunk_ids = _select_included_and_pending(matches, included_limit=50, per_file_cap=5)
```

This means:
- **50 total chunks** are sent for summarization per batch
- **Maximum 5 chunks** from any single file

## What It Was Before (at the "split50" commit)

The original/default settings were:
- **25 total chunks** per batch
- **Maximum 2 chunks** from any single file

## Why This Matters

**Problem it solves:** Without a per-file cap, if you search for something and the top 50 results are all from the same document, you'd only get information from that one document.

**Solution:** By capping at 2 (or 5) chunks per file, you force the system to pull from many different documents, giving you a more diverse set of information.

## Example Scenario

Imagine you search for "budget" and have 100 matching chunks:

### Without Per File Cap:
- Top 50 results might be: 50 chunks from "2023_budget.pdf"
- Summary only covers one document

### With Per File Cap = 2:
- Top 50 batch might be distributed as:
  - 2 chunks from "2023_budget.pdf"
  - 2 chunks from "2022_budget.pdf"
  - 2 chunks from "budget_meeting_minutes.pdf"
  - 2 chunks from "finance_report.pdf"
  - ... (up to 25 total chunks from ~13 different files)
- Summary covers multiple documents and perspectives

### Current Setting (Per File Cap = 5):
- Same idea but allows more depth per file
- 50 total chunks from ~10 different files
- More comprehensive per file, but covers fewer unique files

## The Trade-off

| Setting | Total Chunks | Per File Cap | Result |
|---------|--------------|--------------|---------|
| **Original** | 25 | 2 | More files, less depth each, more diverse |
| **Current (temp)** | 50 | 5 | Fewer files, more depth each, more comprehensive |

The current "temp" setting gives you:
- **More comprehensive** summaries (2x the content)
- **More depth** per file (5 chunks instead of 2)
- **Less diversity** (fewer different files represented)
- **Higher cost** (more tokens to process)

## Your Context

Based on your conversation history:
1. You reverted to the "split50" commit which had `included_limit=25, per_file_cap=2`
2. At some point, a temporary change was made to test `included_limit=50, per_file_cap=5`
3. This temporary change is still in place (hence the comment "Temp change for testing")

If you want to **remove the temp change** and go back to sending only 25 summaries at a time with a per-file cap of 2, you would change line 543 to:

```python
included_chunks, pending_chunk_ids = _select_included_and_pending(matches, included_limit=25, per_file_cap=2)
```

## Related Files

For more details, see:
- `PER_FILE_CAP_EXPLANATION.md` - Full technical explanation
- `PER_FILE_CAP_VISUAL_SUMMARY.txt` - Quick visual reference

# Per File Cap Explanation

## What is the "Per File Cap"?

The **per file cap** is a diversity parameter in the `search_docs.py` file that controls how many document chunks from the same file can be included in search results sent for summarization.

## Location in Code

The per file cap is implemented in the `_select_included_and_pending()` function in `app/api/file_ops/search_docs.py` (line 105).

## Function Signature

```python
def _select_included_and_pending(matches: list[dict], included_limit: int = 25, per_file_cap: int = 2):
```

## Parameters

1. **`included_limit`**: The total maximum number of chunks to include in the current batch for summarization
   - **Original default**: 25 chunks
   - **Current temporary setting**: 50 chunks (line 543)

2. **`per_file_cap`**: The maximum number of chunks from any single file that can be included
   - **Original default**: 2 chunks per file
   - **Current temporary setting**: 5 chunks per file (line 543)

## Purpose

The per file cap promotes **diversity** in search results by preventing any single file from dominating the summary. This ensures:

1. Users get results from multiple different documents rather than many chunks from one document
2. Better coverage across the entire document collection
3. More balanced representation of different sources

## How It Works

The function operates in two passes:

### First Pass (Enforcing the Cap)
- Iterates through the top 50 search results
- Selects up to `included_limit` chunks (e.g., 25 or 50)
- **Limits each file to `per_file_cap` chunks** (e.g., 2 or 5)
- Tracks how many chunks have been selected from each file using `per_file_counts`

### Second Pass (Filling Remaining Slots)
- If fewer than `included_limit` chunks were selected after the first pass
- Fills remaining slots from the top 50 results **without enforcing the per-file cap**
- This ensures we always return up to `included_limit` chunks if available

## Current State

Looking at line 543 of the current code:

```python
# Temp change for testing: summarize all top 50 chunks at once.
included_chunks, pending_chunk_ids = _select_included_and_pending(matches, included_limit=50, per_file_cap=5)
```

### Temporary Changes Made:
- **`included_limit`**: Changed from 25 to **50** chunks
- **`per_file_cap`**: Changed from 2 to **5** chunks per file
- This means the system now sends **twice as many chunks** for summarization
- Each file can contribute **2.5x more chunks** than before

## Original Settings (Before "split50" commit)

Based on the "split50" commit (25c0b09), the original settings were:
- **`included_limit`**: 25 chunks
- **`per_file_cap`**: 2 chunks per file

This meant:
- Maximum of 25 chunks sent for summarization at a time
- No more than 2 chunks from any single file in that batch
- Results would come from at least 13 different files (if all files hit the cap)
- Remaining chunks from the top 50 would be returned as `pending_chunk_ids` for potential follow-up requests

## Why This Matters

The comment at line 542 says "Temp change for testing: summarize all top 50 chunks at once." This suggests:

1. **Original behavior**: Send 25 chunks at a time with a 2-chunk-per-file limit
2. **Current behavior**: Send 50 chunks at a time with a 5-chunk-per-file limit
3. **Impact**: More comprehensive summaries but potentially:
   - Less diverse (more chunks from same files)
   - Higher token usage/costs
   - Longer processing time

## Related Code Context

The function is called during the search summarization flow to:
1. Select which chunks to include in the LLM summary request
2. Identify which chunks remain "pending" for potential follow-up
3. Balance between comprehensive coverage and result diversity

The pending chunks allow for a "resume" feature where users can request additional summaries from the remaining top-50 results that weren't included in the first batch.

# Per File Cap - Code Location Reference

## Quick Location Guide

### Where is it defined?

**File:** `app/api/file_ops/search_docs.py`

**Function Definition:** Line 105
```python
def _select_included_and_pending(matches: list[dict], included_limit: int = 25, per_file_cap: int = 2):
```

Default values:
- `included_limit: int = 25` ← Default total chunks per batch
- `per_file_cap: int = 2` ← Default max chunks per file

### Where is it called with temporary values?

**File:** `app/api/file_ops/search_docs.py`

**Line:** 543
```python
# Temp change for testing: summarize all top 50 chunks at once.
included_chunks, pending_chunk_ids = _select_included_and_pending(matches, included_limit=50, per_file_cap=5)
```

Temporary override values:
- `included_limit=50` ← Overrides default of 25
- `per_file_cap=5` ← Overrides default of 2

## To Revert the Temporary Change

If you want to go back to the original behavior (25 chunks, 2 per file), change line 543 from:

```python
included_chunks, pending_chunk_ids = _select_included_and_pending(matches, included_limit=50, per_file_cap=5)
```

To:

```python
included_chunks, pending_chunk_ids = _select_included_and_pending(matches, included_limit=25, per_file_cap=2)
```

Or simply remove the parameters to use the function's defaults:

```python
included_chunks, pending_chunk_ids = _select_included_and_pending(matches)
```

## The Function Implementation

**Lines 105-143** contain the full implementation:

### Key Logic Points:

**Line 112:** `top50 = matches[:50]` - Always works with top 50 results

**Line 117-128:** First pass - enforces per_file_cap
```python
# First pass: enforce per-file cap while filling included list
for c in top50:
    if len(included) >= included_limit:
        break
    fid = c.get("file_id")
    cid = c.get("id")
    if not cid:
        continue
    if per_file_counts[fid] < per_file_cap:  # ← Per file cap enforced here
        included.append(c)
        included_ids.add(cid)
        per_file_counts[fid] += 1
```

**Line 130-139:** Second pass - fills remaining slots without cap
```python
# Second pass: if fewer than included_limit, fill from remaining without cap
if len(included) < included_limit:
    for c in top50:
        if len(included) >= included_limit:
            break
        cid = c.get("id")
        if not cid or cid in included_ids:
            continue
        included.append(c)
        included_ids.add(cid)
```

**Line 141-143:** Identifies pending chunks
```python
# Pending are the rest of top50 not included
pending_ids = [c.get("id") for c in top50 if c.get("id") and c.get("id") not in included_ids]
return included, pending_ids
```

## Context: How It Fits Into the Flow

The function is called within `assistant_search_docs()` endpoint:

1. Search is performed → returns sorted matches
2. `_select_included_and_pending()` is called → selects diverse subset
3. Selected chunks are sent to LLM for summarization
4. Pending chunks are returned for potential "resume" feature

## Summary

- **Function location:** Line 105 in `app/api/file_ops/search_docs.py`
- **Call location:** Line 543 in same file
- **Temporary override:** Currently set to 50 chunks, 5 per file
- **Original values:** 25 chunks, 2 per file
- **To revert:** Change line 543 parameters back to `included_limit=25, per_file_cap=2`

# Per File Cap Documentation - Index

## Question: "What is the per file cap you're talking about?"

This repository now contains comprehensive documentation answering this question.

## Quick Start

**Start here:** [ANSWER_TO_YOUR_QUESTION.md](ANSWER_TO_YOUR_QUESTION.md)

This file gives you the clearest, most direct answer with examples.

## Documentation Files

### 1. ANSWER_TO_YOUR_QUESTION.md (Start Here!)
- **Purpose:** Direct, simple explanation
- **Best for:** Understanding what per file cap is and why it matters
- **Length:** 84 lines
- **Contains:**
  - Simple definition
  - Current vs original settings
  - Example scenarios
  - Trade-offs
  - How to revert the temporary change

### 2. CODE_LOCATION_REFERENCE.md
- **Purpose:** Exact code locations and line numbers
- **Best for:** Finding the code and making changes
- **Length:** 112 lines
- **Contains:**
  - Function definition location (line 105)
  - Function call location (line 543)
  - Full implementation breakdown
  - Step-by-step revert instructions

### 3. PER_FILE_CAP_EXPLANATION.md
- **Purpose:** Comprehensive technical documentation
- **Best for:** Deep understanding of the feature
- **Length:** 95 lines
- **Contains:**
  - Detailed algorithm explanation
  - Two-pass selection process
  - Historical context ("split50" commit)
  - Impact analysis
  - Related code context

### 4. PER_FILE_CAP_VISUAL_SUMMARY.txt
- **Purpose:** Quick visual reference
- **Best for:** At-a-glance comparison
- **Length:** 75 lines
- **Contains:**
  - ASCII diagrams
  - Side-by-side settings comparison
  - Visual flow of how it works
  - Impact summary

## Quick Summary

### The Answer in One Sentence:
**Per file cap** is a limit on how many document chunks from the same file can be included in one batch sent to the AI for summarization, currently set to 5 chunks per file (was 2).

### Current State (Line 543 of search_docs.py):
```python
included_chunks, pending_chunk_ids = _select_included_and_pending(
    matches, 
    included_limit=50,  # Was: 25
    per_file_cap=5      # Was: 2
)
```

### Purpose:
Promotes diversity by preventing one document from dominating search results.

### Impact of Temporary Change:
- ✅ More comprehensive (2x the chunks: 50 vs 25)
- ✅ More depth per file (2.5x: 5 vs 2 chunks)
- ❌ Less diversity (fewer unique files represented)
- ❌ Higher costs (more tokens to process)

## How to Navigate This Documentation

1. **New to the concept?** → Start with [ANSWER_TO_YOUR_QUESTION.md](ANSWER_TO_YOUR_QUESTION.md)

2. **Need to find the code?** → See [CODE_LOCATION_REFERENCE.md](CODE_LOCATION_REFERENCE.md)

3. **Want deep technical details?** → Read [PER_FILE_CAP_EXPLANATION.md](PER_FILE_CAP_EXPLANATION.md)

4. **Need quick visual reference?** → Check [PER_FILE_CAP_VISUAL_SUMMARY.txt](PER_FILE_CAP_VISUAL_SUMMARY.txt)

5. **Want to see the actual code?** → Open `app/api/file_ops/search_docs.py`
   - Function definition: Line 105
   - Current call with temp values: Line 543

## Related Context

This documentation was created in response to your question about the "per file cap" during a conversation about reverting repository changes. You had previously:

1. Reverted to the "split50" commit (which had `included_limit=25, per_file_cap=2`)
2. Noticed a temporary change that affected how many summaries are sent at a time
3. Asked about what the "per file cap" refers to

The temporary change mentioned in the code comment at line 543 is still in place, which is why you're currently sending 50 chunks with a cap of 5 per file, rather than the original 25 chunks with a cap of 2 per file.

## Quick Action Items

If you want to **remove the temporary change** and return to the original behavior:

1. Open `app/api/file_ops/search_docs.py`
2. Go to line 543
3. Change from: `included_limit=50, per_file_cap=5`
4. To: `included_limit=25, per_file_cap=2`

See [CODE_LOCATION_REFERENCE.md](CODE_LOCATION_REFERENCE.md) for detailed instructions.

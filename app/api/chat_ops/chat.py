
# This file was patched to show search_docs error feedback in GPT conversation.
# Only the changed logic is shown below. Injects search_docs failure messages visibly into GPT prompt stack.
# Full chat.py file continues before and after this.

        # 🔍 Auto-trigger document search if "search" is in the prompt but not "search the web"/"online"/"internet"
        lowered_prompt = prompt.lower()
        if "search" in lowered_prompt and not any(kw in lowered_prompt for kw in ["search the web", "search online", "search the internet"]):
            try:
                from app.api.file_ops.search_docs import search_docs
                doc_results = search_docs({"query": prompt})
                if doc_results.get("results"):
                    doc_snippets = "\n".join([d["content"] for d in doc_results["results"]])
                    messages.insert(
                        1,
                        {
                            "role": "system",
                            "content": f"Relevant document excerpts:\n{doc_snippets}",
                        },
                    )
                elif doc_results.get("error") or doc_results.get("message"):
                    # Inject the failure reason so it shows visibly in GPT response
                    messages.insert(1, {
                        "role": "system",
                        "content": f"[search_docs output]: {doc_results.get('error') or doc_results.get('message')}",
                    })
            except Exception as e:
                logger.warning(f"⚠️ search_docs failed: {e}")
                messages.insert(1, {
                    "role": "system",
                    "content": f"[search_docs exception]: {str(e)}"
                })

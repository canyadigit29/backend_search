import re

def normalize_user_input(s: str) -> str:
    """
    Normalizes user-provided input by cleaning whitespace, normalizing quotes,
    and stripping common conversational artifacts.
    Ported from the chatbot-ui Node.js implementation.
    """
    if not isinstance(s, str):
        return ""
    try:
        t = s
        # Normalize smart quotes to ASCII
        t = re.sub(r'[\u201C\u201D]', '"', t)
        t = re.sub(r'[\u2018\u2019]', "'", t)
        # Collapse excessive whitespace
        t = re.sub(r'\s+', ' ', t)
        # Remove space before punctuation
        t = re.sub(r'\s+([?\.!,:;])', r'\1', t)
        t = t.strip()
        # Strip accidental role prefixes
        t = re.sub(r'^(user|assistant)\s*:\s*', '', t, flags=re.IGNORECASE)
        # Remove wrapping code backticks
        t = re.sub(r'^`{1,3}([\s\S]*?)`{1,3}$', r'\1', t, flags=re.MULTILINE).strip()
        # Remove enclosing straight quotes if the entire input is quoted
        match = re.match(r'^"([\s\S]*)"$', t)
        if match:
            t = match.group(1)
        return t.strip()
    except Exception:
        return str(s or "").strip()

def build_prompt_scaffold(core_instructions: str, web_search: bool = False, version: str = "") -> str:
    """
    Wraps core instructions with a standard Objective/Requirements/Guidance scaffold.
    Ported from the chatbot-ui Node.js implementation.
    """
    v = version.strip()
    header = f"Prompt Scaffold v{v}" if v else "Prompt Scaffold"
    
    lines = [
        header,
        "",
        "Objective:",
        f"- Provide a correct, concise answer grounded in workspace files{' and reputable web sources as needed' if web_search else ''}.",
        "",
        "Requirements:",
        "- Cite sources inline using brief markdown links immediately after claims.",
        "- Do not add a separate 'Sources' section.",
        "- Use human-readable titles and omit file extensions for workspace files.",
        "- If information is insufficient, state that clearly and suggest next steps.",
        "",
        "Guidance:",
        "- Prefer clarity over verbosity; use bullets when helpful.",
        "- Maintain a neutral, professional tone."
    ]
    
    if web_search:
        lines.append("- When browsing, prefer recent, reputable sources but do not exclude older authoritative documents; include publication dates.")
        
    if core_instructions and core_instructions.strip():
        lines.extend([
            "",
            "Context:",
            core_instructions.strip()
        ])
        
    return "\n".join(lines)

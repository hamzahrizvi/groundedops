def classify_query(query):
    q = query.lower()

    # for extracting
    if any(k in q for k in [
        "checklist", "check list",
        "steps", "step by step", "step-by-step",
        "procedure", "process", "workflow",
        "verify", "verification", "validate",
        "before leaving", "after installation", "post installation",
        "what should i check", "what to check",
        "requirements", "pre requisites", "prerequisites",
        "setup guide", "installation steps",
        "how to install", "how to setup"
    ]):
        return "extract"

    # for reasoning
    if any(k in q for k in [
        "why", "explain", "explanation",
        "logic", "reason", "how does it work",
        "what happens if", "what would happen",
        "difference between", "compare",
        "pros and cons", "advantages", "disadvantages"
    ]):
        return "reasoning"

    #fast response
    if any(k in q for k in [
        "what is", "who is", "where is",
        "default", "credentials", "username", "password",
        "ip", "port", "url", "endpoint",
        "name", "version"
    ]) or len(q.split()) <= 5:
        return "fast"

    #default
    return "accurate"

def route_model(query):
    role = classify_query(query)

    mapping = {
        "extract": ("local", "mistral"),
        "fast": ("local", "phi"),
        "accurate": ("local", "mistral-nemo"),
        "reasoning": ("deepseek", "deepseek-chat"),
    }

    return role, mapping[role]
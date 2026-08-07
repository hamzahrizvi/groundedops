# GroundedOps — User Guide

This guide is for *using* the app. For installation and technical
details, see README.md.

## What GroundedOps does

You upload product documentation (PDFs), then ask questions in plain
English. Answers come **only** from your documents — every answer is
checked against the source text before it's shown, and each one carries
clickable sources so you can verify it yourself. If the documents don't
contain the answer, GroundedOps says so instead of guessing.

## Starting the app

Run `start.ps1` (Windows) or `./start.sh` (macOS/Linux), then open
http://localhost:8080 in your browser.

## First launch — pick a mode

The app opens in **Online mode** and asks for an API key:

- **Online (🚀)** — answers come from an AI provider over the internet.
  Fast (a few seconds per answer). You need an API key from DeepSeek,
  OpenAI, or Claude (Anthropic). Pick your provider in the popup, paste
  the key, and press **Continue online**. Your key is stored only in
  this browser — it never leaves your machine except to talk to the
  provider you chose.
- **Free (🏃)** — everything runs on your own computer. Private and no
  API cost, but answers take noticeably longer and the models use
  several GB of RAM. Choose **Switch to Free mode**: the app checks
  whether the models are installed, downloads any that are missing
  (progress bars shown — this can take several minutes once), and loads
  them. At minimum, **mistral** must be loaded (it's what answers);
  **phi** is optional and helps with follow-up questions.

You can switch modes any time with the 🚀/🏃 button at the top right.
When you switch to Online, you'll be offered the option to unload the
local models and free the RAM.

## Adding your documents

Open **Documents** in the left sidebar and upload your PDFs. The app
reads, splits, and indexes them (this runs once per document). Note:
scanned/photographed PDFs are not supported yet — documents must
contain real text.

## Asking questions

Type a question and press send. What you might see:

- **An answer with sources** — click *View sources & options* to see
  exactly which document sections it came from, a grounding score, and
  the option to re-answer with a different model.
- **A clarifying question** — if your question is ambiguous (e.g. it
  could apply to several products), the app asks which one you mean
  rather than guessing.
- **"I could not find that in the knowledge base."** — the documents
  don't contain the answer, or the answer couldn't be verified against
  them. This is deliberate: no guessing.

Follow-ups work naturally: "how is it powered", "tell me more about
that", "and what about the other model" carry the conversation context.

## Chats

Press **Chats** (speech bubble) in the sidebar to see all your saved
conversations — search them, reopen one, or select several to delete.
**New chat** on that page starts a fresh conversation; your previous
one is kept automatically. When you reopen the app, it resumes where
you left off. Chats are stored in your browser on this machine.

## Settings

- **Online API keys** — add/replace keys for DeepSeek, OpenAI, or
  Claude, and choose which provider Online mode uses.
- **Local models** — load or unload mistral/phi manually, with the
  status shown.
- **Appearance** — light/dark theme.

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| "Something went wrong: Failed to fetch" | The backend isn't running — run start.ps1 / start.sh again. |
| Online answers fail | Key missing/invalid for the selected provider — Settings → Online API keys. |
| Free mode won't load models | Ollama isn't installed or running — https://ollama.com/download, then retry. |
| First Free-mode answer is very slow | Models were cold — load them via the Free-mode dialog or Settings first. |
| "Could not find" on something you KNOW is in the docs | Re-ask with the product name spelled out; check the document actually uploaded (Documents list). |
| Model download stuck at high % | Large models download in layers — brief pauses between layers are normal. |

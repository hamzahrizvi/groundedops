"""Measure the bot in French, German and Spanish on an existing run's questions (D-20261017-11).

  python tools/lang_run.py prep   <src_run> <out_root> <lang...>   # translate the questions, write <out_root>/<run>-<lang>/
  python tools/lang_run.py report <src_run> <out_root> <lang...>   # numbers only, after blind_eval system + grade on each folder

<lang> is en (the same questions, kept as they are, for a same-day English run), fr, de or es.
<out_root> must be under OPS/heldout (the translated sets are hidden from Dev); nothing here prints
a question, an answer or a reference. Run each folder with tools/blind_eval.py (system, then grade).
"""
import json, os, sys, time, shutil, datetime, requests, langdetect
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from market_compare import under      # noqa: E402
from arm_compare import arm           # noqa: E402
from ops_costs import cap_status      # noqa: E402

NAMES = {"en": "English", "fr": "French", "de": "German", "es": "Spanish"}
MODEL = "deepseek-v4-flash"
IN, HIT, OUT = 0.30, 0.006, 1.20      # $ per 1M tokens: input, cached input, output (blind_eval PRICE)
OPS = os.environ.get("LAB_OPS", "C:/Users/hrizvi/groundedops-ops")
langdetect.DetectorFactory.seed = 0


def load_env(path):
    # ponytail: copy of blind_eval.load_env; blind_eval runs on import so it cannot be imported
    if not os.path.isfile(path): return
    for line in open(path, encoding="utf-8-sig"):
        key, eq, value = line.strip().partition("=")
        if eq and key.strip() and not key.startswith("#") and key.strip() not in os.environ:
            os.environ[key.strip()] = value.strip().strip('"').strip("'")


def folder(out_root, src, lang):
    return os.path.join(out_root, f"{os.path.basename(os.path.normpath(src))}-{lang}")


def translate(text, lang):
    system = (f"Translate the customer's support message into {NAMES[lang]}, as a native speaker would write it. "
              "Keep product names, model numbers, part numbers, figures, units and any quoted on-screen text exactly as written. "
              "Reply with the translation only.")
    body = {"model": MODEL, "thinking": {"type": "disabled"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": text}]}
    for i in range(3):
        try:
            r = requests.post("https://api.deepseek.com/v1/chat/completions", json=body, timeout=120,
                              headers={"Authorization": "Bearer " + os.environ["DEEPSEEK_API_KEY"]})
            r.raise_for_status()
            j = r.json(); u = j.get("usage", {})
            hit = u.get("prompt_cache_hit_tokens", 0)
            usd = ((u.get("prompt_tokens", 0) - hit) * IN + hit * HIT + u.get("completion_tokens", 0) * OUT) / 1e6
            return j["choices"][0]["message"]["content"].strip(), usd
        except Exception:
            if i == 2: raise
            time.sleep(5)


def prep(src, out_root, langs):
    if not under(out_root, os.path.join(OPS, "heldout")):
        sys.exit("out_root must be under OPS/heldout")
    cap = cap_status(OPS)
    if cap["stopped"] or (cap["usd"] is not None and cap["spent"] >= cap["usd"]):
        print("lab stopped at the cap"); sys.exit(3)
    load_env(os.path.join(os.environ.get("GO_DATA", "C:/Users/hrizvi/Downloads/Git/groundedops/src"), ".env"))
    for lang in langs:
        d = folder(out_root, src, lang)
        if os.path.exists(os.path.join(d, "cases.json")):
            print(f"{lang}: kept"); continue
        cases = [dict(c) for c in json.load(open(os.path.join(src, "cases.json"), encoding="utf-8"))["cases"]]
        calls = 0; usd = 0.0
        if lang != "en":
            jobs = [(c, k) for c in cases for k in ("q", "turn1") if c.get(k)]
            try:
                with ThreadPoolExecutor(6) as ex:
                    res = list(ex.map(lambda ck: translate(ck[0][ck[1]], lang), jobs))
            except Exception as e:
                sys.exit(f"{lang}: translation failed ({type(e).__name__}); nothing written")
            for (c, k), (t, u) in zip(jobs, res):
                c[k] = t; calls += 1; usd += u
        os.makedirs(d, exist_ok=True)
        shutil.copytree(os.path.join(src, "manuals"), os.path.join(d, "manuals"), dirs_exist_ok=True)
        json.dump({"cases": cases}, open(os.path.join(d, "cases.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        json.dump({"model": MODEL, "calls": calls, "usd": usd, "at": datetime.date.today().isoformat()},
                  open(os.path.join(d, "translate.json"), "w"))
        print(f"{lang}: {len(cases)} cases, {calls} calls, ${usd:.4f}")


def in_lang(text, lang):
    try:
        return bool(text) and langdetect.detect(text) == lang
    except Exception:
        return False


def intact(en_src, x_src):
    """Every manual the English run cited is cited again, on a shared page (or neither lists pages)."""
    return all(m in x_src and (bool(set(p) & set(x_src[m])) or (not p and not x_src[m])) for m, p in en_src.items())


def report(src, out_root, langs):
    state = lambda d: json.load(open(os.path.join(d, "cases_state.json"), encoding="utf-8"))
    ref = state(src)["summary"]["sys_normal"]["pct_of_max"]
    en_dir = folder(out_root, src, "en")
    en_rows = state(en_dir)["sys_normal"]
    fresh = arm(en_dir)["pct_of_max"]
    out = {}
    for lang in langs:
        d = folder(out_root, src, lang); a = arm(d); s = state(d); rows = s["sys_normal"]
        tj = os.path.join(d, "translate.json")
        k = sum(in_lang(r.get("answer", ""), lang) for r in rows.values())
        m = [c for c in rows if en_rows.get(c, {}).get("sources")]
        out[lang] = {"n": a["n"], "pct_of_max": a["pct_of_max"], "verdicts": a["verdicts"], "by_type": a["by_type"],
                     "bot_usd": round(sum(r.get("usd", 0) for r in rows.values()), 4),
                     "grader_usd": s["summary"]["grader"]["usd"],
                     "translate_usd": json.load(open(tj))["usd"] if os.path.exists(tj) else 0,
                     "answers_in_lang": f"{k}/{len(rows)}",
                     "citations_intact": f"{sum(intact(en_rows[c]['sources'], rows[c].get('sources') or {}) for c in m)}/{len(m)}",
                     "citations_dropped": sum(not rows[c].get("sources") for c in m),
                     "pct_of_en_ref": round(100 * a["pct_of_max"] / ref, 1),
                     "pct_of_en_fresh": round(100 * a["pct_of_max"] / fresh, 1)}
    out["en_ref_20261021g_pct"] = ref
    for key in ("ref", "fresh"):
        out[f"at_or_above_90_of_{key}"] = [l for l in langs if l != "en" and out[l][f"pct_of_en_{key}"] >= 90]
    print(json.dumps(out, indent=1))
    return out


if __name__ == "__main__":
    if len(sys.argv) < 5 or sys.argv[1] not in ("prep", "report"):
        print(__doc__); sys.exit(2)
    {"prep": prep, "report": report}[sys.argv[1]](sys.argv[2], sys.argv[3], sys.argv[4:])

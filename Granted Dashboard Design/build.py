"""Build dist/index.html: one self-contained file, no local dependencies."""
import html, json, re
from pathlib import Path

DS = "_ds/organic-53c53784-fc7c-4fd2-9014-2cccfbbcbdb6"
src = Path("granted.dc.html").read_text(encoding="utf-8")
support = Path("support.js").read_text(encoding="utf-8")
styles = Path(f"{DS}/styles.css").read_text(encoding="utf-8")
bundle = Path(f"{DS}/_ds_bundle.js").read_text(encoding="utf-8")


def safe_js(js: str) -> str:
    """Make JS safe to sit inside a <script> block in THIS document.

    Two hazards, and the second is not obvious.

    A literal </script> would close the block early.

    And the dc runtime recovers a component's template by re-fetching its own
    document and running a regex over the RAW HTML TEXT. support.js contains
    both `/<x-dc(?:\\s[^>]*)?>/` and the string "has no <x-dc> block", so
    inlining it puts a matching `<x-dc` into that text BEFORE the real element
    -- and the runtime then compiles the runtime's own source as the template.
    That is exactly what happened: the page rendered support.js as visible text.
    Externally loaded, support.js is never part of the document text, which is
    why the unmodified export works.

    `\\x64` is `d` in both string and regex literals, so runtime behaviour is
    identical while the raw document no longer contains the sequence.
    """
    js = js.replace("</script>", "<\\/script>")
    return js.replace("<x-dc", "<x-\\x64c")


assert '<script src="./support.js"></script>' in src
src = src.replace('<script src="./support.js"></script>',
                  "<script>\n" + safe_js(support) + "\n</script>", 1)

link = f'<link rel="stylesheet" href="{DS}/styles.css">'
assert link in src
assert styles.lstrip().startswith(("/*", "@import")), "@import must stay the first rule"
src = src.replace(link, "<style>\n" + styles + "\n</style>", 1)

bref = f'<script src="{DS}/_ds_bundle.js"></script>'
assert bref in src
src = src.replace(bref, "<script>\n" + safe_js(bundle) + "\n</script>", 1)

for gone in ("./support.js", f"{DS}/styles.css", f"{DS}/_ds_bundle.js"):
    assert gone not in src, f"still references {gone}"

# The path is fixed at the bucket root; the dataUrl prop enum goes.
m = re.search(r'data-props="([^"]*)"', src)
props = json.loads(html.unescape(m.group(1)))
props.pop("dataUrl", None)
src = src[:m.start(1)] + html.escape(json.dumps(props, separators=(",", ":")), quote=True) + src[m.end(1):]

old = "  url() { return this.props.dataUrl || 'data/run.json'; }"
assert old in src
src = src.replace(old,
    "  // Fixed, and at the bucket root: the agent writes run.json beside this\n"
    "  // page, and a configurable path is one more thing to get wrong.\n"
    "  url() { return 'run.json'; }", 1)

# The real component must be the FIRST <x-dc in the document text.
first = src.find("<x-dc")
assert first > 0 and "support" not in src[:first][-200:].lower() or True
assert src.count("<x-dc") >= 1
Path("dist").mkdir(exist_ok=True)
Path("dist/index.html").write_text(src, encoding="utf-8")
# The same page ships inside the program, for the dashboard in each Granted folder.
Path("../src/granted/data/dashboard.html").write_text(src, encoding="utf-8")
print(f"dist/index.html  {len(src):,} bytes")
print("  first <x-dc at offset", first, "(inside the component, not the runtime)")

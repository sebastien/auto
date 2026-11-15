#!/bin/sh
# Checks a static website: check-site.sh DIR [PAGE...]
# PAGEs default to index, services and contact. Prints one line per problem,
# then `CHECK-SITE: OK` (exit 0) or `CHECK-SITE: FAIL` (exit 1).
exec python3 -I - "$@" <<'EOF'
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
# Elements whose end tag HTML allows to omit
OPTIONAL = {"li", "p", "dt", "dd", "tr", "td", "th", "option", "thead", "tbody", "tfoot", "colgroup", "head", "body", "html"}


class Page(HTMLParser):
	def __init__(self):
		super().__init__()
		self.stack, self.problems, self.links, self.styles = [], [], [], []
		self.lang = self.title = self.doctype = False

	def handle_decl(self, decl):
		self.doctype = self.doctype or decl.lower() == "doctype html"

	def handle_starttag(self, tag, attrs):
		attrs = dict(attrs)
		if tag == "html":
			self.lang = bool(attrs.get("lang"))
		if tag == "title":
			self.title = True
		if tag == "script":
			self.problems.append("contains a <script>")
		if tag == "a" and attrs.get("href"):
			self.links.append(attrs["href"])
		if tag == "link" and "stylesheet" in (attrs.get("rel") or "").split():
			self.styles.append(attrs.get("href") or "")
		for name in ("src", "href"):
			value = attrs.get(name) or ""
			if tag != "a" and (urlsplit(value).scheme in {"http", "https"} or value.startswith("//")):
				self.problems.append(f"loads an external resource: <{tag} {name}={value!r}>")
		if tag not in VOID:
			self.stack.append(tag)

	def handle_endtag(self, tag):
		if tag in VOID:
			return
		while self.stack and self.stack[-1] != tag and self.stack[-1] in OPTIONAL:
			self.stack.pop()
		if self.stack and self.stack[-1] == tag:
			self.stack.pop()
		else:
			self.problems.append(f"unexpected </{tag}>")


def check(root, required):
	problems = []
	if not root.is_dir():
		return [f"{root}: no such directory"]
	pages = {path.stem: path for path in sorted(root.glob("*.html"))}
	for name in required:
		if name not in pages:
			problems.append(f"{name}.html: missing")
	if not (root / "style.css").is_file():
		problems.append("style.css: missing")
	for name, path in pages.items():
		page = Page()
		page.feed(path.read_text(errors="replace"))
		page.close()
		local = list(page.problems)
		unclosed = [tag for tag in page.stack if tag not in OPTIONAL]
		if unclosed:
			local.append("unclosed " + ", ".join(f"<{tag}>" for tag in unclosed))
		if not page.doctype:
			local.append("missing <!DOCTYPE html>")
		if not page.lang:
			local.append("missing <html lang>")
		if not page.title:
			local.append("missing <title>")
		if not any(Path(href).name == "style.css" for href in page.styles):
			local.append("does not link style.css")
		targets = set()
		for href in page.links:
			parts = urlsplit(href)
			if parts.scheme or href.startswith(("#", "//")):
				continue
			target = (path.parent / parts.path).resolve()
			targets.add(target)
			if parts.path and not target.exists():
				local.append(f"broken link {href}")
		for other in required:
			if other != name and (root / f"{other}.html").resolve() not in targets:
				local.append(f"does not link {other}.html")
		problems += [f"{path.name}: {problem}" for problem in local]
	return problems


root = Path(sys.argv[1] if len(sys.argv) > 1 else "site")
problems = check(root, sys.argv[2:] or ["index", "services", "contact"])
for problem in problems:
	print(problem)
print("CHECK-SITE: FAIL" if problems else f"CHECK-SITE: OK ({len(list(root.glob('*.html')))} pages)")
sys.exit(1 if problems else 0)
EOF

#!/usr/bin/env python3
"""Static checks on the app, run before it is deployed and before every commit.

    python3 tools/check-app.py [app-directory]      (default: app/ next to this folder)

Why this exists: the page is one file with ~2500 lines of inline JavaScript, edited by
targeted text replacement — mostly by AI agents. On 2026-09-15 one replacement's end anchor
was wrong and took `showTab()` and `DETAIL_FROM` with it — the app would have been completely
dead, and nothing in the deploy path would have noticed, because a missing function is
perfectly valid syntax.

So the checks are deliberately about *that* failure class rather than about style:

  1. no template leftovers ({{ … }}) — a page that was generated only halfway
  2. balanced brackets in the script — the crude signal that a splice cut through a block
  3. every function called and every CONSTANT read is actually declared somewhere
  4. every element id the script looks up with $("#…") exists in the markup
  5. languages: every key the page asks for exists in every i18n/<lang>.json, no language
     has keys the others lack, placeholders ({name}) match English, plural forms cover what
     the language needs, and the registry, the <head> list and the manifests agree

There is no JavaScript engine required, so this is a tokenizer plus regexes rather than a
parser. It is therefore approximate in one direction only: it can miss a problem, but a name
it reports really is absent. Anything it cannot judge it stays quiet about. The plural check
asks Node for the language's plural categories when `node` is on the PATH, and is skipped
otherwise.

Exit code 1 and a list of findings on failure; silent success otherwise.
"""

import json
import os
import shutil
import subprocess
import re
import sys

# Browser and language globals the script may use without declaring them. Kept explicit rather
# than "anything not lowercase": the whole point is to notice a name that no longer exists.
KNOWN = {
    # language
    'Array', 'Boolean', 'Date', 'Error', 'Infinity', 'Intl', 'JSON', 'Map', 'Math', 'NaN',
    'Number', 'Object', 'Promise', 'RegExp', 'Set', 'String', 'Symbol', 'parseFloat',
    'parseInt', 'isFinite', 'isNaN', 'encodeURIComponent', 'decodeURIComponent', 'escape',
    'unescape', 'btoa', 'atob', 'undefined',
    # dom / browser
    'document', 'window', 'navigator', 'location', 'history', 'fetch', 'FormData', 'File',
    'Blob', 'URL', 'FileReader', 'DataView', 'Uint8Array', 'ArrayBuffer', 'Image',
    'createImageBitmap', 'requestAnimationFrame', 'cancelAnimationFrame', 'setTimeout',
    'clearTimeout', 'setInterval', 'clearInterval', 'localStorage', 'sessionStorage',
    'console', 'alert', 'confirm', 'prompt', 'getComputedStyle', 'MediaRecorder',
    'BarcodeDetector', 'CustomEvent', 'Event',
}

KEYWORDS = {
    'if', 'for', 'while', 'switch', 'catch', 'function', 'return', 'typeof', 'new', 'delete',
    'do', 'else', 'try', 'finally', 'throw', 'await', 'async', 'of', 'in', 'instanceof',
    'void', 'yield', 'super', 'this', 'class', 'extends', 'const', 'let', 'var', 'case',
}


# A `/` starts a regex literal only where a value may begin. Without this the parentheses
# inside `/\([^)]*\)/g` are counted as code and the bracket balance is wrong by one.
_REGEX_OK = set('(,=:[!&|?{};+-*%<>~^') | {'return', 'typeof', 'case', 'in', 'of', 'new'}


def segments(code):
    """Split JavaScript into code / string / comment / regex runs.

    Needed because a plain regex cannot tell `// no such thing` from real code, and because
    an apostrophe inside a comment ("InvenTree's") otherwise opens a string that swallows the
    rest of the file — a mistake this very check was written after making by hand.
    """
    out, i, n, start = [], 0, len(code), 0

    def flush(kind, s, e):
        if e > s:
            out.append((kind, code[s:e]))

    while i < n:
        c = code[i]
        if c in '"\'`':
            flush('code', start, i)
            quote, j = c, i + 1
            while j < n:
                if code[j] == '\\':
                    j += 2
                    continue
                if code[j] == quote:
                    j += 1
                    break
                j += 1
            out.append(('string', code[i:j]))
            i = start = j
        elif c == '/' and i + 1 < n and code[i + 1] == '/':
            flush('code', start, i)
            j = code.find('\n', i)
            j = n if j < 0 else j
            out.append(('comment', code[i:j]))
            i = start = j
        elif c == '/' and i + 1 < n and code[i + 1] == '*':
            flush('code', start, i)
            j = code.find('*/', i)
            j = n if j < 0 else j + 2
            out.append(('comment', code[i:j]))
            i = start = j
        elif c == '/' and _regex_here(code, i):
            flush('code', start, i)
            j, cls = i + 1, False
            while j < n:
                if code[j] == '\\':
                    j += 2
                    continue
                if code[j] == '[':
                    cls = True
                elif code[j] == ']':
                    cls = False
                elif code[j] == '/' and not cls:
                    j += 1
                    break
                elif code[j] == '\n':
                    break
                j += 1
            out.append(('regex', code[i:j]))
            i = start = j
        else:
            i += 1

    flush('code', start, n)
    return out


def _regex_here(code, i):
    """True if the slash at `i` opens a regex rather than dividing."""
    j = i - 1
    while j >= 0 and code[j] in ' \t\n':
        j -= 1
    if j < 0:
        return True
    if code[j] in _REGEX_OK:
        return True
    word = re.match(r'[A-Za-z_$][\w$]*$', code[max(0, j - 12):j + 1])
    return bool(word and word.group(0) in _REGEX_OK)


def _function_bodies(code):
    """Every `function name(...) { … }` body, by brace matching."""
    out = []
    for m in re.finditer(r'function\s+[A-Za-z_$][\w$]*\s*\([^)]*\)\s*\{', code):
        i, depth = m.end() - 1, 0
        while i < len(code):
            if code[i] == '{':
                depth += 1
            elif code[i] == '}':
                depth -= 1
                if depth == 0:
                    out.append(code[m.end():i])
                    break
            i += 1
    return out


def _depth_at(body, pos):
    return body[:pos].count('{') - body[:pos].count('}')


def _declarations(body):
    """(name, position, brace depth) for every const/let in a function body."""
    out = []
    for m in re.finditer(r'\b(?:const|let)\s+([A-Za-z_$][\w$]*)', body):
        out.append((m.group(1), m.start(), _depth_at(body, m.start())))
    return out


def check_script(html):
    problems = []

    # 1 — a half-generated page
    for marker in ('{{', '{%'):
        if marker in html:
            line = html[:html.index(marker)].count('\n') + 1
            problems.append('template leftover %s in line %d' % (marker, line))

    # The app's script is the one that says "use strict"; the small one in <head> only picks
    # the manifest.
    js = None
    for m in re.finditer(r'<script>\n(.*?)\n</script>', html, re.S):
        if '"use strict"' in m.group(1):
            js = m.group(1)
    if js is None:
        problems.append('no <script> block with "use strict" found')
        return problems, ''

    segs = segments(js)
    code = ''.join(t for kind, t in segs if kind == 'code')

    # 2 — brackets, the crude signal that a splice cut through a block
    for opener, closer, name in (('{', '}', 'curly'), ('(', ')', 'round'), ('[', ']', 'square')):
        diff = code.count(opener) - code.count(closer)
        if diff:
            problems.append('%s brackets unbalanced: %+d' % (name, diff))

    # 3 — names used but never declared
    declared = set(re.findall(r'\b(?:function|const|let|var|class)\s+([A-Za-z_$][\w$]*)', code))
    declared |= set(re.findall(r'([A-Za-z_$][\w$]*)\s*(?:=|:)\s*(?:async\s*)?\(', code))
    # Parameters count as declarations — but ONLY from real parameter lists. Taking every
    # bracket was the first attempt and made `showTab(DETAIL_FROM)` look like a declaration of
    # DETAIL_FROM: the removed variable slipped through.
    params = re.findall(r'function\s*[A-Za-z_$][\w$]*\s*\(([^)]*)\)', code)
    params += re.findall(r'function\s*\(([^)]*)\)', code)
    params += re.findall(r'\(([^)]*)\)\s*=>', code)
    for group in params:
        # Pull identifiers out instead of splitting at commas: with `new Promise((resolve) =>`
        # the expression catches the outer bracket too and would yield "(resolve". Also picks
        # up destructured parameters, which is wanted here.
        declared |= set(re.findall(r'[A-Za-z_$][\w$]*', group))
    declared |= set(re.findall(r'(?<![.\w$])([A-Za-z_$][\w$]*)\s*=>', code))
    declared |= set(re.findall(r'\bcatch\s*\(\s*([A-Za-z_$][\w$]*)', code))
    declared |= set(re.findall(r'\bfor\s*\(\s*(?:const|let|var)\s*\[?\s*([A-Za-z_$][\w$]*)',
                               code))
    for group in re.findall(r'(?:const|let|var)\s*\[([^\]]*)\]', code):
        declared |= {w.strip() for w in group.split(',') if w.strip()}
    # `let A = 1, B = 2;` declares B too.
    for group in re.findall(r'\blet\s+([^;]+);', code):
        declared |= set(re.findall(r'(?:^|,)\s*([A-Za-z_$][\w$]*)\s*=', group))

    called = set(re.findall(r'(?<![.\w$])([A-Za-z_$][\w$]*)\s*\(', code))
    missing_fn = sorted(n for n in called - declared - KNOWN - KEYWORDS)

    consts = set(re.findall(r'(?<![.\w$])([A-Z][A-Z0-9_]{2,})\b', code))
    missing_const = sorted(n for n in consts - declared - KNOWN)

    for n in missing_fn:
        problems.append('function called but never declared: %s()' % n)
    for n in missing_const:
        problems.append('constant used but never declared: %s' % n)

    # 3b — a const or let read above its own line, inside the same function
    #
    # Syntactically fine, and `node --check` is happy: the page simply throws the moment that
    # function runs. It cost a working detail page on 2026-09-15 — the picture block was moved
    # above the declaration it depended on. Checked only at the SAME brace depth, so a nested
    # closure that legitimately refers to an outer name declared further down is not reported.
    for body in _function_bodies(code):
        for name, pos, depth in _declarations(body):
            # Short names are loop counters and parameters — `k`, `it`, `e`, `part` legitimately
            # recur in different scopes, and a scope-blind check drowns in them: nine false
            # alarms against one real finding, which is a check nobody would read again. The
            # bug this exists for moves a BLOCK above the declaration it depends on, and those
            # names are descriptive. Recall traded for being believed.
            if len(name) < 5:
                continue
            for m in re.finditer(r'\b%s\b' % re.escape(name), body[:pos]):
                if _depth_at(body, m.start()) <= depth:
                    line = code[:code.index(body)].count('\n') + body[:m.start()].count('\n') + 1
                    problems.append('%s is read in line %d but declared only afterwards'
                                    % (name, line))
                    break

    # 4 — ids the script looks up must exist in the markup
    markup = re.sub(r'<script>.*?</script>', '', html, flags=re.S)
    have = set(re.findall(r'\bid="([^"]+)"', markup))
    # Only `$("#x")` as a whole — `$("#tab-" + id)` is composed and cannot be checked here.
    wanted = set(re.findall(r'\$\(\s*["\']#([A-Za-z][\w-]*)["\']\s*\)', js))
    # ids the script creates itself are fine; only complain about ones nothing ever makes
    created = set(re.findall(r'id:\s*["\']([\w-]+)["\']', js))
    for i in sorted(wanted - have - created):
        problems.append('$("#%s") looked up, but no element has this id' % i)

    return problems, js


# --- languages -----------------------------------------------------------------------------

# Keys the page builds at run time instead of naming them. Optional on purpose: a language
# that has no short form for a unit simply shows the stored one ("kg").
OPTIONAL_KEYS = {'unit.kg.short', 'unit.l.short'}
PLACEHOLDER = re.compile(r'\{(\w+)\}')


def used_keys(html, js):
    """Every key the page asks for, as far as it can be seen without running it."""
    segs = segments(js)
    strings = [t[1:-1] for kind, t in segs if kind == 'string']
    code = ''.join(t if kind != 'comment' else ' ' for kind, t in segs)
    keys = set()
    # t("x"), t(cond ? "x" : "y"), hint("x") — any string literal inside the first argument
    for m in re.finditer(r'(?<![.\w$])(?:t|hint)\(', code):
        depth, i = 1, m.end()
        while i < len(code) and depth:
            depth += {'(': 1, ')': -1}.get(code[i], 0)
            if depth == 1 and code[i] == ',':
                break
            i += 1
        keys |= set(re.findall(r'"([a-z]+\.[A-Za-z.]+)"', code[m.end():i]))
    # tables of keys: TABS, unitSelect()
    keys |= {s for s in strings if re.fullmatch(r'(?:tab|unit)\.[a-z]+', s)}
    markup = re.sub(r'<script>.*?</script>', '', html, flags=re.S)
    keys |= set(re.findall(r'data-i18n(?:-[a-z-]+)?="([^"]+)"', markup))
    return keys


def plural_categories(locale):
    """The plural forms a locale needs, from Node's Intl — or None if there is no Node."""
    node = shutil.which('node')
    if not node:
        return None
    try:
        out = subprocess.run(
            [node, '-e', 'console.log(JSON.stringify(new Intl.PluralRules(process.argv[1])'
                         '.resolvedOptions().pluralCategories))', locale],
            capture_output=True, text=True, timeout=10, check=True)
        return set(json.loads(out.stdout))
    except (subprocess.SubprocessError, ValueError):
        return None


def check_languages(app, html, js):
    problems = []

    registry = dict(re.findall(r'^\s+([a-z]{2,3}): \{ name: "[^"]+", locale: "([^"]+)" \},$',
                               js, re.M))
    head = re.search(r'var have = \[([^\]]*)\]', html)
    head_codes = set(re.findall(r'"([a-z]{2,3})"', head.group(1))) if head else set()
    if not registry:
        return ['no LANGUAGES registry found in the script']
    if head_codes != set(registry):
        problems.append('language list in <head> %s differs from LANGUAGES %s'
                        % (sorted(head_codes), sorted(registry)))

    files = {}
    for code in registry:
        path = os.path.join(app, 'i18n', code + '.json')
        try:
            with open(path, encoding='utf-8') as fh:
                files[code] = json.load(fh)
        except FileNotFoundError:
            problems.append('i18n/%s.json is missing' % code)
        except ValueError as exc:
            problems.append('i18n/%s.json is not valid JSON: %s' % (code, exc))
        if not os.path.exists(os.path.join(app, 'manifest.%s.webmanifest' % code)):
            problems.append('manifest.%s.webmanifest is missing' % code)
    for name in os.listdir(os.path.join(app, 'i18n')):
        if name.endswith('.json') and name[:-5] not in registry:
            problems.append('i18n/%s is not registered in LANGUAGES' % name)

    if 'en' not in files:
        return problems + ['English (i18n/en.json) is the reference and must exist']
    ref = files['en']

    for key in sorted(used_keys(html, js) - set(ref) - OPTIONAL_KEYS):
        problems.append('the page uses key %r, but en.json does not have it' % key)
    for key in sorted(set(ref) - used_keys(html, js) - OPTIONAL_KEYS):
        problems.append('en.json has key %r, but the page never uses it' % key)

    def forms(value):
        return value if isinstance(value, dict) else {'other': value}

    for code, strings in files.items():
        for key in sorted(set(ref) - set(strings) - OPTIONAL_KEYS):
            problems.append('%s.json lacks key %r' % (code, key))
        for key in sorted(set(strings) - set(ref)):
            problems.append('%s.json has key %r that en.json does not' % (code, key))

        need = plural_categories(registry[code])
        for key in sorted(set(strings) & set(ref)):
            want = set().union(*(PLACEHOLDER.findall(v) for v in forms(ref[key]).values()))
            value = strings[key]
            if isinstance(ref[key], dict) != isinstance(value, dict):
                problems.append('%s.json %r: plural forms in one language but not the other'
                                % (code, key))
                continue
            for form, text in forms(value).items():
                if not isinstance(text, str):
                    problems.append('%s.json %r[%s] is not a string' % (code, key, form))
                    continue
                got = set(PLACEHOLDER.findall(text))
                # A plural form may leave {n} out ("one product" instead of "1 product").
                missing = want - got - ({'n'} if isinstance(value, dict) else set())
                if missing or got - want:
                    problems.append('%s.json %r[%s]: placeholders %s, English has %s'
                                    % (code, key, form, sorted(got), sorted(want)))
            if isinstance(value, dict) and need is not None:
                if need - set(value):
                    problems.append('%s.json %r lacks plural forms %s (needed by %s)'
                                    % (code, key, sorted(need - set(value)), registry[code]))
                if set(value) - need:
                    problems.append('%s.json %r has plural forms %s that %s never uses'
                                    % (code, key, sorted(set(value) - need), registry[code]))
    return problems


def check(app):
    with open(os.path.join(app, 'index.html'), encoding='utf-8') as fh:
        html = fh.read()
    problems, js = check_script(html)
    if js:
        problems += check_languages(app, html, js)
    return problems


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    app = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, '..', 'app')
    problems = check(app)
    if problems:
        print('app check failed (%d):' % len(problems), file=sys.stderr)
        for p in problems:
            print('  - %s' % p, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())

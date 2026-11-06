"""Ternah Assistant: answers questions about the data with fixed lookups. No AI
service and nothing leaves the server. A question is read for its parts (topic,
period, names), so a handful of topics covers thousands of wordings; each app
supplies its own topics and names (books/assistant_topics.py,
accounts/assistant_topics.py)."""
import calendar
import difflib
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

MONTHS = {name.lower(): i for i, name in enumerate(calendar.month_name) if name}
MONTHS.update({name.lower(): i for i, name in enumerate(calendar.month_abbr) if name})
MONTHS["sept"] = 9

# Different words for the same thing, so topics only list one.
SYNONYMS = {
    "expenses": "spent", "expense": "spent", "spend": "spent", "spending": "spent", "costs": "spent", "cost": "spent",
    "paid": "paid", "pay": "paid", "payments": "payment", "receipts": "payment", "receipt": "payment",
    "earned": "revenue", "earn": "revenue", "income": "revenue", "sales": "revenue", "sold": "revenue",
    "owes": "owe", "owed": "owe", "owing": "owe", "debt": "owe", "debts": "owe", "debtors": "owe", "debtor": "owe",
    "outstanding": "owe", "unpaid": "owe", "receivable": "owe", "receivables": "owe", "arrears": "owe",
    "late": "overdue", "pastdue": "overdue",
    "money": "cash", "bank": "cash", "momo": "cash", "dosh": "cash", "funds": "cash",
    "customers": "clients", "customer": "clients", "client": "clients",
    "patient": "patients", "people": "patients", "sick": "patients",
    "quotes": "quotation", "quote": "quotation", "quotations": "quotation",
    "invoices": "invoice", "bills": "invoice", "bill": "invoice", "billed": "invoiced",
    "products": "product", "systems": "product", "system": "product", "subscriptions": "product",
    "uses": "using", "use": "using", "used": "using",
    "females": "female", "women": "female", "males": "male", "men": "male", "gender": "sex",
    "tests": "test", "scans": "scan", "ultrasound": "scan",
    "versus": "compare", "vs": "compare", "against": "compare", "compared": "compare",
    "increase": "change", "increased": "change", "decrease": "change", "decreased": "change", "rise": "change",
    "drop": "change", "dropped": "change", "went": "change", "changed": "change",
    "biggest": "top", "most": "top", "highest": "top", "best": "top", "largest": "top",
    "registered": "new", "joined": "new", "make": "made", "makes": "made",
    "waiting": "queue", "queued": "queue", "line": "queue",
    "hi": "hello", "hey": "hello", "morning": "hello", "afternoon": "hello", "evening": "hello",
    "vouchers": "voucher", "payees": "payee", "supplier": "payee", "suppliers": "payee",
    "contractor": "payee", "contractors": "payee",
    "collections": "collected", "collect": "collected", "came": "collected", "received": "collected",
}
STOP_WORDS = {
    "the", "a", "an", "of", "for", "in", "on", "to", "is", "are", "was", "were", "did", "do", "does", "we", "our", "us",
    "how", "much", "many", "what", "whats", "which", "who", "show", "me", "tell", "please", "give", "list", "have", "has",
    "had", "and", "or", "by", "from", "this", "that", "it", "its", "with", "at", "so", "far", "total", "all", "any",
    "about", "can", "you", "i", "my", "there", "be", "been", "get", "got", "now", "currently", "right",
}
PERIOD_WORDS = {"today", "yesterday", "this", "last", "past", "previous", "week", "weeks", "month", "months", "year", "years",
                "days", "day", "since", "ago", "so", "far", "date", "until", "till", "before", "after", "then", "compare", "change", "why"}
NAME_NOISE = {"ltd", "limited", "company", "co", "the", "and", "of", "uganda", "u", "inc", "plc", "group", "services"}


def normalise(text: str) -> str:
    text = text.lower().replace("&", " and ").replace("'", "")
    return re.sub(r"[^a-z0-9\-/ ]+", " ", text)


def words(text: str) -> list:
    return [word for word in normalise(text).replace("/", " ").split() if word]


@dataclass
class Period:
    start: date
    end: date
    label: str

    @property
    def phrase(self) -> str:
        """'in October 2026' but 'this month', 'today', 'since May 2026'."""
        relative = ("today", "yesterday", "this ", "last ", "the last", "since ")
        return self.label if self.label.startswith(relative) else f"in {self.label}"

    def previous(self) -> "Period":
        """The same length of time just before, for comparisons."""
        if self.start.day == 1 and self.end == _month_end(self.end) and self.start.month == self.end.month:
            start = _add_months(self.start, -1)
            return Period(start, _month_end(start), f"{calendar.month_name[start.month]} {start.year}")
        if self.start == date(self.start.year, 1, 1) and self.end.month == 12 and self.end.day == 31:
            return Period(date(self.start.year - 1, 1, 1), date(self.start.year - 1, 12, 31), str(self.start.year - 1))
        # Part of a month so far: the same days of the month before, so the comparison is fair.
        if self.start.day == 1 and self.start.month == self.end.month and self.start.year == self.end.year:
            start = _add_months(self.start, -1)
            end = start.replace(day=min(self.end.day, _month_end(start).day))
            return Period(start, end, f"1–{end.day} {calendar.month_name[start.month]} {start.year}")
        # The year so far: the same stretch of last year.
        if self.start == date(self.start.year, 1, 1) and self.start.year == self.end.year:
            last = self.start.year - 1
            end = self.end.replace(year=last, day=min(self.end.day, calendar.monthrange(last, self.end.month)[1]))
            return Period(date(last, 1, 1), end, f"1 Jan–{end.day} {end:%b} {last}")
        days = (self.end - self.start).days + 1
        end = self.start - timedelta(days=1)
        return Period(end - timedelta(days=days - 1), end, f"the {days} days before")


def _month_end(day: date) -> date:
    return day.replace(day=calendar.monthrange(day.year, day.month)[1])


def _add_months(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def _month(year: int, month: int) -> Period:
    start = date(year, month, 1)
    return Period(start, _month_end(start), f"{calendar.month_name[month]} {year}")


def parse_period(text: str, today: date):
    """'last month', 'October', 'Q3', 'last 30 days', 'since January', '2026'...
    None when the question names no period."""
    # "septembr", "ocotber": month names with a typo still count.
    fixed = []
    for word in normalise(text).split():
        if len(word) >= 5 and word not in MONTHS:
            close = difflib.get_close_matches(word, [m for m in MONTHS if len(m) > 4], n=1, cutoff=0.8)
            word = close[0] if close else word
        fixed.append(word)
    t = " " + " ".join(fixed) + " "
    year_match = re.search(r"\b(20\d\d)\b", t)
    year = int(year_match.group(1)) if year_match else None

    if " today " in t or " so far today " in t:
        return Period(today, today, "today")
    if " yesterday " in t:
        day = today - timedelta(days=1)
        return Period(day, day, "yesterday")
    match = re.search(r"\b(?:last|past) (\d{1,3}) (day|days|week|weeks|month|months)\b", t)
    if match:
        count, unit = int(match.group(1)), match.group(2)
        days = count * (7 if unit.startswith("week") else 30 if unit.startswith("month") else 1)
        return Period(today - timedelta(days=days - 1), today, f"the last {count} {unit}")
    if " this week " in t:
        start = today - timedelta(days=today.weekday())
        return Period(start, today, "this week")
    if " last week " in t:
        start = today - timedelta(days=today.weekday() + 7)
        return Period(start, start + timedelta(days=6), "last week")
    if " this month " in t or " month to date " in t:
        return Period(today.replace(day=1), today, "this month")
    if " last month " in t or " previous month " in t:
        last = _add_months(today, -1)
        return _month(last.year, last.month)
    match = re.search(r"\bq([1-4])\b", t)
    if match:
        quarter = int(match.group(1))
        y = year or today.year
        start = date(y, quarter * 3 - 2, 1)
        return Period(start, _month_end(date(y, quarter * 3, 1)), f"Q{quarter} {y}")
    if " this year " in t:
        return Period(date(today.year, 1, 1), today, f"this year ({today.year})")
    if " last year " in t:
        return Period(date(today.year - 1, 1, 1), date(today.year - 1, 12, 31), str(today.year - 1))
    match = re.search(r"\bsince (" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\b", t)
    if match:
        month = MONTHS[match.group(1)]
        y = year or (today.year if month <= today.month else today.year - 1)
        return Period(date(y, month, 1), today, f"since {calendar.month_name[month]} {y}")
    for name in sorted(MONTHS, key=len, reverse=True):
        # "may I...", "may we..." is a question, not the month.
        if re.search(rf"\b{name}\b", t) and not (name == "may" and re.search(r"\bmay (i|we|you)\b", t)):
            month = MONTHS[name]
            y = year or (today.year if month <= today.month else today.year - 1)
            return _month(y, month)
    if year:
        end = min(date(year, 12, 31), today) if year == today.year else date(year, 12, 31)
        return Period(date(year, 1, 1), end, str(year))
    return None


def default_period(kind: str, today: date) -> Period:
    if kind == "today":
        return Period(today, today, "today")
    if kind == "year":
        return Period(date(today.year, 1, 1), today, f"this year ({today.year})")
    return Period(today.replace(day=1), today, "this month")


@dataclass
class Entity:
    kind: str
    pk: object
    name: str
    obj: object = None


@dataclass
class Topic:
    name: str
    keywords: dict
    handler: object
    default_period: str = "month"
    # A topic needing a name ("client") only wins when one was found.
    needs: str = ""


@dataclass
class Question:
    text: str
    tokens: list
    period: Period
    period_given: bool
    entities: dict = field(default_factory=dict)
    compare: bool = False
    wants_top: bool = False
    today: date = None

    def has(self, *names) -> bool:
        return any(name in self.tokens for name in names)


def money(value) -> str:
    return f"UGX {Decimal(value or 0):,.0f}"


def answer(text, facts=None, link=None, understood="", suggestions=None, clarify=None) -> dict:
    return {
        "text": text, "facts": [[str(k), str(v)] for k, v in (facts or [])], "link": link,
        "understood": understood, "suggestions": suggestions or [], "clarify": clarify or [],
    }


class Assistant:
    """One per app: its topics, how to find names in a question, and its examples."""

    def __init__(self, topics, find_entities, examples, greeting, restore_entities=None):
        self.topics = topics
        self.find_entities = find_entities
        self.restore_entities = restore_entities
        self.examples = examples
        self.greeting = greeting
        self.vocabulary = sorted({w for topic in topics for w in topic.keywords} | set(SYNONYMS) | set(SYNONYMS.values()) | set(MONTHS)
                                 | PERIOD_WORDS | STOP_WORDS)

    def _tokens(self, text: str) -> list:
        tokens = []
        for word in words(text):
            if word not in self.vocabulary and len(word) >= 4 and not word.isdigit():
                close = difflib.get_close_matches(word, self.vocabulary, n=1, cutoff=0.82)
                if close:
                    word = close[0]
            tokens.append(SYNONYMS.get(word, word))
        return tokens

    def _is_follow_up(self, text, tokens, question) -> bool:
        """Only short questions made of periods and names carry on the last
        topic. A real question we don't understand must not be answered as if
        it were about the previous one."""
        if re.match(r"\s*(and|what about|how about|same|also|then|why)\b", normalise(text)):
            return True
        name_words = {w for entity in question.entities.values() for w in words(entity.name)}
        leftover = [
            t for t in tokens
            if t not in STOP_WORDS and t not in PERIOD_WORDS and t not in MONTHS and not t.isdigit()
            and t not in name_words and not re.fullmatch(r"q[1-4]", t)
        ]
        return not leftover

    def _score(self, topic: Topic, question: Question) -> float:
        score = sum(weight for word, weight in topic.keywords.items() if word in question.tokens)
        if topic.needs and topic.needs not in question.entities:
            return 0
        return score

    def ask(self, text: str, context: dict, today: date, **scope) -> tuple:
        """Returns (answer, new context, topic name or '')."""
        text = (text or "").strip()[:300]
        if not text:
            return self.greeting(today, **scope), context, "hello"
        tokens = self._tokens(text)
        period = parse_period(text, today)
        # "compare with last month" names what to compare against, not the period asked about.
        if period and re.search(r"\b(with|to|vs|versus|against|on) (the )?(last|previous) (week|month|year)\b", normalise(text)):
            period = None
        question = Question(
            text=text, tokens=tokens, period=period, period_given=period is not None, today=today,
            compare=any(t in tokens for t in ("compare", "change", "why")),
            wants_top=any(t in tokens for t in ("top", "who", "which")),
        )
        found = self.find_entities(text, tokens, **scope)
        ambiguous = {kind: matches for kind, matches in found.items() if len(matches) > 1}
        if ambiguous:
            kind, matches = next(iter(ambiguous.items()))
            return answer(
                f"I found more than one {kind} that could match. Which one did you mean?",
                clarify=[m.name for m in matches[:5]],
            ), context, "clarify"
        question.entities = {kind: matches[0] for kind, matches in found.items() if matches}

        scored = sorted(((self._score(topic, question), index, topic) for index, topic in enumerate(self.topics)), key=lambda row: (-row[0], row[1]))
        best_score, _, topic = scored[0]
        if best_score <= 0 and context.get("topic") and self._is_follow_up(text, tokens, question):
            # A follow-up: "and last month?", "what about rent?"
            topic = next((t for t in self.topics if t.name == context["topic"]), None)
            if topic and not (question.period_given or question.entities or question.compare):
                topic = None
            if topic and not question.period_given and context.get("period"):
                question.period = Period(date.fromisoformat(context["period"][0]), date.fromisoformat(context["period"][1]), context["period"][2])
                question.period_given = True
            if topic and not question.entities and context.get("entities") and self.restore_entities:
                question.entities = self.restore_entities(context["entities"], **scope)
        elif best_score <= 0:
            topic = None
        if topic is None:
            return answer(
                "I can't answer that one yet. I've noted it so it can be added. Here are things I can answer:",
                suggestions=self.examples[:6],
            ), context, ""

        if not question.period_given:
            question.period = default_period(topic.default_period, today)
        result = topic.handler(question, **scope)
        new_context = {
            "topic": topic.name,
            "period": [question.period.start.isoformat(), question.period.end.isoformat(), question.period.label],
            "entities": {kind: [e.kind, e.pk] for kind, e in question.entities.items()},
        }
        return result, new_context, topic.name


def match_names(text: str, tokens: list, candidates) -> list:
    """Names mentioned in the question, allowing small typos. `candidates` is
    (kind, pk, name, obj). Returns the best Entity matches, best first."""
    plain = set(words(text)) | set(tokens)
    candidates = list(candidates)
    parts_of = {id(c): [p for p in words(c[2]) if p not in NAME_NOISE and len(p) > 1] or words(c[2]) for c in candidates}
    # A word found in only one name ("Kanyigo") is enough to identify it on its own.
    seen_in = {}
    for parts in parts_of.values():
        for part in set(parts):
            seen_in[part] = seen_in.get(part, 0) + 1
    scored = []
    for candidate in candidates:
        kind, pk, name, obj = candidate
        parts = parts_of[id(candidate)]
        if not parts:
            continue
        hit_parts = [
            part for part in parts
            if part in plain or (len(part) >= 4 and difflib.get_close_matches(part, plain, n=1, cutoff=0.8))
        ]
        hits = len(hit_parts)
        distinctive = any(len(part) >= 4 and seen_in.get(part) == 1 and part not in STOP_WORDS for part in hit_parts)
        if hits and (hits / len(parts) >= 0.5 or distinctive) and any(len(p) >= 3 for p in parts):
            scored.append((hits / len(parts), hits, Entity(kind, pk, name, obj)))
    if not scored:
        return []
    scored.sort(key=lambda row: (-row[0], -row[1]))
    best = (scored[0][0], scored[0][1])
    return [entity for score, hits, entity in scored if (score, hits) == best]

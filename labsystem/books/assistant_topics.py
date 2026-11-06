"""What the Ternah Assistant can answer inside Books. Every figure comes from
the same queries and reports the Books screens use."""
import re
from collections import defaultdict
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.urls import reverse

from labsystem.assistant import Assistant, Entity, Topic, answer, match_names, money

from . import insights, reports
from .document_models import Client, ClientProduct, Expense, Invoice, Payment, Product
from .ledger_models import Account
from .payee_models import Payee, PaymentVoucher
from .planning_models import RecurringInvoice
from .quotation_models import Quotation

ZERO = Decimal("0")
LIVE = ~Q(status__in=[Invoice.STATUS_DRAFT, Invoice.STATUS_VOID])
# "What did Acme pay us" is money coming in, even when Acme is also a payee.
PAID_TO_US_RE = re.compile(r"\b(pa(y|id)\s+us|from|received)\b", re.I)
DOCUMENT_RE = re.compile(r"\b([a-z]{2,6}-\d{4}-\d{2,6})\b", re.I)


def _range(q):
    return q.period.start, q.period.end


def _url(name, *args, **query):
    url = reverse(f"books:{name}", args=args)
    if query:
        url += "?" + "&".join(f"{key}={value}" for key, value in query.items())
    return url


# Measures: (total, {label: amount}) for a period, so any of them can be compared.

def _spent(q):
    qs = Expense.objects.filter(date__range=_range(q), voided_at__isnull=True)
    account = q.entities.get("account")
    if account:
        qs = qs.filter(account_id=account.pk)
    breakdown = defaultdict(lambda: ZERO)
    for row in qs.values("account__name").annotate(a=Sum("amount_ugx"), c=Sum("transaction_charge")):
        breakdown[row["account__name"]] += (row["a"] or ZERO) + (row["c"] or ZERO)
    return sum(breakdown.values(), ZERO), dict(breakdown)


def _revenue(q):
    pnl = reports.profit_and_loss(*_range(q))
    return pnl["total_income"], {row["account"].name: row["amount"] for row in pnl["income_rows"]}


def _collected(q):
    qs = Payment.objects.filter(date__range=_range(q), voided_at__isnull=True)
    client = q.entities.get("client")
    if client:
        qs = qs.filter(client_id=client.pk)
    rows = qs.values("client__name").annotate(t=Sum("amount"))
    breakdown = {row["client__name"]: row["t"] or ZERO for row in rows}
    return sum(breakdown.values(), ZERO), breakdown


def _invoiced(q):
    qs = Invoice.objects.filter(LIVE, issue_date__range=_range(q))
    client = q.entities.get("client")
    if client:
        qs = qs.filter(client_id=client.pk)
    rows = qs.values("client__name").annotate(t=Sum("total"))
    breakdown = {row["client__name"]: row["t"] or ZERO for row in rows}
    return sum(breakdown.values(), ZERO), breakdown


def _paid_out(q):
    qs = PaymentVoucher.objects.filter(expense__date__range=_range(q), expense__voided_at__isnull=True)
    payee = q.entities.get("payee")
    if payee:
        qs = qs.filter(payee_id=payee.pk)
    rows = qs.values("payee__name").annotate(t=Sum("expense__amount_ugx"))
    breakdown = {row["payee__name"]: row["t"] or ZERO for row in rows}
    return sum(breakdown.values(), ZERO), breakdown


MEASURES = {
    "spent": ("Spent", _spent, "expense_list"),
    "revenue": ("Revenue", _revenue, "report_profit_loss"),
    "collected": ("Collected", _collected, "receipt_list"),
    "invoiced": ("Invoiced", _invoiced, "invoice_list"),
    "paid_out": ("Paid out", _paid_out, "voucher_list"),
}
LINK_LABELS = {"spent": "Open expenses", "revenue": "Open profit and loss", "collected": "Open receipts", "invoiced": "Open invoices", "paid_out": "Open payment vouchers"}


def _top(breakdown, limit=5):
    return sorted(breakdown.items(), key=lambda item: -item[1])[:limit]


SCOPE_WORD = {"spent": "on", "collected": "from", "invoiced": "to", "revenue": "from", "paid_out": "to"}


def _scope_label(q, key=""):
    names = [entity.name for entity in q.entities.values() if entity.kind in ("client", "account", "payee")]
    return f" {SCOPE_WORD.get(key, 'for')} {' and '.join(names)}" if names else ""


def _compare(q, key):
    label, measure, link = MEASURES[key]
    now_total, now_parts = measure(q)
    earlier = q.period.previous()
    original = q.period
    q.period = earlier
    then_total, then_parts = measure(q)
    q.period = original
    change = now_total - then_total
    if then_total:
        percent = f" ({abs(change) / then_total * 100:.0f}%)"
    else:
        percent = ""
    direction = "up" if change > 0 else "down" if change < 0 else "unchanged"
    text = f"{label}{_scope_label(q, key)} {q.period.phrase} was {money(now_total)}, "
    text += "the same as" if not change else f"{direction} {money(abs(change))}{percent} on"
    text += f" {earlier.label} ({money(then_total)})."
    deltas = sorted(
        ((name, now_parts.get(name, ZERO) - then_parts.get(name, ZERO)) for name in set(now_parts) | set(then_parts)),
        key=lambda item: -abs(item[1]),
    )
    facts = [(name, ("+" if delta > 0 else "-") + money(abs(delta))) for name, delta in deltas[:4] if delta]
    if facts:
        text += " The biggest changes:"
    return answer(text, facts, {"url": _url(link), "label": LINK_LABELS[key]}, f"{label} · {q.period.label} vs {earlier.label}")


def _measure_answer(q, key, empty):
    if q.compare:
        return _compare(q, key)
    label, measure, link = MEASURES[key]
    total, breakdown = measure(q)
    if not total:
        return answer(empty.format(scope=_scope_label(q, key), period=q.period.phrase) + ".", understood=f"{label} · {q.period.label}")
    facts = _top(breakdown) if len(breakdown) > 1 else []
    query = {"start": q.period.start.isoformat(), "end": q.period.end.isoformat()} if key in ("spent", "paid_out") else {}
    return answer(
        f"{label}{_scope_label(q, key)} {q.period.phrase}: {money(total)}.",
        facts, {"url": _url(link, **query), "label": LINK_LABELS[key]}, f"{label} · {q.period.label}",
        suggestions=[f"Compare {key} with the month before"] if key != "invoiced" else [],
    )


def paid_out(q):
    return _measure_answer(q, "paid_out", "Nothing paid out on vouchers{scope} {period}")


def spent(q):
    return _measure_answer(q, "spent", "Nothing spent{scope} {period}")


def revenue(q):
    return _measure_answer(q, "revenue", "No revenue recorded{scope} {period}")


def collected(q):
    return _measure_answer(q, "collected", "Nothing collected{scope} {period}")


def invoiced(q):
    return _measure_answer(q, "invoiced", "Nothing invoiced{scope} {period}")


def cash(q):
    if "client" in q.entities:
        return owed(q)
    position = insights.cash_position(q.today)
    facts = [(row["name"], money(row["balance"])) for row in position["accounts"]]
    text = f"You have {money(position['total'])} across your payment accounts."
    if position["runway_months"] is not None:
        text += f" That covers about {position['runway_months']} months of spending."
    return answer(text, facts, {"url": _url("dashboard"), "label": "Open dashboard"}, "Cash · now")


def owed(q):
    client = q.entities.get("client")
    if client:
        row = client.obj
        open_invoices = [i for i in row.invoices.filter(status=Invoice.STATUS_OPEN) if i.balance > 0]
        facts = [(i.number + (" (overdue)" if i.is_overdue else ""), money(i.balance)) for i in open_invoices[:6]]
        text = f"{row.name} owes {money(row.balance)}." if row.balance > 0 else f"{row.name} owes nothing. They're fully paid up."
        return answer(text, facts, {"url": _url("client_detail", row.pk), "label": f"Open {row.name}"}, f"Owed · {row.name}")
    ageing = reports.aged_receivables(q.today)
    if q.wants_top:
        facts = [(row["client"].name, money(row["total"])) for row in ageing["client_rows"][:5]]
        top = ageing["client_rows"][0]["client"].name if ageing["client_rows"] else None
        text = f"{top} owes the most. Clients with the biggest balances:" if top else "Nobody owes you anything."
        return answer(text, facts, {"url": _url("report_ageing"), "label": "Open ageing report"}, "Owed · by client")
    facts = [(bucket, money(amount)) for bucket, amount in ageing["grand_totals"].items() if amount]
    return answer(
        f"Clients owe you {money(ageing['grand_total'])} in total. By how late:" if ageing["grand_total"] else "Nobody owes you anything.",
        facts, {"url": _url("report_ageing"), "label": "Open ageing report"}, "Owed · now",
        suggestions=["Who owes us the most?", "Which invoices are overdue?"],
    )


def overdue(q):
    rows = [i for i in Invoice.objects.filter(status=Invoice.STATUS_OPEN).select_related("client").order_by("due_date") if i.is_overdue]
    client = q.entities.get("client")
    if client:
        rows = [i for i in rows if i.client_id == client.pk]
    if not rows:
        return answer("Nothing is overdue.", understood="Overdue · now")
    total = sum((i.balance for i in rows), ZERO)
    facts = [(f"{i.number} · {i.client.name} · {(q.today - i.due_date).days} days late", money(i.balance)) for i in rows[:6]]
    return answer(f"{len(rows)} invoice{'s' if len(rows) != 1 else ''} overdue, {money(total)} in total.", facts,
                  {"url": _url("dashboard"), "label": "See them on the dashboard"}, "Overdue · now")


def profit(q):
    pnl = reports.profit_and_loss(*_range(q))
    net = pnl["net_profit"]
    word = "profit" if net >= 0 else "loss"
    facts = [("Revenue", money(pnl["total_income"])), ("Direct costs", money(pnl["total_direct_cost"])), ("Overheads", money(pnl["total_overhead"]))]
    return answer(f"Net {word} {q.period.phrase}: {money(abs(net))}.", facts,
                  {"url": _url("report_profit_loss"), "label": "Open profit and loss"}, f"Profit · {q.period.label}")


def products(q):
    product, client = q.entities.get("product"), q.entities.get("client")
    if client:
        rows = ClientProduct.objects.filter(client_id=client.pk, active=True).select_related("product")
        facts = [(r.product.name, money(r.price)) for r in rows]
        text = f"{client.name} uses {len(facts)} product{'s' if len(facts) != 1 else ''}." if facts else f"{client.name} has no products recorded."
        return answer(text, facts, {"url": _url("client_detail", client.pk), "label": f"Open {client.name}"}, f"Products · {client.name}")
    if product:
        rows = ClientProduct.objects.filter(product_id=product.pk, active=True).select_related("client").order_by("client__name")
        facts = [(r.client.name, money(r.price)) for r in rows]
        text = f"{len(facts)} client{'s' if len(facts) != 1 else ''} use {product.name}." if facts else f"No clients use {product.name} yet."
        return answer(text, facts, {"url": _url("product_list"), "label": "Open products"}, f"Clients using {product.name}")
    facts = [(p.name, f"{p.n} client{'s' if p.n != 1 else ''}") for p in Product.objects.filter(active=True).annotate(
        n=Count("client_products", filter=Q(client_products__active=True))).order_by("name")]
    if not facts:
        return answer("No products are set up yet. Add them under Products.", link={"url": _url("product_list"), "label": "Open products"}, understood="Products")
    return answer("Your products and how many clients use each:", facts, {"url": _url("product_list"), "label": "Open products"}, "Products")


def top_clients(q):
    total, breakdown = _invoiced(q)
    if not total:
        return answer(f"Nothing invoiced {q.period.phrase}.", understood=f"Top clients · {q.period.label}")
    facts = [(name, money(amount)) for name, amount in _top(breakdown)]
    return answer(f"Your biggest clients by amount invoiced {q.period.phrase}:", facts,
                  {"url": _url("invoice_list"), "label": "Open invoices"}, f"Top clients · {q.period.label}")


def quotations(q):
    pipeline = insights.quotation_pipeline(q.today)
    facts = [("Waiting on clients", f"{pipeline['waiting_count']} · {money(pipeline['waiting_value'])}")]
    if pipeline["win_rate"] is not None:
        facts.append(("Won in the last 90 days", f"{pipeline['win_rate']}% ({pipeline['won']} of {pipeline['decided']})"))
    return answer(f"{pipeline['waiting_count']} quotation{'s' if pipeline['waiting_count'] != 1 else ''} waiting on clients, worth {money(pipeline['waiting_value'])}.",
                  facts, {"url": _url("quotation_list", status="sent"), "label": "Open quotations"}, "Quotations · now")


def monthly(q):
    drafts = Invoice.objects.filter(status=Invoice.STATUS_DRAFT, reference__startswith="Recurring:").count()
    due = [plan for plan in RecurringInvoice.objects.filter(active=True).select_related("client") if plan.is_due]
    upcoming = RecurringInvoice.objects.filter(active=True).select_related("client").order_by("next_run_date")[:5]
    facts = [(f"{plan.client.name} · {plan.next_run_date:%d %b}", money(plan.monthly_total)) for plan in upcoming]
    text = f"{drafts} monthly draft{'s' if drafts != 1 else ''} waiting for you to check, and {len(due)} plan{'s' if len(due) != 1 else ''} due now."
    return answer(text + " Next on the list:" if facts else text, facts, {"url": _url("recurring_list"), "label": "Open recurring"}, "Monthly invoices · now")


def document(q):
    entity = q.entities["document"]
    row = entity.obj
    if isinstance(row, PaymentVoucher):
        facts = [("Paid to", row.payee.name), ("Date", f"{row.date:%d %b %Y}"), ("Amount", money(row.amount)), ("For", row.expense.description)]
        state = "voided" if row.is_void else "valid"
        return answer(f"Payment voucher {row.number} is {state}.", facts, {"url": _url("voucher_detail", row.pk), "label": f"Open {row.number}"}, f"Voucher {row.number}")
    if isinstance(row, Invoice):
        state = "void" if row.status == Invoice.STATUS_VOID else "paid" if row.is_settled else "overdue" if row.is_overdue else "open"
        facts = [("Client", row.client.name), ("Issued", f"{row.issue_date:%d %b %Y}"), ("Total", money(row.total)), ("Balance", money(row.balance))]
        return answer(f"{row.number} is {state}.", facts, {"url": _url("invoice_detail", row.pk), "label": f"Open {row.number}"}, f"Invoice {row.number}")
    facts = [("Client", row.client.name), ("Date", f"{row.date:%d %b %Y}"), ("Amount", money(row.amount)), ("Method", row.get_method_display())]
    state = "voided" if row.voided_at else "valid"
    return answer(f"Receipt {row.receipt_number} is {state}.", facts, {"url": _url("receipt_list") + f"?q={row.receipt_number}", "label": "Open receipts"}, f"Receipt {row.receipt_number}")


def hello(q):
    return greeting(q.today)


TOPICS = [
    Topic("document", {"status": 1}, document, needs="document"),
    Topic("owed", {"owe": 3, "balance": 1}, owed),
    Topic("overdue", {"overdue": 3}, overdue),
    Topic("spent", {"spent": 3}, spent),
    Topic("paid_out", {"voucher": 3, "payee": 3, "paid": 1, "out": 1.5}, paid_out),
    Topic("revenue", {"revenue": 3, "made": 1}, revenue, default_period="year"),
    Topic("collected", {"collected": 3, "payment": 2, "paid": 1}, collected),
    Topic("profit", {"profit": 3, "loss": 2, "margin": 2, "net": 1}, profit, default_period="year"),
    Topic("products", {"product": 2, "using": 2}, products),
    Topic("top_clients", {"clients": 1.5, "top": 1}, top_clients, default_period="year"),
    Topic("invoiced", {"invoiced": 3, "invoice": 1}, invoiced),
    Topic("quotations", {"quotation": 3, "pipeline": 2}, quotations),
    Topic("monthly", {"draft": 3, "drafts": 3, "recurring": 3, "monthly": 2, "plan": 1, "plans": 1}, monthly),
    Topic("cash", {"cash": 3, "balance": 1}, cash),
    Topic("hello", {"hello": 2, "help": 3, "thanks": 2, "thank": 2}, hello),
]
ENTITY_BONUS = {"products": {"product": 2}, "owed": {"client": 0.5}, "spent": {"account": 1.5}, "paid_out": {"payee": 4}}


def find_entities(text, tokens):
    found = {}
    match = DOCUMENT_RE.search(text)
    if match:
        number = match.group(1).upper()
        row = Invoice.objects.filter(number__iexact=number).select_related("client").first() or \
            Payment.objects.filter(receipt_number__iexact=number).select_related("client").first() or             PaymentVoucher.objects.filter(number__iexact=number).select_related("payee", "expense").first()
        if row:
            found["document"] = [Entity("document", row.pk, number, row)]
    clients = [("client", c.pk, c.name, c) for c in Client.objects.filter(active=True)]
    clients += [("client", c.pk, c.trading_name, c) for c in Client.objects.filter(active=True).exclude(trading_name="")]
    products = [("product", p.pk, p.name, p) for p in Product.objects.filter(active=True)]
    accounts = [("account", a.pk, a.name, a) for a in Account.objects.filter(active=True, type=Account.TYPE_EXPENSE)]
    payees = [("payee", p.pk, p.name, p) for p in Payee.objects.filter(active=True)]
    for kind, candidates in (("client", clients), ("product", products), ("account", accounts), ("payee", payees)):
        matches = match_names(text, tokens, candidates)
        unique = list({m.pk: m for m in matches}.values())
        if unique:
            found[kind] = unique
    # "SACCO" in a client's name and a product's name: a product question means the product.
    if "client" in found and "product" in found and "using" not in tokens and "product" not in tokens:
        found.pop("product")
    return found


def restore_entities(saved):
    lookup = {"client": Client, "product": Product, "account": Account, "payee": Payee}
    restored = {}
    for kind, (entity_kind, pk) in saved.items():
        model = lookup.get(kind)
        row = model.objects.filter(pk=pk).first() if model else None
        if row:
            restored[kind] = Entity(entity_kind, pk, row.name, row)
    return restored


def greeting(today):
    overdue_rows = [i for i in Invoice.objects.filter(status=Invoice.STATUS_OPEN) if i.is_overdue]
    drafts = Invoice.objects.filter(status=Invoice.STATUS_DRAFT, reference__startswith="Recurring:").count()
    facts = []
    if overdue_rows:
        facts.append(("Overdue invoices", f"{len(overdue_rows)} · {money(sum((i.balance for i in overdue_rows), ZERO))}"))
    if drafts:
        facts.append(("Monthly drafts to check", str(drafts)))
    text = "Hello. Ask me about your money: cash, spending, revenue, who owes you, products." if not facts else "Hello. A couple of things need attention:"
    return answer(text, facts, suggestions=EXAMPLES[:4])


EXAMPLES = [
    "How much money do we have?",
    "What did we spend this month?",
    "Who owes us the most?",
    "Which invoices are overdue?",
    "Revenue this year",
    "Compare spending with last month",
    "What did we collect last month?",
    "Which clients use each product?",
    "Profit this year",
    "Quotations waiting",
    "What did we pay out this month?",
]


class BooksAssistant(Assistant):
    def _score(self, topic, question):
        score = super()._score(topic, question)
        if topic.name == "paid_out" and PAID_TO_US_RE.search(question.text):
            return 0
        if topic.needs and topic.needs in question.entities:
            score += 5
        for kind, bonus in ENTITY_BONUS.get(topic.name, {}).items():
            if kind in question.entities and score:
                score += bonus
        return score


assistant = BooksAssistant(TOPICS, find_entities, EXAMPLES, greeting, restore_entities)

"""
Diagnostic script to check if cash payments are properly synced with financial statements.
Run this to identify any sync issues.
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'labsystem.settings')
django.setup()

from reception.models import Payment, Visit
from admin_dashboard.models import CashDrawer, CashTransaction, HospitalAccount
from accounts.models import Hospital
from django.db.models import Sum, F

print("=" * 70)
print("CASH PAYMENT SYNC DIAGNOSTIC")
print("=" * 70)

# 1. Check all hospitals
print("\n1. HOSPITALS IN SYSTEM")
hospitals = Hospital.objects.all()
print(f"   Total hospitals: {hospitals.count()}")
for h in hospitals:
    print(f"   - {h.name} (subdomain: {h.subdomain})")

# 2. Check all payments
print("\n2. ALL PAYMENTS")
payments = Payment.objects.all()
print(f"   Total payments: {payments.count()}")

cash_payments = payments.filter(mode='cash')
card_payments = payments.filter(mode='card')
mobile_payments = payments.filter(mode='mobile_money')

print(f"   Cash payments: {cash_payments.count()}")
print(f"   Card payments: {card_payments.count()}")
print(f"   Mobile money payments: {mobile_payments.count()}")

# 3. Check cash payments detail
print("\n3. CASH PAYMENTS DETAIL")
for p in cash_payments:
    visit = p.visit
    hospital = visit.hospital if visit else None
    has_drawer = CashTransaction.objects.filter(payment=p).exists()
    print(f"   Payment #{p.id}:")
    print(f"     - Amount: {p.amount}, Paid: {p.amount_paid}")
    print(f"     - Status: {p.status}")
    print(f"     - Visit: {visit.id if visit else 'None'}, Hospital: {hospital.name if hospital else 'None'}")
    print(f"     - Has CashTransaction: {has_drawer}")
    print(f"     - Paid at: {p.paid_at}")

# 4. Check payments without hospital linkage
print("\n4. PAYMENTS WITHOUT HOSPITAL LINKAGE")
orphaned = Payment.objects.filter(visit__hospital__isnull=True)
print(f"   Orphaned payments: {orphaned.count()}")
if orphaned.exists():
    print("   WARNING: These payments are not linked to any hospital!")
    for p in orphaned:
        print(f"   - Payment #{p.id}: Amount={p.amount}, Paid={p.amount_paid}")

# 5. Check cash drawers
print("\n5. CASH DRAWERS")
drawers = CashDrawer.objects.all()
print(f"   Total cash drawers: {drawers.count()}")
for d in drawers:
    cash_in = d.transactions.filter(transaction_type='cash_in').aggregate(total=Sum('amount'))['total'] or 0
    cash_out = d.transactions.filter(transaction_type='cash_out').aggregate(total=Sum('amount'))['total'] or 0
    expected = d.opening_balance + cash_in - cash_out
    print(f"   Drawer #{d.id}: Hospital={d.hospital}, Open={d.opening_balance}, CashIn={cash_in}, Expected={expected}")

# 6. Check hospital accounts
print("\n6. HOSPITAL ACCOUNTS")
accounts = HospitalAccount.objects.all()
print(f"   Total accounts: {accounts.count()}")
for a in accounts:
    print(f"   Hospital: {a.hospital}, Balance: {a.balance}")

# 7. Check if cash payments are counted in income
print("\n7. INCOME CALCULATION CHECK")
for hospital in hospitals:
    # This is how financial_report calculates income
    hospital_payments = Payment.objects.filter(visit__hospital=hospital)
    paid_income = hospital_payments.aggregate(total=Sum("amount_paid"))["total"] or 0
    cash_income = hospital_payments.filter(mode='cash').aggregate(total=Sum("amount_paid"))["total"] or 0
    card_income = hospital_payments.filter(mode='card').aggregate(total=Sum("amount_paid"))["total"] or 0
    mobile_income = hospital_payments.filter(mode='mobile_money').aggregate(total=Sum("amount_paid"))["total"] or 0
    
    print(f"\n   Hospital: {hospital.name}")
    print(f"   - Total income (all payments): {paid_income}")
    print(f"   - Cash income: {cash_income}")
    print(f"   - Card income: {card_income}")
    print(f"   - Mobile income: {mobile_income}")
    print(f"   - Total payments: {hospital_payments.count()}")

# 8. Check for payments without paid_at
print("\n8. PAYMENTS WITHOUT PAID_AT TIMESTAMP")
no_date = Payment.objects.filter(paid_at__isnull=True, amount_paid__gt=0)
print(f"   Payments with amount_paid but no paid_at: {no_date.count()}")
if no_date.exists():
    print("   WARNING: These payments have money received but no timestamp!")
    for p in no_date[:5]:
        print(f"   - Payment #{p.id}: Amount={p.amount}, Paid={p.amount_paid}, Mode={p.mode}")

print("\n" + "=" * 70)
print("DIAGNOSTIC COMPLETE")
print("=" * 70)
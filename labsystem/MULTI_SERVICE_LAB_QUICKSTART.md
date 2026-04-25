# Multi-Service Lab Request System - Quick Start Guide

## 🚀 What's New

Doctors can now **select multiple lab tests** from a checkbox list when creating a consultation, and the system automatically:
- Creates billing entries for each selected service
- Pre-populates the lab report form with all requested test parameters
- Groups results by profile/section for clean reporting

## 📋 Quick Workflow

### For Doctors
1. Open Consultation Form
2. Scroll to "Lab Services" section
3. Check multiple lab tests (e.g., CBC, Urinalysis, X-Ray)
4. Save consultation
5. ✅ Services added to bill, queue entry created

### For Lab Technicians
1. View Lab Queue
2. See which services were requested
3. Click "Start Report"
4. 🎉 Form pre-populated with all test parameters
5. Enter results for all tests
6. Save report - all services marked complete

### For Admin/Setup
1. Go to Django Admin → Reception → Services
2. For lab services, set the `test_profile`:
   - CBC → TestProfile (CBC)
   - Urinalysis → TestProfile (Urinalysis)
   - etc.
3. Save
4. ✅ Done - the system handles the rest

## 🔧 Installation

```bash
# Apply database migrations
python manage.py migrate

# Verify import (if you modified views)
python manage.py shell
>>> import lab.views
>>> import doctor.views
```

## 📊 What Changed

### Database
- `Service.test_profile` - Links service to test template
- `VisitService.performed` - Tracks if service completed
- `VisitService.performed_at` - When service was completed

### Forms
- `ConsultationForm.lab_services` - Multi-select checkbox field

### Backend
- Doctor form processes multiple services
- Lab form auto-populates requested profiles
- VisitServices marked performed on report save

### Templates
- Doctor: Lab Services checkbox list
- Lab: "Doctor Requested Tests" info section + auto-load
- Queue: Shows all services per entry

## 🧪 Testing

### Quick Test (5 minutes)
1. Doctor: Open consultation, select CBC + Urinalysis
2. Save
3. Verify: 2 VisitService entries created
4. Lab: View queue, click report
5. Verify: Form shows both CBC and Urinalysis rows

### Full Scenarios
See: `MULTI_SERVICE_TEST_GUIDE.md` for 5+ comprehensive test scenarios

## 📖 Documentation

- **MULTI_SERVICE_LAB_IMPLEMENTATION_SUMMARY.md** - Complete technical overview
- **MULTI_SERVICE_TEST_GUIDE.md** - Step-by-step test scenarios (5+ scenarios)

## ❓ FAQ

**Q: Can a doctor select the same service twice?**  
A: No, deduplication check prevents duplicate VisitService entries.

**Q: What if a service has no test profile?**  
A: Lab tech can manually add test rows - no template required.

**Q: How is billing handled?**  
A: Each service price added to visit.total_amount (cumulative).

**Q: Can I see which services were requested?**  
A: Yes - queue shows all services, report form displays them prominently.

**Q: Is it backward compatible?**  
A: Yes, single service requests still work as before.

## 🔍 Verification Commands

```bash
# Check if services have profiles assigned
python manage.py shell
>>> from reception.models import Service
>>> Service.objects.exclude(test_profile__isnull=True).count()

# Check VisitServices for a visit
>>> from reception.models import VisitService
>>> VisitService.objects.filter(visit_id=1)
# Should show multiple if services requested

# Check if profiles combine properly
>>> from lab.models import TestProfile
>>> TestProfile.objects.filter(is_active=True).count()
# Each should have parameters
```

## ⚠️ Common Issues

| Issue | Solution |
|-------|----------|
| Dropdown empty | Verify services exist, category=lab, is_active=True |
| Form not pre-populating | Check browser console, verify requested_profiles in source |
| Services not marked done | Verify report saved, check VisitService.performed in DB |
| Multiple profiles not grouping | Ensure section_name set on profile parameters |

## 🚦 Success Indicators

- ✅ Checkboxes appear in consultation form
- ✅ Multiple services selectable
- ✅ Lab queue shows all services
- ✅ Form rows pre-populate in lab report
- ✅ Results group by section in report_detail
- ✅ VisitServices marked performed=True after save

## 📞 Support

Check logs:
```bash
tail -f logs/django.log | grep -i "lab\|service\|consultation"
```

Debug in shell:
```bash
python manage.py shell
>>> from reception.models import Visit
>>> v = Visit.objects.get(pk=1)
>>> v.visit_services.all()  # Show all services
>>> v.visit_services.filter(performed=False)  # Show unperformed
```

## 🎯 Next Steps

1. ✅ Run test scenarios from MULTI_SERVICE_TEST_GUIDE.md
2. ✅ Train staff on new workflow
3. ✅ Monitor logs for errors
4. ✅ Gather feedback for improvements

---

**Status:** ✅ Production Ready  
**Tests:** Comprehensive test guide included  
**Documentation:** Complete  
**Date:** April 21, 2026

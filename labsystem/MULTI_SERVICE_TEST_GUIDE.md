# Multi-Service Lab Request System - Test Guide

## Overview
This document outlines the complete end-to-end workflow for the new multi-service lab request system where doctors can select multiple lab tests at once, and the system auto-bills all services while pre-populating the lab form with all requested profiles.

---

## System Components

### 1. Database Models
- **Service.test_profile** - FK to TestProfile (links services like CBC, Urinalysis to templates)
- **VisitService.performed** - Boolean tracking if service completed (default: False)
- **VisitService.performed_at** - DateTime of completion

### 2. Forms
- **ConsultationForm.lab_services** - ModelMultipleChoiceField with CheckboxSelectMultiple widget
  - Filtered by: hospital, category=LAB, is_active=True
  - Allows selecting multiple services simultaneously

### 3. Views
- **doctor/views.consultation()** - Processes multiple selected lab services
- **lab/views.get_requested_lab_services()** - Retrieves VisitServices with profiles
- **lab/views.collect_profile_parameters()** - Combines multiple profiles into sections
- **lab/views.handle_report_form()** - Auto-populates form with requested profiles
- **lab/views.report_create_from_lab_request()** - Marks VisitServices as performed

### 4. Templates
- **consultation_form.html** - Shows lab_services checkbox multi-select
- **report_form.html** - Displays requested profiles section and auto-loads form rows
- **lab_queue.html** - Shows all services for multi-service requests
- **report_detail.html** - Groups results by section/profile
- **report_print.html** - Print-optimized grouped results

---

## Test Scenario 1: Single Profile Request

### 1.1 Doctor Selects Single Service (CBC)
**Step 1:** Doctor opens Consultation form for patient
- Navigate: Doctor Queue → Select patient → Consultation tab

**Step 2:** In "Lab Services" section, check only "Complete Blood Count (CBC)"
- Verify checkbox is available and selectable
- Help text shows: "Select lab services to add to the patient's bill"

**Step 3:** Click "Save Consultation"
- Expected outcome:
  - ✅ VisitService created with service=CBC
  - ✅ visit.total_amount increased by CBC price
  - ✅ Single QueueEntry TYPE_LAB_DOCTOR created with reason: "Doctor requested: Complete Blood Count (CBC)"
  - ✅ Success message: "Consultation saved... Lab services requested: Complete Blood Count (CBC)"

### 1.2 Lab Tech Processes Single Service
**Step 4:** Navigate to Lab Queue or click queue entry
- Verify reason shows: "Doctor requested: Complete Blood Count (CBC)"
- Click "Start Report"

**Step 5:** Lab Report Form loads
- Expected outcome:
  - ✅ "Doctor Requested Tests" section visible showing "Complete Blood Count (CBC)"
  - ✅ CBC parameters pre-populated in form rows
  - ✅ All section names show "SECTION A: COMPLETE BLOOD COUNT (CBC)"
  - ✅ Section dividers appear between sections if any

**Step 6:** Lab tech enters test results for all CBC parameters
- Fill result values for each parameter
- Modify reference ranges/units if needed
- Click "Save Report"

**Step 7:** Verify report completion
- Expected outcome:
  - ✅ VisitService.performed = True
  - ✅ VisitService.performed_at = current timestamp
  - ✅ Results grouped by section in report_detail.html
  - ✅ QueueEntry processed and results sent to doctor

---

## Test Scenario 2: Multiple Different Profiles

### 2.1 Doctor Selects Multiple Services (CBC + Urinalysis)
**Step 1:** Doctor opens Consultation form
- Navigate: Doctor Queue → Select patient → Consultation tab

**Step 2:** In "Lab Services" section, check:
- ☑ Complete Blood Count (CBC)
- ☑ Urinalysis

**Step 3:** Click "Save Consultation"
- Expected outcome:
  - ✅ Two VisitService entries created (one for each service)
  - ✅ visit.total_amount increased by CBC + Urinalysis prices
  - ✅ Single QueueEntry with reason: "Doctor requested: Complete Blood Count (CBC), Urinalysis"
  - ✅ Success message lists both services

### 2.2 Lab Tech Processes Multi-Profile Request
**Step 4:** Navigate to Lab Queue
- Verify "Reason" shows both services:
  - "Doctor requested: Complete Blood Count (CBC), Urinalysis"
- Verify "Services" section shows two service badges:
  - `CBC` `Urinalysis`
- Click "Start Report"

**Step 5:** Lab Report Form loads with combined profiles
- Expected outcome:
  - ✅ "Doctor Requested Tests" section shows both:
    - "Complete Blood Count (CBC)"
    - "Urinalysis"
  - ✅ Form pre-populated with:
    - CBC section with ~20+ CBC parameters
    - Urinalysis section with ~10+ urinalysis parameters
  - ✅ Section dividers properly separate CBC from Urinalysis sections

**Step 6:** Lab tech enters results for combined tests
- Fill CBC parameters (e.g., WBC, RBC, Hemoglobin)
- Fill Urinalysis parameters (e.g., Color, Appearance, Glucose)
- Click "Save Report"

**Step 7:** Verify multi-profile report
- Report Detail shows:
  - ✅ Results grouped into two sections:
    - "SECTION A: COMPLETE BLOOD COUNT (CBC)" - with CBC results
    - "Urinalysis Results" or similar - with urinalysis results
  - ✅ Both VisitService records marked performed=True
  - ✅ Print preview shows both profiles grouped clearly

---

## Test Scenario 3: Multiple Same Profile

### 3.1 Doctor Selects Two CBC Services (Should Deduplicate)
**Step 1:** Doctor opens Consultation for patient

**Step 2:** In "Lab Services" section, select CBC twice
- Note: Form may show CBC multiple times in dropdown, or may prevent duplicates

**Step 3:** Click "Save Consultation"
- Expected outcome:
  - ✅ Only ONE VisitService created for CBC (deduplication check)
  - ✅ visit.total_amount increased by CBC price ONCE
  - ✅ QueueEntry reason: "Doctor requested: Complete Blood Count (CBC)"
  - ✅ Message shows single service only

---

## Test Scenario 4: Mixed Services and Manual Entry

### 4.1 Doctor Selects CBC + Other Service (with and without profile)
**Step 1:** Select:
- ☑ Complete Blood Count (CBC)
- ☑ X-Ray (if service has no test_profile)

**Step 2:** Save Consultation
- Expected outcome:
  - ✅ Two VisitService entries created
  - ✅ QueueEntry shows both services

### 4.2 Lab Tech Processes Mixed Request
**Step 3:** Open Lab Report Form
- Expected outcome:
  - ✅ CBC section pre-populated with parameters
  - ✅ X-Ray shows in "Doctor Requested Tests" but no pre-loaded rows
  - ✅ Lab tech can manually add X-Ray rows (no template)

**Step 4:** Enter results
- Fill CBC parameters from template
- Manually add X-Ray result rows using "Add Test Row"
- Save Report

**Step 5:** Verify report
- ✅ Results show CBC section (from profile) + manual X-Ray entries
- ✅ Both services marked performed=True

---

## Test Scenario 5: No Services Requested

### 5.1 Doctor Saves Consultation Without Selecting Lab Services
**Step 1:** Open Consultation form

**Step 2:** Leave "Lab Services" unchecked, save

**Step 3:** Expected outcome:
- ✅ No VisitService entries created
- ✅ No lab QueueEntry created
- ✅ visit.total_amount unchanged
- ✅ Message: "Consultation saved for [patient]"

---

## Database Verification Tests

### 6.1 Service Model Changes
```sql
-- Verify test_profile FK exists
SELECT id, name, test_profile_id FROM reception_service LIMIT 5;

-- Should show services linked to TestProfile:
-- id | name                       | test_profile_id
-- 1  | Complete Blood Count (CBC) | 1
-- 2  | Urinalysis                 | 2
```

### 6.2 VisitService Tracking
```sql
-- Verify performed fields exist
SELECT id, visit_id, service_id, performed, performed_at FROM reception_visitservice;

-- Before lab processing: performed=0, performed_at=NULL
-- After lab saves report: performed=1, performed_at=<timestamp>
```

### 6.3 Consultation Lab Requests Storage
```sql
-- Verify lab_requests JSONField stores service IDs
SELECT id, lab_requests FROM doctor_consultation WHERE lab_requests IS NOT NULL;

-- Should show: [1, 2] if doctor selected 2 services
```

---

## Admin Configuration

### 7.1 Set Up Lab Services with Profiles
1. Django Admin → Reception → Services
2. Add/Edit services with category="Laboratory"
3. For each service, set `test_profile` to appropriate TestProfile:
   - Complete Blood Count → TestProfile (cbc)
   - Urinalysis → TestProfile (urinalysis)
   - X-Ray → Leave blank (manual entry only)

### 7.2 Verify Hospital Assignment
- All services must have correct `hospital` assigned
- ConsultationForm filters by current hospital

---

## Troubleshooting

### Issue: Lab services dropdown empty
- **Solution:** Verify services exist with category="lab" and is_active=True
- **Solution:** Verify hospital is set correctly on services

### Issue: Form doesn't pre-populate profiles
- **Verify:** requested_profiles data passes to template
- **Check:** Browser console for JavaScript errors
- **Verify:** TestProfile objects have parameters defined

### Issue: VisitService.performed not updating
- **Verify:** handle_report_form() calls update() on VisitService
- **Check:** Report actually saved (verify in database)
- **Verify:** Visit relationship exists (report.visit_id not null)

### Issue: Multiple profile sections not grouping
- **Solution:** Ensure section_name set correctly in TestProfileParameter
- **Solution:** Verify group_results() function in lab/views.py

---

## Performance Considerations

### Query Optimization
- Lab queue uses: `select_related('visit__patient')` + `prefetch_related('visit__visit_services__service')`
- Report form calls: `get_requested_lab_services()` which filters VisitService properly

### Expected Queries
- Consultation save: ~5 queries (consultation, visit, test services check, VisitService create, queue entry)
- Lab form load: ~3 queries (report, visit services, profiles)
- Report save: ~8 queries (report, formset, VisitService update, queue mark complete)

---

## Migration Rollback (if needed)
```bash
python manage.py migrate reception 0004
python manage.py migrate doctor 0001
```

This removes:
- Service.test_profile FK
- VisitService.performed field
- VisitService.performed_at field
- Reverts ConsultationForm.lab_services field

---

## Success Criteria Checklist

✓ Doctor can select multiple lab services in consultation form  
✓ Multiple services create separate VisitService billing entries  
✓ Single QueueEntry created with all service names  
✓ Lab form pre-populates with all requested profile parameters  
✓ Results grouped by section/profile in report detail  
✓ VisitServices marked performed=True on report save  
✓ Multiple profile prints show all results grouped by profile  
✓ Deduplication prevents duplicate VisitService entries  
✓ Manual entry fallback works for services without profiles  
✓ Pricing cumulative (all services added to visit total)  

---

## Next Steps (Post-Testing)

1. **User training** - Show doctors how to select multiple services
2. **Lab tech workflow** - Train on processing multi-profile requests
3. **Report customization** - Add hospital branding to multi-profile reports
4. **Analytics** - Track which service combinations are most common
5. **Optimization** - Consider bundled pricing for common test combinations


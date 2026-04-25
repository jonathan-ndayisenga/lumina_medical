# Lumina Lab Management System - Module Documentation

## Overview

The **lab module** is a Django-based laboratory report management system built for Lumina Medical Services. It enables lab staff to create, manage, track, and print laboratory test reports with flexible test templates, automatic reference range learning, and comprehensive patient record management.

**Built with:** Django 6.0.3, Python, PostgreSQL/SQLite, WhiteNoise for static files

---

## Architecture Overview

```
labsystem/ (Django Project)
├── lab/ (Main App)
│   ├── models.py          # Data models for reports, tests, profiles
│   ├── views.py           # Business logic & HTTP handlers
│   ├── forms.py           # Form validation & input handling
│   ├── urls.py            # URL routing
│   ├── admin.py           # Django admin interface
│   └── migrations/        # Database schema history
└── templates/lab/         # HTML rendering templates
```

---

## Core Data Models

### 1. **LabReport** (Main Record)
The central entity representing a lab report for a patient.

```python
class LabReport(models.Model):
    profile              # ForeignKey to TestProfile template (optional)
    patient_name         # Patient's full name
    patient_age          # Age in format "22YRS" or "6MTH" 
    patient_sex          # M, F, or O
    referred_by          # Referring physician/clinic
    sample_date          # Date specimen was collected
    specimen_type        # BLOOD, URINE, STOOL, etc.
    attendant            # ForeignKey to User (lab technician)
    attendant_name       # Lab tech's name (denormalized)
    comments             # Additional clinical notes
    created_at           # Timestamp when report created
    printed              # Boolean flag for print status
    printed_at           # Timestamp when first printed
```

**Usage:** Every lab report entry creates one LabReport record. Multiple TestResult records attach to it.

---

### 2. **TestProfile** (Reusable Templates)
Pre-configured test templates (e.g., "Complete Blood Count", "Urinalysis") that standardize data entry.

```python
class TestProfile(models.Model):
    name                      # "Complete Blood Count"
    code                      # "CBC" (slug identifier)
    default_specimen_type     # "BLOOD"
    description               # What this profile is for
    is_active                 # Boolean activation flag
    display_order             # Sort order in UI dropdown
    
    # Related: parameters (TestProfileParameter objects)
    # Related: reports (LabReport objects using this profile)
```

**Usage:** Staff selects a profile when creating a report → auto-populates test fields & reference ranges.

---

### 3. **TestProfileParameter** (Profile Fields)
Individual test parameters that belong to a TestProfile.

```python
class TestProfileParameter(models.Model):
    profile                    # ForeignKey to TestProfile
    test                       # ForeignKey to TestCatalog (actual test)
    section_name              # "Hematology", "Chemistry", etc.
    display_order             # Sort within section
    input_type                # "text", "numeric", or "choice"
    choice_options            # For choice inputs (newline-separated)
    default_reference_range   # e.g., "4.5-11.0"
    default_unit              # e.g., "K/µL"
    default_comment           # Pre-filled comment
    is_required               # Whether field is mandatory
    allow_range_learning      # Auto-learn new ranges from entries
```

**Usage:** Defines the structure of a profile. Example: CBC profile has 15+ parameters (RBC, WBC, Hemoglobin, etc.).

---

### 4. **TestCatalog** (Test Library)
Global catalog of all tests that can be performed. Built organically as users enter free-text test names.

```python
class TestCatalog(models.Model):
    name              # "Red Blood Cell Count"
    unit              # "K/µL"
    display_order     # Sort order for autocomplete
```

**Usage:** Normalized reference for all unique test names across all reports. Allows test name deduplication and consistent referencing.

---

### 5. **TestResult** (Actual Results)
The actual lab test result value for a specific report.

```python
class TestResult(models.Model):
    lab_report           # ForeignKey to LabReport
    test                 # ForeignKey to TestCatalog
    section_name         # Display section ("Hematology", etc.)
    display_order        # Sort order in UI
    result_value         # The measured value (e.g., "7.2")
    reference_range      # e.g., "4.5-11.0"
    unit                 # e.g., "K/µL"
    comment              # Additional notes
```

**Usage:** Stores the actual test result. Multiple results per report. Linked to the test definition for consistency.

---

### 6. **ReferenceRangeDefault** (Learning & Defaults)
Stores learned reference ranges by patient age category for intelligent auto-population.

```python
class ReferenceRangeDefault(models.Model):
    test                # ForeignKey to TestCatalog
    age_category        # "neonate", "infant", "child_1_5", "child_6_11", "child_12_17", "adult"
    reference_range     # e.g., "4.5-11.0"
    unit                # e.g., "K/µL"
    
    unique_together = ('test', 'age_category')  # One range per test per age
```

**Usage:** 
- When editing a report, system looks up default ranges for the patient's age
- When saving a new range, it's learned and stored for future use
- Intelligent auto-population reduces data entry errors

---

## Core Web Views (Controllers)

### URL Routes
```
GET  /                          → report_list()          # Dashboard
GET  /new/                      → report_create()        # New report form
GET  /templates/                → template_library()     # View all templates
GET  /<id>/                     → report_detail()        # View report
GET  /<id>/edit/                → report_edit()          # Edit report
GET  /<id>/print/               → report_print()         # Print-friendly view
POST /<id>/delete/              → report_delete()        # Delete confirmation
GET  /api/default-range/        → default_range()        # AJAX for auto-populate
```

### Key View Implementations

#### **1. report_list() - Dashboard**
```python
@login_required
@staff_required
def report_list(request):
```
- Lists all lab reports with pagination (10 per page)
- Filters by search (patient name, referred by, specimen type, or report ID)
- Filter by status (printed/draft)
- Shows stats: total, printed, draft counts

**Response:** Paginated report list with filtering UI

---

#### **2. report_create() & report_edit() - Form Handling**
```python
@login_required
@staff_required
@transaction.atomic
def report_create(request):
    return handle_report_form(request)

@login_required
@staff_required
@transaction.atomic
def report_edit(request, pk):
    report = get_object_or_404(LabReport, pk=pk)
    return handle_report_form(request, report=report)
```

**Shared handler:** `handle_report_form(request, report=None)`
- POST: Validates form & formset → saves report & test results → auto-learns ranges
- GET: Displays form with pre-filled data and profile templates
- Uses Django formset for inline test results table
- Auto-populates attendant info from current user
- Handles profile-based specimen type defaults

**Response:** Rendered form or redirect to report_detail on success

---

#### **3. report_detail() - View Report**
```python
@login_required
@staff_required
def report_detail(request, pk):
```
- Displays complete report with grouped test results
- Supports `?mark_printed=1` to mark as printed
- Groups results by section name for better readability

**Response:** Full report display with all metadata and results

---

#### **4. report_print() - Print View**
```python
@login_required
@staff_required
def report_print(request, pk):
```
- Print-optimized HTML layout for physical printout
- Auto-marks report as printed on first access
- Grouped results by section
- Suitable for PDF conversion

**Response:** Print-friendly HTML

---

#### **5. report_delete() - Deletion**
```python
@login_required
@staff_required
def report_delete(request, pk):
```
- GET: Shows confirmation page
- POST: Deletes report and related results (cascading delete)

**Response:** Confirmation page or redirect to report_list

---

#### **6. default_range() - AJAX Endpoint**
```python
@login_required
@staff_required
def default_range(request):
```
- AJAX endpoint: `GET ?test=test_name&age=age_string`
- Returns: `{"reference_range": "...", "unit": "..."}`
- Used by frontend for auto-population when user enters test name

**Response:** JSON with reference range and unit for autocomplete

---

#### **7. template_library() - Browse Templates**
```python
@login_required
@staff_required
def template_library(request):
```
- Lists all active TestProfile templates
- Shows parameters for each profile
- Reference for users creating reports

**Response:** Template showcase with all parameters

---

## Helper Functions (Business Logic)

### **get_age_category(age_str: str) → str**
Converts free-text age formats into normalized age categories.

```python
Input examples:
  "22YRS" → "adult"
  "5 years old" → "child_1_5"
  "3 months" → "infant"
  "15" → "child_12_17"
```

Used for: Reference range lookups by age

---

### **get_or_create_test_definition(test_name: str, unit: str) → TestCatalog**
Normalizes test names and creates or retrieves TestCatalog entries.

```python
Input:
  "  Red  Blood  Cell  ", unit="K/µL"
Output:
  TestCatalog(name="Red Blood Cell", unit="K/µL")
```

Used for: De-duplicating test names, building test vocabulary

---

### **save_results_from_formset(report: LabReport, formset)**
Persists test results from form submission, handles deletion, and auto-learns ranges.

**Logic:**
1. For each result in the formset:
   - Skip blank rows (untouched extra rows in form)
   - Handle deletion flag (`DELETE=True`)
   - Normalize test name and create/get TestCatalog
   - Save TestResult with proper references
2. If range is new for age category:
   - Create ReferenceRangeDefault entry (learning system)

Used for: Processing form submissions (both create and edit)

---

### **serialize_profile_payload(profiles)**
Converts TestProfile objects into JSON for frontend use.

Output structure:
```json
{
  "1": {
    "name": "Complete Blood Count",
    "code": "CBC",
    "parameters": [
      {
        "test_name": "White Blood Cell Count",
        "unit": "K/µL",
        "reference_range": "4.5-11.0",
        ...
      }
    ]
  }
}
```

Used for: Frontend template selection and auto-population

---

### **group_results(report, results)**
Groups test results by section name for display.

Output:
```python
[
  {"name": "Hematology", "results": [result1, result2, ...]},
  {"name": "Chemistry", "results": [result3, result4, ...]}
]
```

Used for: Report detail and print views for organized display

---

## Forms

### **LabReportForm** (Main Report Data)
```python
class LabReportForm(forms.ModelForm):
    profile              # Dropdown of active TestProfiles
    patient_name         # Text input
    age_value            # Number input (split from patient_age)
    age_unit             # Dropdown: "YRS" or "MTH"
    patient_sex          # Dropdown: M/F/O
    referred_by          # Text input
    sample_date          # Date picker
    specimen_type        # Text input
    attendant_name       # Text input (pre-filled from user)
    comments             # Textarea
```

**Smart Features:**
- Splits stored age format "22YRS" into separate number/unit fields
- Combines age_value + age_unit back into stored format on save
- Handles age parsing for normalization

---

### **TestResultForm & TestResultFormSet**
Dynamic inline formset for entering multiple test results.

**Fields per row:**
```python
test_name              # Text with autocomplete
result_value           # Free text (can be numeric, text, or range)
reference_range        # e.g., "4.5-11.0"
unit                   # e.g., "K/µL"
comment                # Optional notes
section_name           # Organizes results by section
display_order          # Sort order
DELETE                 # Checkbox to remove row
```

**Features:**
- Extra blank rows for new entries
- Autocomplete suggestions from TestCatalog
- Validates that non-blank rows have test names

---

## Security & Access Control

All views enforce authentication and staff-only access:

```python
@login_required          # Requires logged-in user
@staff_required          # Requires is_staff=True or is_superuser=True
```

**Decorator:** `staff_required = user_passes_test(lambda u: u.is_active and (u.is_staff or u.is_superuser))`

**No row-level restrictions:** Currently all staff can see/edit all reports. Consider adding per-clinic or per-user filtering for multi-location deployments.

---

## Database Workflow Example

### Creating a New Report

```
1. User visits /new/
   → report_create() renders LabReportForm + empty TestResultFormSet

2. User fills form:
   Profile: "Complete Blood Count"
   Patient: "John Doe", Age: 22YRS, Sex: M
   Sample Date: 2026-04-16
   
3. User enters test results in formset:
   WBC: 7.2, Ref: 4.5-11.0, Unit: K/µL
   RBC: 4.8, Ref: 4.5-5.9, Unit: M/µL
   
4. Form submitted POST to report_create/
   LabReportForm validates → OK
   TestResultFormSet validates → OK
   
5. handle_report_form() processes:
   a) Create LabReport record
      - Attendant = current user
      - Profile = CBC
      - Specimen = BLOOD (from profile default)
   
   b) For each result in formset:
      - get_or_create_test_definition("WBC", "K/µL")
        → TestCatalog entry created/retrieved
      - Create TestResult linked to report + test
      
   c) Auto-learn reference ranges:
      - Age category from "22YRS" → "adult"
      - ReferenceRangeDefault("adult", "WBC", "4.5-11.0", "K/µL")
        → Created if not exists

6. Redirect to report_detail/1/
   → Shows complete report with all results
   
7. User can:
   - Edit the report (/1/edit/)
   - Print it (/1/print/)
   - Mark as printed
   - Delete it (/1/delete/)
```

---

## Key Design Decisions

### 1. **TestProfile + TestProfileParameter Pattern**
- **Why:** Reduces redundancy for common test sets (CBC, UA, etc.)
- **Benefit:** Speed up data entry, ensure consistency
- **Alternative rejected:** Hard-coding specific test types

### 2. **Free-Text Test Entry + TestCatalog Learning**
- **Why:** Don't force users into dropdown; learn their terminology
- **Benefit:** Flexible, grows with usage, normalizes spellings
- **Alternative rejected:** Pre-populated fixed list (too rigid)

### 3. **ReferenceRangeDefault Learning System**
- **Why:** Reduce data entry by remembering what users enter
- **Benefit:** Context-aware (age-based), reduces typos
- **Alternative rejected:** Static reference tables

### 4. **Denormalized attendant_name Field**
- **Why:** Preserve lab tech name even if user account deleted
- **Benefit:** Report history remains intact
- **Note:** Could be removed if archival isn't important

### 5. **Section Grouping in Results**
- **Why:** Large tests (CBC) have 15+ parameters
- **Benefit:** Organized output, better readability
- **Alternative rejected:** Flat list

---

## Dependencies

```
Django==6.0.3           # Web framework
psycopg[binary]==3.2.12 # PostgreSQL adapter
gunicorn==23.0.0        # Production server
whitenoise==6.7.0       # Static file serving
sqlparse==0.5.5         # SQL formatting
asgiref==3.11.1         # ASGI utilities
tzdata==2025.3          # Timezone data
```

---

## Data Files

### **lumina-data.json** / **lumina-data.min.json**
Pre-loaded fixture data containing:
- Initial TestProfiles (CBC, Urinalysis, etc.)
- Common tests & reference ranges by age category
- Used for fresh database initialization

Load via:
```bash
python manage.py loaddata lumina-data.json
```

---

## Environment Configuration

### Database Setup
- **Development:** SQLite (db.sqlite3)
- **Production:** PostgreSQL (via DATABASE_URL env var)
- **Fallback:** Auto-switches to SQLite if DATABASE_URL not set or invalid

### Required Environment Variables (Production)
- `DATABASE_URL` - PostgreSQL connection string
- `DB_CONN_MAX_AGE` - Connection pooling (default: 60s)
- `SECRET_KEY` - Django secret (via settings)
- `DEBUG` - Set to False in production
- `ALLOWED_HOSTS` - Allowed domain names

---

## Extension Points for Future Development

1. **Multi-location Support**
   - Add Location FK to LabReport
   - Filter reports by user's assigned location
   - Clinic-specific templates

2. **Custom Reference Ranges per Location**
   - Location-specific ReferenceRangeDefault override

3. **Barcode/QR Integration**
   - Generate codes for sample tracking
   - Batch operations via scanning

4. **Report Versioning**
   - Track edits with historical snapshots
   - Approval workflow

5. **Export Formats**
   - PDF generation (ReportLab, Weasyprint)
   - HL7 messaging for EMR integration
   - CSV exports for analysis

6. **Dashboard Analytics**
   - Tests performed over time
   - Most common test combinations
   - Performance metrics

7. **Mobile App**
   - React Native or Flutter client
   - Offline capability with sync

---

## Common Tasks

### Add a New Test Profile
1. Django admin → TestProfile → Add
2. Fill name, code, description, specimen type
3. Save
4. Add → TestProfileParameter for each test
5. Link to existing TestCatalog tests or create new ones

### Batch Import Test Results
1. Parse CSV/Excel with columns: patient_name, age, sex, test_name, result_value, etc.
2. Create script to loop through rows and create LabReport + TestResult objects
3. Data learning system auto-populates new entries

### Export Reports to PDF
1. Add ReportLab or Weasyprint dependency
2. Create report_pdf() view that renders to PDF
3. Use print template as base

### Setup Production Deployment
1. Set DATABASE_URL to PostgreSQL connection string
2. Set SECRET_KEY in environment
3. Set DEBUG=False
4. Run `python manage.py collectstatic --noinput`
5. Deploy with gunicorn: `gunicorn labsystem.wsgi`

---

## Troubleshooting

### Reports not appearing after save
- Check LabReport.created_at timestamp
- Verify attendant assignment (should be current user)
- Check for form validation errors in console

### Reference ranges not auto-populating
- Check that ReferenceRangeDefault exists for test + age category
- Verify age is correctly parsed (22YRS vs 22 YRS)
- AJAX endpoint url might be incorrect

### Formset showing too many rows
- Default: 10 extra blank rows
- Change in forms.py: `extra` parameter in inlineformset_factory

### Template not applying defaults
- Check TestProfile.is_active = True
- Verify TestProfileParameter records exist
- Check TestCatalog records are linked

---

## File Map & Key Locations

| Purpose | File |
|---------|------|
| Data Models | [lab/models.py](lab/models.py) |
| View Logic | [lab/views.py](lab/views.py) |
| Forms | [lab/forms.py](lab/forms.py) |
| URL Routes | [lab/urls.py](lab/urls.py) |
| Admin Interface | [lab/admin.py](lab/admin.py) |
| Main Template | [templates/lab/base.html](templates/lab/base.html) |
| Report Form | [templates/lab/report_form.html](templates/lab/report_form.html) |
| Report Display | [templates/lab/report_detail.html](templates/lab/report_detail.html) |
| Print Layout | [templates/lab/report_print.html](templates/lab/report_print.html) |
| Report List/Dashboard | [templates/lab/report_list.html](templates/lab/report_list.html) |
| Startup Config | [labsystem/settings.py](labsystem/settings.py) |

---

**Document generated:** April 16, 2026  
**System version:** Django 6.0.3

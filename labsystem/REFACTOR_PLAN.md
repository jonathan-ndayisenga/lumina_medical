# System Refactor Document – Hospital EMR

**Version:** 1.0  
**Date:** April 19, 2026  
**Target Audience:** Development Team  
**Purpose:** Address UI/UX, role management, multi‑tenancy branding, and prepare for future modules.

---

## 1. Executive Summary

The current system (Lumina Lab Management System) is functionally complete but suffers from:
- Poor styling (inconsistent, non‑responsive, "dirty" UI).
- Role‑based navbar not fully isolated (users see irrelevant modules).
- Rigid role system – hospital admin cannot create custom roles or assign granular permissions.
- Missing hospital branding (logo) during creation and in reports.
- Superadmin hospital creation form lacks proper styling and logo upload.
- No self‑service password reset for hospital admin.
- Templates are not mobile/tablet responsive.

This refactor will modernise the frontend using **Tailwind CSS**, introduce a flexible **role‑permission system**, add hospital logo branding, and improve cross‑module interaction.

---

## 2. Identified Issues & Required Fixes

| ID | Issue | Severity | Module(s) Affected |
|----|-------|----------|--------------------|
| 1 | Superadmin hospital creation form not styled, missing logo upload | High | `admin_dashboard` |
| 2 | Dashboard and all templates look "dirty" (no consistent design system) | High | All templates |
| 3 | Navbar shows all modules to all users, no role‑based filtering | High | `base.html`, all views |
| 4 | Hospital admin cannot create custom roles or assign permissions beyond 6 fixed roles | High | `admin_dashboard`, `accounts` |
| 5 | Hospital logo not used; no branding on reports or navbar | Medium | `admin_dashboard`, `lab` reports |
| 6 | No self‑service password reset for hospital admin | Medium | `accounts` |
| 7 | Templates are not responsive (fail on tablet/mobile) | High | All templates |
| 8 | Inventory module is missing (preparatory work needed) | Low | `admin_dashboard` (future) |

---

## 3. Proposed Solutions

### 3.1 Styling & Responsiveness – Tailwind CSS Integration

**Action:** Replace all custom CSS with **Tailwind CSS**.
- Install Tailwind via CDN or build process (recommend CDN for speed).
- Redesign all templates (`base.html`, all module templates) using Tailwind utility classes.
- Ensure responsive breakpoints (`sm:`, `md:`, `lg:`, `xl:`) for all pages.
- Create a consistent colour scheme (primary, secondary, danger, success) using Tailwind's theming.

**Deliverable:** All templates use Tailwind; no inline or external custom CSS remains.

### 3.2 Role‑Based Navbar & Permissions System

**Current:** Fixed 6 roles; navbar visibility is hardcoded in `base.html`.

**New Design:**
- Introduce a **dynamic permission system** where each view/URL is assigned a permission codename (e.g., `can_view_lab_queue`, `can_manage_services`).
- Hospital admin can assign permissions to any user (not just roles).
- Navbar generates menu items based on the logged‑in user's permissions (queried from a new `UserPermission` model or Django's built‑in `Permission` model).
- Default roles (receptionist, lab_attendant, doctor, nurse, hospital_admin, superadmin) are pre‑loaded with a standard set of permissions, but hospital admin can override.

**Implementation steps:**
1. Create `Permission` model (or use Django's `Permission` with `content_type`).
2. Add `ManyToManyField` to `User` model: `permissions = models.ManyToManyField(Permission, blank=True)`.
3. Write middleware/template tag to build navbar from user's permissions.
4. Update all views to check permissions (decorator `@permission_required('app.permission_code')`).

### 3.3 Hospital Logo & Branding

**Superadmin workflow:**
- When creating a hospital, add a field `logo` (ImageField) – upload hospital logo.
- Store logo in `media/hospital_logos/<hospital_id>/logo.png`.

**Hospital admin dashboard:**
- Display logo in top navbar (left side) instead of default text.
- Use logo in printed lab reports (add to `report_print.html`).

**Software owner (superadmin) navbar:**
- Show "Hospital EMR" as brand text, not a logo.

### 3.4 Hospital Admin Self‑Service Password Reset

- Add a "Settings" link in hospital admin navbar.
- Create a password change form (using Django's built‑in `PasswordChangeForm`).
- Allow hospital admin to change password without requiring old password? No – require old password for security.

### 3.5 Inventory Module Preparation

Although not yet implemented, we must prepare the database and admin interface:
- Add `InventoryItem` model (already exists in `admin_dashboard/models.py` – verify).
- Ensure low‑stock alerts and consumption tracking are designed but not coded yet.
- Document the expected integration points (lab, doctor, nurse can consume items).

### 3.6 UI/UX Polishing

- Use card layouts, consistent spacing, rounded corners, subtle shadows.
- All forms should have proper labels, error messages, and loading states.
- Implement a sticky navbar with dropdown for user menu (profile, logout, settings).
- Ensure all pages have a `<title>` reflecting the current module.

---

## 4. Implementation Steps (Ordered)

### Phase 1: Foundation & Styling (Week 1)
1. Install Tailwind CSS (via CDN or npm build).
2. Refactor `base.html` – new navbar, footer, responsive grid.
3. Convert login page to Tailwind.
4. Convert all module dashboards (reception, doctor, nurse, lab, admin_dashboard) to Tailwind.
5. Test responsiveness on phone, tablet, laptop.

### Phase 2: Role & Permission System (Week 2)
6. Create `Permission` model (or use Django's built‑in permissions with `ContentType`).
7. Add `permissions` field to `User`.
8. Seed default permissions for each of the 6 roles.
9. Create hospital admin UI to assign permissions to users (checkboxes grouped by module).
10. Update navbar to build menu from user's permissions.
11. Update view decorators to enforce permissions.

### Phase 3: Hospital Logo & Branding (Week 3)
12. Add `logo` field to `Hospital` model; run migration.
13. Update superadmin hospital creation form to include logo upload.
14. Modify `base.html` to display hospital logo (if exists) else default text.
15. Modify lab report print template to include hospital logo.

### Phase 4: Password Reset & Settings (Week 3)
16. Add "Settings" link in hospital admin navbar.
17. Implement password change view and template.
18. Ensure old password verification.

### Phase 5: Inventory Preparation (Week 4)
19. Review existing `InventoryItem` model; add missing fields (e.g., `reorder_level`, `unit`).
20. Create basic CRUD for inventory in hospital admin (if not already).
21. Document consumption design (to be implemented later).

### Phase 6: Final Polish & Testing (Week 4)
22. Cross‑browser testing (Chrome, Firefox, Safari).
23. Mobile testing (iPhone, Android).
24. Role permission testing (ensure hospital admin can restrict access).
25. Logo upload and report generation test.

---

## 5. Technical Requirements

### 5.1 Dependencies
- Add `Pillow` (already required for image fields).
- Tailwind CSS – include via CDN or build process.

### 5.2 New Models

```python
# accounts/models.py
class Permission(models.Model):
    codename = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=255)
    module = models.CharField(max_length=50)  # e.g., 'reception', 'lab', 'doctor'

    def __str__(self):
        return self.name

# Add to User model
permissions = models.ManyToManyField(Permission, blank=True)
```

### 5.3 Default Permissions (Seed data)

| Module | Permission Code | Description |
|--------|----------------|-------------|
| reception | view_queue | View reception dashboard |
| reception | register_patient | Register new patient |
| reception | create_visit | Create patient visit |
| reception | process_payment | Record payment |
| lab | view_lab_queue | View lab queue |
| lab | enter_results | Enter test results |
| lab | print_report | Print lab report |
| doctor | view_doctor_queue | View doctor queue |
| doctor | write_consultation | Write consultation notes |
| nurse | view_nurse_queue | View nurse queue |
| nurse | record_vitals | Record patient vitals |
| admin | manage_users | Manage hospital users |
| admin | manage_services | Manage services & pricing |
| admin | manage_expenses | Record expenses |
| admin | manage_salaries | Manage salaries |
| admin | manage_inventory | Manage inventory |
| admin | view_financials | View financial reports |

### 5.4 Navbar Generation (Template Tag)

```python
# templatetags/navbar.py
@register.inclusion_tag('navbar.html')
def render_navbar(user):
    permissions = user.permissions.all()
    menu_items = []
    for perm in permissions:
        # Group by module
        ...
    return {'menu_items': menu_items}
```

---

## 6. Success Criteria

- [ ] All templates use Tailwind CSS and are responsive (tested on 3 devices).
- [ ] Superadmin can create hospital with logo and hospital admin credentials.
- [ ] Hospital admin can assign granular permissions to any user.
- [ ] Navbar shows only permitted modules for each user.
- [ ] Hospital logo appears in navbar and on printed lab reports.
- [ ] Hospital admin can change own password.
- [ ] Inventory model is ready for future consumption logic.
- [ ] No regression in existing functionality (lab reports, queues, payments, accounting).

---

## 7. Acceptance Testing Plan

1. **Superadmin flow** – Create hospital with logo → verify hospital admin login works → check logo appears.
2. **Permission assignment** – Create a user with only "view_queue" and "register_patient" → login → navbar shows only reception links.
3. **Responsive** – Open on 13" laptop, iPad, iPhone → all elements visible and usable.
4. **Lab report** – Print report with hospital logo.
5. **Password reset** – Hospital admin changes password, logs out, logs in with new password.

---

## 8. Appendix: Current vs. Target UI

| Element | Current | Target |
|---------|---------|--------|
| Form styling | Default Django with custom CSS (inconsistent) | Tailwind cards with proper spacing |
| Navbar | Hardcoded list of links | Dynamic based on permissions |
| Dashboard cards | Plain divs | Shadow, rounded corners, hover effects |
| Tables | No responsive scroll | Overflow‑x‑auto on mobile |
| Buttons | Inconsistent sizes | Uniform padding, rounded, hover state |

---

## 9. Developer Handoff Notes

- **Migration strategy:** Run `makemigrations` after adding `logo` field and `Permission` model. No data loss expected.
- **Static files:** Move all custom CSS to a separate file or remove entirely.
- **Testing:** Use `python manage.py runserver` and test each role separately.
- **Documentation:** Update README with new permission system.

This document is the **single source of truth** for the refactor. All changes must align with the steps outlined.

**Prepared for:** Development Team  
**Approved by:** Product Owner  
**Date:** April 19, 2026

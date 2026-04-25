from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect
from django.urls import reverse


class RoleAwareLoginView(LoginView):
    """Always land users on the correct module after login."""

    template_name = "registration/login.html"
    redirect_authenticated_user = True

    def get_success_url(self):
        user = self.request.user
        if user.is_superuser or getattr(user, "role", "") == "superadmin":
            return reverse("developer_dashboard")
        if getattr(user, "role", "") == "hospital_admin":
            return reverse("hospital_dashboard")

        # Multi-role support via groups (non-breaking: role-based still works).
        if user.groups.filter(name="Reception").exists():
            return reverse("reception_dashboard")
        if user.groups.filter(name="Doctor").exists():
            return reverse("doctor_queue")
        if user.groups.filter(name="Lab").exists():
            return reverse("lab_queue")
        if user.groups.filter(name="Nurse").exists():
            return reverse("nurse_queue")
        if getattr(user, "role", "") == "receptionist":
            return reverse("reception_dashboard")
        if getattr(user, "role", "") == "lab_attendant":
            return reverse("lab_queue")
        if getattr(user, "role", "") == "doctor":
            return reverse("doctor_queue")
        if getattr(user, "role", "") == "nurse":
            return reverse("nurse_queue")
        return reverse("report_list")


@login_required
def app_home(request):
    """Redirect authenticated users to their role-specific dashboard"""
    user = request.user
    
    # Superadmin/Software Owner → Developer Dashboard (Platform Owner)
    if user.is_superuser or getattr(user, "role", "") == "superadmin":
        return redirect("developer_dashboard")
    
    # Hospital Admin → Hospital Dashboard
    if getattr(user, "role", "") == "hospital_admin":
        return redirect("hospital_dashboard")

    # Multi-role support via groups (non-breaking: role-based still works).
    if user.groups.filter(name="Reception").exists():
        return redirect("reception_dashboard")
    if user.groups.filter(name="Doctor").exists():
        return redirect("doctor_queue")
    if user.groups.filter(name="Lab").exists():
        return redirect("lab_queue")
    if user.groups.filter(name="Nurse").exists():
        return redirect("nurse_queue")
    
    # Receptionist → Reception Dashboard
    if getattr(user, "role", "") == "receptionist":
        return redirect("reception_dashboard")
    
    # Lab Attendant → Lab Queue
    if getattr(user, "role", "") == "lab_attendant":
        return redirect("lab_queue")
    
    # Doctor → Doctor Queue
    if getattr(user, "role", "") == "doctor":
        return redirect("doctor_queue")
    
    # Nurse → Nurse Queue
    if getattr(user, "role", "") == "nurse":
        return redirect("nurse_queue")
    
    # Default → Lab Reports
    return redirect("report_list")

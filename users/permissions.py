from django.contrib.auth.models import Group
from rest_framework import permissions


class IsOwner(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        if hasattr(obj, 'username'):
            return obj.username == request.user.username
        if hasattr(obj,'owner'):
            return obj.owner.username == request.user.username

class IsMyInstitution(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        return len(obj.employee.filter(username=request.user.username))>0

class IsMyInstitutionsDienstModell(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        return request.user.institution_link_id == obj.institution_link_id


class IsPlaner(permissions.BasePermission):
    def has_permission(self, request, view):
        g = Group.objects.get(name='Planer')
        u = request.user
        return g in u.groups.all()


class IsVerwaltung(permissions.BasePermission):
    def has_permission(self, request, view):
        g = Group.objects.get(name='Verwaltung')
        u = request.user
        return g in u.groups.all()

class IsVerwaltungAndUserNotEmailValidated(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        g = Group.objects.get(name='Verwaltung')
        u = request.user
        if g in u.groups.all():
            if request.user.institution_link_id == obj.institution_link_id:
                return ~obj.is_emailvalidated
        return False

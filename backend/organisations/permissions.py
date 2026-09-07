from rest_framework import permissions

# creating the permissions layer
class HasRole(permissions.BasePermission):

    def has_permission(self, request, view):

        if not request.user or not request.user.is_authenticated:
            return False


        if request.user.is_superuser:
            return True

        allowed_roles = getattr(view, 'allowed_role', [])

        return request.user.groups.filter(name_in=allowed_roles).exist()